"""The AWS file provider for Dreamer."""

import configparser
import datetime
import json
import logging
import os
import os.path
import re
import tempfile
import traceback

from pathlib import Path
from shutil import rmtree
from types import TracebackType
from typing import Any, Dict, List, Optional, Set, Tuple, Type

from dreamer.exceptions import DreamerException, ProgrammerError

from .base import AbstractFileProvider
from .utils import _get, whoami


class AWSFileProvider(AbstractFileProvider):
    """
    Store the state remotely in AWS. Works by using a temporary directory, and tries to handle a lot of edge cases,
    status changes, cache invalidation and other trickery.
    """

    def __init__(self, base_dir: str, default_module: Optional[str] = None,
                 default_project: Optional[str] = None) -> None:
        super().__init__(base_dir, default_module, default_project)
        self.logger = logging.getLogger('AWSFileProvider')

        # pylint: disable=import-outside-toplevel
        try:
            import boto3
            from boto3.s3.transfer import S3Transfer
            from botocore.exceptions import ClientError
        except ImportError as e:
            raise ImportError('Please install boto3 to use the remote state.') from e

        if not base_dir.startswith('s3://'):
            raise ProgrammerError('Invalid base directory given for AWSFileProvider')

        self.base_dir = base_dir
        self.default_module = default_module
        self.default_project = default_project

        # Terminology for reference:
        # S3 does not have directories or filenames, it has *keys* (which might contain forward slashes)
        # local_dir is the temporary directory used to cache the files locally
        # Variables with `path` in them are `pathlib.Path` objects
        # fn is usually just the basename of the file we're handling

        # Parse the base directory to the S3 bucket name and an optional key prefix
        base_dir = base_dir[5:]  # strip leading "s3://"
        if base_dir.endswith('/'):
            base_dir = base_dir.rstrip('/')
        if '/' in base_dir:
            self.bucket, self.prefix = base_dir.split('/', 1)
            if not self.prefix.endswith('/'):
                self.prefix += '/'
        else:
            self.bucket = base_dir
            self.prefix = ''

        self.client = boto3.client('s3')
        self.transfer = S3Transfer(self.client)
        self.local_dir = Path(tempfile.mkdtemp(prefix='dreamer-'))

        self.dirty: Set[Path] = set()
        self._bucket_contents: Optional[List[str]] = None
        self.last_version: Optional[int] = None

        # Used to signify that `sync()` should not raise an error on version mismatch and should not try to write a new
        # metadata file to the S3 bucket.
        self._deleted_project = False

        # Delayed imports make life harder
        self._ClientError = ClientError  # pylint: disable=invalid-name

    # Helper methods

    def _get_project_and_module(self, project: Optional[str] = None, module: Optional[str] = None) -> Tuple[str, str]:
        """
        Get the module and project. If None is given, the value is fetched from the corresponding default. If the
        default is None as well, an exception is raised.
        """
        project = _get(project, self.default_project)
        module = _get(module, self.default_module)
        return project, module

    def _ensure_local_path(self, project: Optional[str] = None, module: Optional[str] = None) -> Path:
        """Ensure that the local path for the given project and module exists."""
        project, module = self._get_project_and_module(project, module)
        base_path = self.local_dir / module / project
        base_path.mkdir(mode=0o700, parents=True, exist_ok=True)
        return base_path

    def _get_s3_key(self, fn: str, project: Optional[str] = None, module: Optional[str] = None) -> str:
        """Return the S3 object key for the given file in the given project and module."""
        project, module = self._get_project_and_module(project, module)
        return self.prefix + '/'.join((module, project, fn))

    def _get_local_path(self, fn: str, project: Optional[str] = None, module: Optional[str] = None) -> Path:
        """Return the local path for the given file in the given project and module."""
        project, module = self._get_project_and_module(project, module)
        return self.local_dir / module / project / fn

    # Versioning methods

    def get_metadata(self, module: str, project: str) -> Dict[str, Any]:
        """Get the full metadata dictionary of a project."""
        key = self.prefix + module + '/' + project
        base_path = self._ensure_local_path(project, module)
        destination = str(base_path / 'meta.json')
        self.transfer.download_file(self.bucket, key, destination)
        with open(destination, 'rb') as f:
            return json.load(f)

    def get_remote_version(self) -> Optional[int]:
        """Get the version of the remote state, or None if there is no remote state yet."""
        if self.default_module is None or self.default_project is None:
            raise DreamerException('get_remote_version() called without default_module and default_project set')
        try:
            return self.get_metadata(self.default_module, self.default_project)['version']
        except self._ClientError:
            return None

    def open_project(self, module: str, project: str) -> None:
        """
        Open the project, setting the default module and project and storing the remote version to prevent
        accidentally overwriting the remote state if it is edited by another client during the local changes.
        """
        self.default_module = module
        self.default_project = project
        self.last_version = self.get_remote_version()

    def close_project(self) -> None:
        self.sync_project()
        self.default_module = None
        self.default_project = None
        self.last_version = None

    def sync_project(self) -> int:
        """
        Sync the project, fetching the remote version again to detect overlapping edits to the state, and incrementing
        the remote state version by one. If there was no remote state present before this run, the version is 0.
        """
        if self.default_module is None or self.default_project is None:
            raise DreamerException('sync_project() called without default_module and default_project set')
        key = self.prefix + '/'.join((self.default_module, self.default_project))
        base_path = self._ensure_local_path(self.default_project, self.default_module)
        fn = base_path / 'meta.json'
        if self.last_version is None:
            new_version = 0
        else:
            new_version = self.last_version + 1
        json.dump({'version': new_version, 'author': whoami()}, fn.open('w'))
        self.transfer.upload_file(
            str(fn), self.bucket, key,
            extra_args={'Metadata': {'Content-Type': 'application/json'}}
        )
        return new_version

    # FileProvider method overrides

    def get(self, fn: str, *, project: Optional[str] = None, module: Optional[str] = None) -> Path:
        """
        Fetch the file to the local state for reading. Raise an exception if the file is missing in the S3 bucket.
        Returns the path to the local copy of the file.
        """
        base_path = self._ensure_local_path(project, module)
        file_path = base_path / fn
        if not file_path.exists():
            key = self._get_s3_key(fn, project, module)
            try:
                self.transfer.download_file(self.bucket, key, str(file_path))
            except Exception as e:
                raise FileNotFoundError(f's3://{self.bucket}/{key}') from e
        return file_path

    def get_rw(self, fn: str, *, project: Optional[str] = None, module: Optional[str] = None) -> Path:
        """
        Fetch the file to the local state for reading and writing. If the file is missing in the S3 bucket, an empty
        file is initially created to prevent file not found errors etc.
        """
        base_path = self._ensure_local_path(project, module)
        file_path = base_path / fn
        self.dirty = self.dirty | {file_path}
        try:
            self.get(fn, project=project, module=module)
        except FileNotFoundError:
            file_path.touch()
        return file_path

    @property
    def bucket_contents(self) -> List[str]:
        """Return all files in the S3 bucket matching the current prefix."""
        if self._bucket_contents is None:
            self._bucket_contents = []
            paginator = self.client.get_paginator('list_objects')
            results = paginator.paginate(
                Bucket=self.bucket,
                PaginationConfig={'PageSize': 1000},
                Prefix=self.prefix
            )
            for page in results:
                for item in page.get("Contents", []):
                    self._bucket_contents.append(item["Key"][len(self.prefix):])
        return self._bucket_contents

    def get_modules(self) -> List[str]:
        """Return all modules present in the S3 bucket."""
        # Kludge? Just show all files that have a project inside them.
        # This might fail if the bucket has other content, but at this point it is not recommended in any case.
        out = []
        for key in self.bucket_contents:
            parts = key.split('/')
            if len(parts) == 2:
                out.append(parts[0])
        return sorted(list(set(out)))

    def get_projects(self, module: Optional[str] = None) -> List[str]:
        """Return all projects present in the S3 bucket in the given module."""
        module = _get(module, self.default_module)
        out = []
        for key in self.bucket_contents:
            if re.match(rf'^{module}/[^/]+$', key):
                out.append(key.split('/', 1)[1])
        return out

    def get_files(self, project: Optional[str] = None, module: Optional[str] = None) -> List[str]:
        """Return all files in the S3 bucket belonging to the given project in the given module."""
        project, module = self._get_project_and_module(project, module)
        out = []
        for key in self.bucket_contents:
            if key.startswith(f'{module}/{project}/'):
                out.append(key.split('/', 2)[2])
        return out

    def delete(self, fn: str, *, project: Optional[str] = None, module: Optional[str] = None) -> None:
        project, module = self._get_project_and_module(project, module)
        self.client.delete_object(Bucket=self.bucket, Key=self._get_s3_key(fn, project, module))

    def delete_project(self, project: str, module: Optional[str] = None, recursive: bool = False) -> None:
        project, module = self._get_project_and_module(project, module)
        if project == self.default_project and module == self.default_module:
            self._deleted_project = True
        if recursive:
            for fn in self.get_files(project, module):
                self.delete(fn, project=project, module=module)
        self.client.delete_object(Bucket=self.bucket, Key='/'.join((self.prefix + module, project)))

    def module_exists(self, module: str) -> bool:
        # Directories don't exist in S3, so modules "always exist"
        return True

    def project_exists(self, project: str, module: Optional[str] = None) -> bool:
        project, module = self._get_project_and_module(project, module)
        return f'{module}/{project}' in self.bucket_contents

    def sync(self) -> bool:
        if self._deleted_project or not self.dirty:
            # If we're deleting the project or there's no changes, don't sync anything
            return True

        current_version = self.get_remote_version()
        if current_version != self.last_version:
            raise DreamerException('Critical: Remote state has been modified during our runtime!')

        for path in self.dirty:
            key = self.prefix + str(path.relative_to(self.local_dir))
            self.transfer.upload_file(str(path), self.bucket, str(key), extra_args={'Metadata': {'author': whoami()}})
        if self.default_module and self.default_project:
            self.last_version = self.sync_project()
        return True

    def get_default_local_path(self) -> Path:
        if self.default_module is None or self.default_project is None:
            raise DreamerException('get_default_local_path() called without default_module and default_project set')
        return self.local_dir / self.default_module / self.default_project

    def get_base_url(self) -> str:
        return f's3://{self.bucket}/{self.prefix}'

    def url_for(self, fn: str, *, project: Optional[str] = None, module: Optional[str] = None) -> str:
        return f's3://{self.bucket}/{self._get_s3_key(fn, project, module)}'

    def url_for_project(self, project: str, module: str) -> str:
        return f's3://{self.bucket}/{self.prefix}{module}/{project}/'

    def __enter__(self) -> None:
        # Nothing special to do here
        pass

    def __exit__(self, exc_type: Optional[Type[BaseException]], exc_val: Optional[Exception],
                 exc_tb: Optional[TracebackType]) -> bool:
        """Sync the remote state, and (only) on a clean exit remove the temporary directory."""
        local_exc = None
        try:
            self.sync()
        except Exception as exc:  # pylint: disable=broad-except
            local_exc = exc

        if exc_type is None:
            rmtree(self.local_dir)
        if exc_type is not None or local_exc is not None:
            self.logger.error('An exception happened, so your local state has NOT been removed from %s', self.local_dir)
            self.logger.error('You might be able to use the local state to manually solve the issue or clean up.')
            if local_exc is not None:
                raise local_exc  # pylint: disable=raising-bad-type
        return False

    def troubleshoot(self) -> None:
        # pylint: disable=import-outside-toplevel,broad-except,too-many-locals,too-many-branches,too-many-statements
        import boto3.session
        import botocore.exceptions

        print(f'Current $AWS_PROFILE: {os.environ.get("AWS_PROFILE", "<not found>")}')
        print('Testing STS GetCallerIdentity...')
        session = boto3.session.Session()
        client = session.client('sts')
        try:
            response = client.get_caller_identity()
        except botocore.exceptions.NoCredentialsError as e:
            print(f'No credentials found while running STS GetCallerIdentity: {str(e)}')
        except Exception:
            print('Unexpected exception while running STS GetCallerIdentity:')
            traceback.print_exc()
        else:
            print('GetCallerIdentity OK, credentials likely valid:')
            print(f'* AWS account: {response["Account"]}')
            print(f'* AWS user ID: {response["UserId"]}')
            print(f'* AWS ARN: {response["Arn"]}')

        print('\nParsing ~/.aws/credentials...')
        parser = configparser.ConfigParser()
        parser.read(os.path.expanduser('~/.aws/credentials'))

        current_profile = session.profile_name
        current_expiration = None

        if current_profile not in parser.sections():
            print(f'Current profile "{current_profile}" not found in ~/.aws/credentials')
        else:
            data = parser[current_profile]
            if 'aws_credentials_expire_unix' in data:
                current_expiration = datetime.datetime.fromtimestamp(int(data['aws_credentials_expire_unix']))
                print(f'Current credentials expiration date: {current_expiration.isoformat()}')
            else:
                print('Current credentials do not expire.')

        print('Available credentials:')
        longest_valid_credential = None
        longest_valid_expiration = None
        permanent_credentials = []
        for section_name in parser.sections():
            if section_name == current_profile:
                prefix = 'current, '
            else:
                prefix = ''
            expiration_text = 'permanent'
            data = parser[section_name]
            if 'aws_credentials_expire_unix' in data:
                expiration = datetime.datetime.fromtimestamp(int(data['aws_credentials_expire_unix']))
                if expiration < datetime.datetime.now():
                    expiration_text = f'temporary, EXPIRED {expiration.isoformat()}'
                else:
                    expiration_text = f'temporary, expires {expiration.isoformat()}'
                if longest_valid_expiration is None or expiration > longest_valid_expiration:
                    longest_valid_expiration = expiration
                    longest_valid_credential = section_name
            else:
                permanent_credentials.append(section_name)

            print(f'* {section_name} ({prefix}{expiration_text})')

        print()
        if longest_valid_credential is not None:
            if longest_valid_expiration < datetime.datetime.now():
                if permanent_credentials:
                    if current_profile in permanent_credentials:
                        print('You are using permanent credentials (and all your temporary credentials ' +
                              'have expired).')
                    else:
                        profiles = ', '.join(permanent_credentials)
                        print('You are using expired temporary credentials, but permanent credentials are ' +
                              'available!')
                        print(f'Did you mean to use one of the following AWS profiles: {profiles}')
                else:
                    print('All available temporary credentials have expired, including the active ' +
                          'profile.')

            elif longest_valid_credential != current_profile:
                if current_profile in permanent_credentials:
                    print('You are using permanent credentials, but there are also valid temporary ' +
                          'credentials available.')
                else:
                    if current_expiration < datetime.datetime.now():
                        print('You are using expired credentials, but valid temporary credentials are ' +
                              'available. Did you mean to use the AWS profile "{longest_valid_credential}"?')
                    else:
                        print('You are using valid temporary credentials. The longest-lived temporary '
                              'credentials you have are in the AWS profile {longest_valid_credential}.')

            else:
                print('You are using the longest-lived available temporary credentials, which are ' +
                      'valid.')
                if permanent_credentials:
                    profiles = ', '.join(permanent_credentials)
                    print('You also have permanent credentials available in the following AWS profiles: ' +
                          profiles)

        else:
            print('You only have permanent credentials available.')

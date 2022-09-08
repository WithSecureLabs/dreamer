"""The local filesystem provider for Dreamer."""

import stat

from pathlib import Path
from shutil import rmtree
from types import TracebackType
from typing import List, Optional, Tuple, Type

from ..exceptions import DreamerException
from .base import AbstractFileProvider
from .utils import _get


class LocalFileProvider(AbstractFileProvider):
    """A file provider for storing the Dreamer state in the local filesystem."""

    def __init__(self, base_dir: str, default_module: Optional[str] = None,
                 default_project: Optional[str] = None) -> None:
        super().__init__(base_dir, default_module, default_project)
        self.base_dir = Path(base_dir)
        self.default_module: Optional[str] = default_module
        self.default_project: Optional[str] = default_project

    def _get_project_and_module(self, project: Optional[str] = None, module: Optional[str] = None) -> Tuple[str, str]:
        """
        Get the module and project according to the given parameters. If a parameter is None, the default one will be
        used. If the default one is None as well, an exception is raised.
        """
        project = _get(project, self.default_project)
        module = _get(module, self.default_module)
        return project, module

    def get_path(self, fn: str, project: str, module: str) -> Path:
        """Get the path for the given file in the given project in the given module."""
        return self.base_dir / module / project / fn

    def get(self, fn: str, *, project: Optional[str] = None, module: Optional[str] = None) -> Path:
        project, module = self._get_project_and_module(project, module)
        path = self.get_path(fn, project, module)
        if not path.exists():
            raise FileNotFoundError(path)
        return path

    def get_rw(self, fn: str, *, project: Optional[str] = None, module: Optional[str] = None) -> Path:
        project, module = self._get_project_and_module(project, module)
        # `get_rw()` is the only place that should ever start actually writing the state, so this is the logical place
        # to ensure that the base directory exists.
        if not self.base_dir.exists():
            self.base_dir.mkdir()
        module_dir = self.base_dir / module
        if not module_dir.exists():
            module_dir.mkdir()
        project_dir = module_dir / project
        if not project_dir.exists():
            project_dir.mkdir()
        # Finally: `get_rw()` should transparently create an empty file if it is not present on the filesystem
        path = self.get_path(fn, project, module)
        if not path.exists():
            path.touch()
        return path

    def get_modules(self) -> List[str]:
        return [str(node.name) for node in self.base_dir.glob('*') if node.is_dir()]

    def get_projects(self, module: Optional[str] = None) -> List[str]:
        module = _get(module, self.default_module)
        return [str(node.name) for node in (self.base_dir / module).glob('*') if node.is_dir()]

    def get_files(self, project: Optional[str] = None, module: Optional[str] = None) -> List[str]:
        project, module = self._get_project_and_module(project, module)
        return sorted(str(node.name) for node in (self.base_dir / module / project).glob('*'))

    def delete(self, fn: str, *, project: Optional[str] = None, module: Optional[str] = None) -> None:
        project, module = self._get_project_and_module(project, module)
        return (self.base_dir / module / project / fn).unlink()

    def delete_project(self, project: str, module: Optional[str] = None, recursive: bool = False) -> None:
        project, module = self._get_project_and_module(project, module)
        if recursive:
            rmtree(self.base_dir / module / project)
        else:
            (self.base_dir / module / project).rmdir()

    def module_exists(self, module: str) -> bool:
        return (self.base_dir / module).is_dir()

    def project_exists(self, project: str, module: Optional[str] = None) -> bool:
        module = _get(module, self.default_module)
        return (self.base_dir / module / project).is_dir()

    def sync(self) -> bool:
        # no-op
        return True

    def get_default_local_path(self) -> Path:
        if self.default_module is None or self.default_project is None:
            raise DreamerException('get_default_local_path() called without default_module and default_project')
        return self.base_dir / self.default_module / self.default_project

    def get_base_url(self) -> str:
        return str(self.base_dir)

    def url_for(self, fn: str, *, project: Optional[str] = None, module: Optional[str] = None) -> str:
        module, project = self._get_project_and_module(project, module)
        return str(self.get_path(fn, project, module))

    def url_for_project(self, project: str, module: str) -> str:
        return str(self.base_dir / module / project)

    def open_project(self, module: str, project: str) -> None:
        self.default_module = module
        self.default_project = project

    def close_project(self) -> None:
        pass

    def __enter__(self) -> None:
        # no-op: all local file operations are done immediately
        pass

    def __exit__(self, exc_type: Optional[Type[BaseException]], exc_val: Optional[Exception],
                 exc_tb: Optional[TracebackType]) -> bool:
        # no-op: all local file operations are done immediately
        pass

    def troubleshoot(self) -> None:
        result = self.base_dir.stat()

        try:
            owner = self.base_dir.owner()
        except KeyError:
            owner = '<UNKNOWN USER>'

        print(f'Using local base directory {self.base_dir}')
        print(f'Base directory mode: {oct(stat.S_IMODE(result.st_mode))}')
        print(f'Base directory owned by uid {result.st_uid} ({owner})')

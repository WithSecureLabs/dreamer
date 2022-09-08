#!/usr/bin/env python3

"""
This module contains Dreamer pseudo-modules, which are not directly related to building infrastructure, but use them
by e.g. listing them for the user.
"""

import argparse
import os.path
import subprocess
import sys

from pathlib import Path
from typing import Any, List, Optional

from .providers.base import AbstractFileProvider
from .runconfig import RunConfig
from .utils import git_version

# Python package version check
if sys.version_info < (3, 8, 0):
    try:
        from pkg_resources import get_distribution
        get_version = lambda package: get_distribution(package).version
    except ImportError:
        get_version = None  # pylint: disable=invalid-name
else:
    from importlib.metadata import version as get_version


def is_editable(package: str) -> bool:
    """Is the given package installed as editable, that is, with `pip install -e`?"""
    for path in sys.path:
        egg_link = os.path.join(path, f'{package}.egg-link')
        if os.path.isfile(egg_link):
            return True
    return False


class PseudoModule:
    """
    A base class for internal "pseudo-modules" that run tasks not directly related to building infrastructure, such as
    listing projects etc.
    """
    friendly_name = 'internal_module'
    steps = {
        'run': 'run'
    }
    default_steps = ['run']
    steps_requiring_parents: List[str] = []
    parser: Optional[argparse.ArgumentParser] = None
    """
    NB: Always use the `default` parameter of the argument parser in a pseudo-module ArgumentParser to avoid unnecessary
    `hasattr` calls and/or other trickery.
    """

    def __init__(self, name: str, file_provider: AbstractFileProvider, tf_config: RunConfig, ansible_config: RunConfig,
                 additional_args: Optional[List[str]] = None) -> None:
        if self.parser and additional_args:
            args = self.parser.parse_args(additional_args)
            for key, value in vars(args).items():
                setattr(self, key, value)
        self.provider = file_provider
        self.name = name
        self.tf_config = tf_config
        self.ansible_config = ansible_config

    def load_parents(self, parents: dict) -> None:
        """Dummy method for signature compatibility."""

    def run(self) -> None:
        """Run the module."""
        raise NotImplementedError


class ListParser(argparse.ArgumentParser):
    """An argument parser for ListPseudoModule."""
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.add_argument('-m', '--module', action='store', default=None, help='show only projects in the given module')
        self.add_argument('-V', '--verbose', action='store_true', help='also show all files belonging to projects')


class ListPseudoModule(PseudoModule):
    """A pseudo-module for the `list` command, which lists modules, projects, and optionally files."""
    friendly_name = 'list'
    parser = ListParser('init.py [...] list', add_help=False)
    module: Optional[str] = None
    verbose: bool = False

    def run(self) -> None:
        # pylint: disable=E1101
        print(f'Base directory: {self.provider.get_base_url()}')
        modules = self.provider.get_modules()
        if not modules:
            print('  (no modules)')
            return
        for module in modules:
            if self.module and module != self.module:
                continue
            print(f'Module: {module}')
            projects = self.provider.get_projects(module)
            if not projects:
                print('    (no projects)')
                continue
            for project in projects:
                print(f'  - {project}')
                if self.verbose:
                    for file_ in self.provider.get_files(project, module):
                        print(f'    - {file_}')


class TroubleshootModule(PseudoModule):
    """A pseudo-module for the `troubleshoot` command, which tries to identify common issues."""
    friendly_name = 'troubleshoot'

    def run(self) -> None:
        # Dreamer version check
        if get_version is not None:
            print(f'Dreamer version: {get_version("dreamer")}')
            if is_editable('dreamer'):
                print('Note: Dreamer seems to be installed in editable mode, above version might be incorrect!')
                print('      Finding git commit information...')
                base_path = Path(__file__).parent.parent
                try:
                    print(f'Dreamer git status: {git_version(base_path)}')
                except subprocess.CalledProcessError:
                    pass
        else:
            print('Unable to get Dreamer version; running on Python <3.8 without setuptools installed.')
        print()

        # Terraform version check: "Terraform v0.12.21"
        result = subprocess.run(('terraform', 'version'), capture_output=True, check=False)
        tf_version = result.stdout.splitlines()[0].decode()
        major, minor = (int(x) for x in tf_version.split()[1][1:].split(".", 2)[:2])
        if major < 1 and minor < 15:
            print(f'You are running an old version of Terraform, please upgrade to 1.0 or 0.15+ (found: {tf_version})')
        else:
            print(f'Terraform up to date: {tf_version}')

        # Ansible version check: "ansible-playbook 2.9.6"
        result = subprocess.run(('ansible-playbook', '--version'), capture_output=True, check=False)
        ansible_version_raw = result.stdout.splitlines()[0].decode()
        ansible_version = tuple(int(x) for x in ansible_version_raw.split()[1].split('.'))
        if ansible_version < (2, 8, 0):
            print(
                'You are running an outdated version of Ansible, which is not supported by Dreamer (found: %s)',
                ansible_version_raw
            )
        else:
            print(f'Ansible up to date: {ansible_version_raw}')

        print(f'\nDreamer base directory: {self.provider.get_base_url()}')
        print(f'Provider type: {self.provider.__class__.__name__}\n')
        self.provider.troubleshoot()

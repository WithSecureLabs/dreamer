"""Contains the base class for file providers."""

from abc import ABCMeta, abstractmethod
from pathlib import Path
from types import TracebackType
from typing import List, Optional, Type


class AbstractFileProvider:
    """
    A base class for file providers, allowing the Dreamer state to be placed in somewhere else than the local
    filesystem, such as an S3 bucket.

    The arguments are "backwards" for methods to better facilitate storing default project and module to make the
    subclassing of `dreamer.Module` a bit easier.
    """

    __metaclass__ = ABCMeta

    def __init__(self, base_dir: str, default_module: Optional[str] = None,
                 default_project: Optional[str] = None) -> None:
        pass

    @abstractmethod
    def open_project(self, module: str, project: str) -> None:
        """
        Open a project, possibly setting the lock for a shared state etc., and set the default module and project.
        """

    @abstractmethod
    def close_project(self) -> None:
        """Close the project, releasing any locks etc."""

    @abstractmethod
    def get(self, fn: str, *, project: Optional[str] = None, module: Optional[str] = None) -> Path:
        """Get a file for read-only access. Throws FileNotFoundError if the file is not found."""

    @abstractmethod
    def get_rw(self, fn: str, *, project: Optional[str] = None, module: Optional[str] = None) -> Path:
        """Get a file for read and write access. Missing files are simply created from scratch."""

    @abstractmethod
    def get_modules(self) -> List[str]:
        """List all modules in the store."""

    @abstractmethod
    def get_projects(self, module: str) -> List[str]:
        """List all projects for the given module."""

    @abstractmethod
    def get_files(self, project: str, module: str) -> List[str]:
        """List all files belonging to a given project in the given module."""

    @abstractmethod
    def delete(self, fn: str, *, project: str, module: str) -> None:
        """Delete the given file."""

    @abstractmethod
    def delete_project(self, project: str, module: str, recursive: bool = False) -> None:
        """
        Delete the given project from the given module. If `recursive` is True, remove all contained files as well.
        """

    @abstractmethod
    def module_exists(self, module: str) -> bool:
        """Return whether or not the given module exists."""

    @abstractmethod
    def project_exists(self, project: str, module: Optional[str] = None) -> bool:
        """Return whether or not the given project exists in the given module."""

    @abstractmethod
    def sync(self) -> bool:
        """Synchronize all changes."""

    @abstractmethod
    def get_default_local_path(self) -> Path:
        """Return a Path object for the local base path based on the current default module and project."""

    @abstractmethod
    def get_base_url(self) -> str:
        """Return a human-readable path or URL of the current base directory."""

    @abstractmethod
    def url_for(self, fn: str, *, project: Optional[str] = None, module: Optional[str] = None) -> str:
        """Return a human-readable path or URL for the given file"""

    @abstractmethod
    def url_for_project(self, project: str, module: str) -> str:
        """Return a human-readable path or URL for the given project"""

    @abstractmethod
    def __enter__(self) -> None:
        """Allow usage of the file provider as a context manager."""

    @abstractmethod
    def __exit__(self, exc_type: Optional[Type[BaseException]], exc_val: Optional[Exception],
                 exc_tb: Optional[TracebackType]) -> bool:
        """Sync the file provider state, passing all exceptions through."""

    @abstractmethod
    def troubleshoot(self) -> None:
        """Troubleshoot the provider, trying to identify common problems."""

"""Miscellaneous utilities for Dreamer file providers."""

from getpass import getuser
from socket import gethostname
from typing import Optional, TypeVar

T = TypeVar('T')

def _get(*args: Optional[T]) -> T:
    """Return the first non-None argument. If none are found, raise an exception."""
    for arg in args:
        if arg is not None:
            return arg
    raise ValueError('no argument or default given')


def whoami() -> str:
    """Return a `user@host` string for the current username and hostname."""
    return f'{getuser()}@{gethostname()}'

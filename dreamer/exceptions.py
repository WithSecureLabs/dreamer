"""Custom exceptions that can be raised by Dreamer."""

class DreamerException(Exception):
    """A generic catch-all exception type for Dreamer."""

class ProgrammerError(Exception):
    """An exception type for Dreamer signifying that the module developer has made a mistake, not the user."""

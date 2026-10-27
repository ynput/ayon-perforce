"""Exceptions raised by the Perforce backend API."""
from __future__ import annotations


class P4BaseError(Exception):
    """Base class for all Perforce backend errors."""


# Backwards compatible alias of the original public name.
P4BaseException = P4BaseError


class P4AttributeError(P4BaseError):
    """Raised when a Perforce attribute cannot be read or set."""


class P4PathDoesNotExistError(P4BaseError):
    """Raised when a path does not exist in Perforce."""


class P4ServerConnectionError(P4BaseError):
    """Raised when the connection to the Perforce server fails."""


class P4ExclusiveCheckoutError(P4BaseError):
    """Raised when files are exclusively checked out by someone else."""

    def __init__(self, files: list[str]) -> None:
        """Initialize the error with the exclusively checked out files.

        Args:
            files: Paths of the exclusively checked out files.

        """
        files_str = "\n - ".join(files)
        super().__init__(
            "The following files are exclusively checked out:"
            f"\n - {files_str}"
        )


class P4UnsafeOfflineCommandError(P4BaseError):
    """Raised when an action that requires a server runs offline."""

    def __init__(self, action: str) -> None:
        """Initialize the error with the offending action.

        Args:
            action: Name of the action that is not safe offline.

        """
        super().__init__(f"This action is not safe to run offline: {action}")


class P4Exceptions:
    """Namespace exposing the Perforce exception classes."""

    P4BaseException = P4BaseError
    P4AttributeError = P4AttributeError
    P4PathDoesNotExistError = P4PathDoesNotExistError
    P4ServerConnectionError = P4ServerConnectionError

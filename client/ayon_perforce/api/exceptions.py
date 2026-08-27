"""Pyforce Exceptions."""

from __future__ import annotations

__all__ = [
    "AuthenticationError",
    "ChangeUnknownError",
    "ClientNotFoundError",
    "CommandExecutionError",
    "ConnectionExpiredError",
    "P4Error",
    "UserNotFoundError",
]


class P4Error(Exception):
    """Base exception for the `pyforce` package."""


class UserNotFoundError(P4Error):
    """Raised when a user is requested but doesn't exist."""


class ChangeUnknownError(P4Error):
    """Raised when a changelist is requested but doesn't exist."""


class ClientNotFoundError(P4Error):
    """Raised when a client workspace is requested but doesn't exist."""


class ConnectionExpiredError(P4Error):
    """Raised when the connection to the Helix Core Server has expired.

    You need to log back in.
    """


class AuthenticationError(P4Error):
    """Raised when login to the Helix Core Server failed."""


class CommandExecutionError(P4Error):
    """Raised when an error occured during the execution of a `p4` command.

    Args:
        message: Error message.
        command: The executed command.
        data: Optional marshalled output returned by the command.
    """

    def __init__(
        self,
        message: str,
        command: list[str],
        data: dict[str, str] | None = None,
    ) -> None:
        """Initialize a CommandExecutionError.

        Args:
            message: Error message.
            command: The executed command.
            data: Optional marshaled output returned by the command.

        """
        self.command = command
        self.data = data or {}
        super().__init__(message)

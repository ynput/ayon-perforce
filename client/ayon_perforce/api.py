"""Abstraction for the Perforce CLI."""

import subprocess
from os import environ
from dataclasses import dataclass


@dataclass
class P4Stream:
    """An abstraction representing a Perforce stream."""

    name: str
    parent: str

    def post_init(self) -> None:
        """Post-init method."""

    def parse_spec(self) -> None:
        """Parse the stream specification."""


@dataclass
class P4Workspace:
    """An abstraction representing a Perforce workspace."""

    name: str
    user: str
    stream: P4Stream

    def post_init(self) -> None:
        """Post-init method."""

    def parse_spec(self) -> None:
        """Parse the workspace specification."""

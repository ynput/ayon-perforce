"""CLI backand for Perforce operations.

This is using local p4v client to perform operations.

"""
from __future__ import annotations

import logging
import pathlib
import subprocess
from typing import List, Optional, Tuple, Union


class P4CLI:
    """Perforce CLI backend."""
    def __init__(self):
        """Initialize P4CLI."""
        self.p4_cmd = "p4"  # Path to p4 executable
        self.log = logging.getLogger(__name__)

    def _run_command(self, args: list[str]) -> tuple[str, str]:
        """Run p4 command and return stdout and stderr.

        Args:
            args (list[str]): List of arguments for p4 command.

        Returns:
            tuple[str, str]: Tuple of stdout and stderr.

        Raises:
            RuntimeError: If command fails.

        """
        process = subprocess.Popen(
            [self.p4_cmd, *args],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True
        )
        stdout, stderr = process.communicate()
        if process.returncode != 0:
            msg = f"P4 command failed: {stderr}"
            raise RuntimeError(msg)
        return stdout, stderr

    def login(
            self, host: str, port: int, username: str,
            password: str, workspace: str) -> None:
        """Login to Perforce server.

        Args:
            host (str): Host name.
            port (int): Port number.
            username (str): User name.
            password (str): Password.
            workspace (str): Workspace name.

        """
        # Set environment
        self._run_command(
            ["-p", f"{host}:{port}", "-u", username,
             "-c", workspace, "login", password])

    def checkout(self, path: Union[str, pathlib.Path],
                 description: Optional[str] = None) -> bool:
        """Checkout file(s) from Perforce.

        Args:
            path (Union[str, pathlib.Path]): Path to file(s).
            description (Optional[str], optional): Description for
                checkout. Defaults to None.

        Returns:
            bool: True if successful, False otherwise.

        """
        args = ["edit"]
        if description:
            args.extend(["-c", description])
        args.append(str(path))

        try:
            self._run_command(args)
        except RuntimeError as e:
            self.log.exception("Checkout failed: %s", e, exc_info=True)
            return False
        else:
            return True

    def add(self, path: Union[str, pathlib.Path], description: Optional[str] = None) -> bool:
        """Add file(s) to Perforce."""
        args = ["add"]
        if description:
            args.extend(["-c", description])
        args.append(str(path))

        try:
            self._run_command(args)
            return True
        except RuntimeError:
            return False

    def submit(self, description: str) -> Optional[int]:
        """Submit changelist to Perforce."""
        try:
            stdout, _ = self._run_command(["submit", "-d", description])
            # Parse change number from output
            for line in stdout.splitlines():
                if "Change" in line and "submitted" in line:
                    return int(line.split()[1])
            return None
        except RuntimeError:
            return None

    def revert(self, path: Union[str, pathlib.Path]) -> bool:
        """Revert changes."""
        try:
            self._run_command(["revert", str(path)])
            return True
        except RuntimeError:
            return False

    def get_stat(self, path: Union[str, pathlib.Path]) -> Optional[dict]:
        """Get file status information."""
        try:
            stdout, _ = self._run_command(["fstat", str(path)])
            stat = {}
            for line in stdout.splitlines():
                if "..." in line:
                    key, value = line.split("...", 1)
                    stat[key.strip()] = value.strip()
            return stat
        except RuntimeError:
            return None

    def sync(self, path: Union[str, pathlib.Path]) -> bool:
        """Sync file(s) from Perforce."""
        try:
            self._run_command(["sync", str(path)])
            return True
        except RuntimeError:
            return False

    def get_version_info(self, path: Union[str, pathlib.Path]) -> Optional[Tuple[int, int]]:
        """Get version information (head revision and have revision)."""
        stat = self.get_stat(path)
        if stat:
            head_rev = int(stat.get("headRev", "0"))
            have_rev = int(stat.get("haveRev", "0"))
            return head_rev, have_rev
        return None

    def is_checked_out(self, path: Union[str, pathlib.Path]) -> bool:
        """Check if file is checked out."""
        stat = self.get_stat(path)
        if stat:
            action = stat.get("action", "")
            return action in ("edit", "add")
        return False

    def get_workspaces(self) -> List[str]:
        """Get list of available workspaces."""
        try:
            stdout, _ = self._run_command(["clients", "-u", self.get_user_name()])
            workspaces = []
            for line in stdout.splitlines():
                if line.strip():
                    workspaces.append(line.split()[1])
            return workspaces
        except RuntimeError:
            return []

    def get_user_name(self) -> str:
        """Get current user name."""
        try:
            stdout, _ = self._run_command(["user", "-o"])
            for line in stdout.splitlines():
                if line.startswith("User:"):
                    return line.split(":")[1].strip()
            return ""
        except RuntimeError:
            return ""

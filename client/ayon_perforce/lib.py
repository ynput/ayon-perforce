from __future__ import annotations

from os import environ
from dataclasses import dataclass
from pathlib import Path

import subprocess
from typing import TYPE_CHECKING, Optional

from ayon_core.lib import Logger, AYONSecureRegistry


log = Logger.get_logger(__name__)


def call_command(
    command: list[str],
    from_stdin: Optional[str] = None,
) -> list[str]:
    """Call a command and return the output.

    Args:
        command (list[str]): The command to run as a list of strings.
        from_stdin (Optional[str], optional): Input to be passed to the command. Defaults to None.

    Returns:
        list[str]: The output of the command as a list of strings.
    """
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            check=True,
            input=from_stdin,
        )
        return result.stdout.splitlines()
    except subprocess.CalledProcessError as e:
        if e.stderr:
            raise RuntimeError(e.stderr.strip()) from e
        msg = f"{e.stderr.strip()}\n{e.stdout.strip()}"
        log.debug(msg)
        return []


@dataclass
class WorkspaceProfileContext:
    """Data that could be used in filtering workspace name."""

    folder_paths: str
    task_names: str
    task_types: str


@dataclass
class P4:
    """Perforce cli abstraction.

    Used for grouping together common P4 commands.
    """

    @staticmethod
    def available_depots() -> list[str]:
        """Get all available depots for the current user.

        Returns:
            list[str]: A list of depot names.
        """
        cmd_out: list = call_command(["p4", "depots"])
        result: list[str] = []
        for line in cmd_out:
            if not line or line.startswith("#"):
                continue
            splits = line.split(" ")
            if len(splits) < 2:
                continue
            result.append(splits[1])
        return result

    @staticmethod
    def available_workspaces() -> list[str]:
        """Get all available workspaces for the current user.

        Returns:
            list[str]: A list of workspace names.
        """
        cmd_out: list = call_command(["p4", "clients"])
        result: list[str] = []
        for line in cmd_out:
            if not line or line.startswith("#"):
                continue
            splits = line.split(" ")
            if len(splits) < 2:
                continue
            result.append(splits[1])
        return result

    @staticmethod
    def available_streams(depot: str = None) -> list[str]:
        """Get all available streams for the current user.

        Returns:
            list[str]: A list of stream names.
        """
        cmd_out: list = call_command(["p4", "streams"])
        if depot:
            if not depot.startswith("//"):
                depot = f"//{depot}"
            if not depot.endswith("/..."):
                depot = f"{depot}/..."
            cmd_out = call_command(["p4", "streams", depot])
        result: list[str] = []
        for line in cmd_out:
            if not line or line.startswith("#"):
                continue
            splits = line.split(" ")
            if len(splits) < 2:
                continue
            result.append(splits[1])
        return result


@dataclass
class P4Workspace:
    """Perforce client/workspace abstraction.

    This class is used to manage Perforce workspaces, including creating,
    updating, and syncing them. It also provides methods to retrieve the
    current workspace and available workspaces.
    """

    name: str
    stream: str = None
    options: list[str] = None
    owner: str = None
    root: Optional[Path] = None
    depot: Optional[str] = None
    host: Optional[str] = None

    def __post_init__(self):
        """Post-initialization of the P4Workspace class.

        Ensures a local workspace is created and updated from its properties.

        # TODO:
            - implement long running task spinner for p4 sync

        Raises:
            RuntimeError: If the depot or stream are not present.
        """
        if self.stream and not self.stream.startswith("//"):
            self.stream = f"//{self.depot}/{self.stream}"

    def switch(self, force: bool=False) -> None:
        """Update the workspace from generated spec and activates it."""
        # check if workspace name is already on the server
        if self.name not in P4.available_workspaces():
            msg = f"Workspace `{self.name}` does not exist on the Perforce server. Creating one."
            log.debug(msg)

        # check if the stream is available
        if self.stream not in P4.available_streams(depot=self.depot):
            msg = f"Stream `{self.stream}` does not exist on the Perforce server."
            log.debug(f"{P4.available_streams(depot=self.depot) = }")
            raise RuntimeError(msg)

        # check if the depot is available
        if self.depot not in P4.available_depots():
            # depot is not in p4 spec
            msg = (
                f"Depot `{self.depot}` does not exist on the Perforce server."
            )
            log.debug(f"{P4.available_depots() = }")
            raise RuntimeError(msg)

        # check if workspace has opened files and can be switched to a different stream
        if force:
            try:
                call_command(["p4", "revert", "//..."])
            except RuntimeError:
                log.warning("Failed to revert files. Ignoring.")
        else:
            curr_ws = P4Workspace.current()
            log.debug(f"{curr_ws = }")
            if P4Workspace.opened_files() and curr_ws.stream != self.stream:
                raise RuntimeError(
                    "Workspace has opened files. Can't switch streams."
                )

        # generate a new p4 spec and set it as current workspace
        spec = self.generate_spec()
        switch_cmd = ["p4", "client", "-i"]
        if force:
            switch_cmd.append("-f")
        call_command(switch_cmd, from_stdin=spec)
        call_command(["p4", "set", f"P4CLIENT={self.name}"])

        # set instance variables based on created spec
        cmd_out: list = call_command(["p4", "client", "-o", self.name])
        spec_setts = self.parse_cli_spec(cmd_out)
        log.debug(f"{cmd_out = }")
        log.debug(f"cli spec: {spec_setts = }")
        for sett_key, sett_val in spec_setts.items():
            setattr(self, sett_key, sett_val)

        # get latest files for workspace stream
        self.get_latest(force=force)

    def get_latest(self, force: bool = False) -> None:
        """Get the latest changes for this workspace.

        # TODO:
            - implement long running task spinner for p4 sync
            - change stash:bool to mode:enum

        Args:
            stash (bool, optional): If True, stashes the changes before syncing. Defaults to False

        Raises:
            RuntimeError: If the sync command fails.
        """
        sync_dry_run = call_command(["p4", "sync", "-n"])
        total_sync_changes: int = len(sync_dry_run)
        log.debug(f"total changes to sync: {total_sync_changes}")
        if total_sync_changes == 0:
            log.info("Nothing to sync, workspace already up-to-date.")
            return
        try:
            cmd = ["p4", "sync"]
            if force:
                cmd.append("-f")
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=1,
            )
            for idx, line in enumerate(iter(proc.stdout.readline, "")):
                msg = f"{idx + 1} / {total_sync_changes} - {line.strip()}"
                log.debug(msg)

            proc.stdout.close()
            retcode = proc.wait()
            if retcode:
                errmsg = f"p4 sync failed: {proc.stderr.strip}"
                raise RuntimeError(errmsg)

            if force:
                # reconcile all files in the workspace, takes some time
                cmd = ["p4", "clean", "-n", f"{self.root}/..."]
                proc = subprocess.Popen(
                    cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    bufsize=1,
                )
                for line in iter(proc.stdout.readline, ""):
                    log.debug(line.strip())
                proc.stdout.close()
                retcode = proc.wait()
                if retcode:
                    errmsg = f"p4 clean failed: {proc.stderr.strip()}"
                    raise RuntimeError(errmsg)
                log.info("p4 clean finished successfully.")

        except subprocess.CalledProcessError as e:
            raise RuntimeError from e

    def generate_spec(self, stream: Optional[str] = None) -> dict:
        options_str = " ".join(self.options or [])
        spec_lines = [
            f"Client: {self.name}",
            f"Host: {self.host}",
            f"Owner: {self.owner}",
            "Description: Created by Ayon Launcher Hook",
            f"Root: {self.root}",
            f"Stream: {stream or self.stream}",
            f"Options: {options_str}",
            "",  # newline required by spec
        ]
        spec = "\n".join(spec_lines)
        log.debug(f"generated spec: {spec}")
        return spec

    @classmethod
    def current(cls) -> P4Workspace:
        """Get the currently active Perforce workspace.

        Retrieved by calling `p4 client -o` and parsing the output.

        Returns:
            P4Workspace: The current Perforce workspace object.
        """
        cmd_out: list = call_command(["p4", "client", "-o"])
        spec: dict = P4Workspace.parse_cli_spec(cmd_out)
        return cls(**spec)

    @staticmethod
    def parse_cli_spec(spec: list[str]) -> dict[str, any]:
        """Parse Perforce spec output into a dictionary.

        Args:
            spec (list[str]): The output of the `p4 client -o` command.

        Returns:
            dict[str, str]: A dictionary containing the parsed spec information.
        """
        spec_dict = {}
        for line in spec:
            if not line or line.startswith("#"):
                continue

            if line.startswith("Root:"):
                spec_dict["root"] = line.replace("Root:", "").strip()
            if line.startswith("Stream:"):
                spec_dict["stream"] = line.replace("Stream:", "").strip()
            if line.startswith("Owner:"):
                spec_dict["owner"] = line.replace("Owner:", "").strip()
            if line.startswith("Client:"):
                spec_dict["name"] = line.replace("Client:", "").strip()
            if line.startswith("Options:"):
                options = line.replace("Options:", "").strip()
                spec_dict["options"] = options.split(" ")

        return spec_dict

    @staticmethod
    def opened_files() -> list[str]:
        """Get the list of opened files in the current workspace.

        Returns:
            list[str]: A list of opened files.
        """
        cmd_out: list = call_command(["p4", "opened"])
        log.debug(f"opened files: {cmd_out}")
        return cmd_out


def get_local_login() -> None:
    """Get the Perforce Login entry from the local registry."""
    try:
        reg = AYONSecureRegistry("perforce/username")
        username = reg.get_item("value")
        reg = AYONSecureRegistry("perforce/password")
        password = reg.get_item("value")
    except ValueError:
        return (None, None)

    return (username, password)


def save_local_login(username: str, password: str) -> None:
    """Save the Perforce Login entry from the local registry."""
    reg = AYONSecureRegistry("perforce/username")
    reg.set_item("value", username)
    reg = AYONSecureRegistry("perforce/password")
    reg.set_item("value", password)
    environ["P4USER"] = username


def clear_local_login() -> None:
    """Clear the Perforce Login entry from the local registry."""
    reg = AYONSecureRegistry("perforce/user")
    if reg.get_item("value", None) is not None:
        reg.delete_item("value")
    reg = AYONSecureRegistry("perforce/pass")
    if reg.get_item("value", None) is not None:
        reg.delete_item("value")
    environ["P4USER"] = ""

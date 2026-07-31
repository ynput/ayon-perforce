"""Library for Perforce client operations."""
from __future__ import annotations

import subprocess
from dataclasses import dataclass
from os import environ
from typing import Any

from ayon_core.lib import AYONSecureRegistry, Logger

from ayon_perforce.api.commands import P4Commands

log = Logger.get_logger(__name__)


class P4WorkspaceError(Exception):
    """Raised on P4 workspace failure."""


class P4StreamError(Exception):
    """Raised on P4 stream failure."""


@dataclass
class WorkspaceProfileContext:
    """Data that could be used in filtering workspace name."""

    folder_paths: str
    task_names: str
    task_types: str


@dataclass
class P4Workspace:
    """Perforce client/workspace abstraction.

    This class is used to manage Perforce workspaces, including creating,
    updating, and syncing them. It also provides methods to retrieve the
    current workspace and available workspaces.
    """

    owner: str
    host: str
    root: str
    depot: str | None = None
    name: str | None = None
    stream: str | None = None
    options: list[str] | None = None

    def __init__(
            self,

            owner: str,
            host: str,
            root: str,
            depot: str | None = None,
            name: str | None = None,
            options: list[str] | None = None,
            stream: str | None = None):
        """Construct P4Workspace.

        Args:
            name (str, optional): The name of the workspace. Defaults to None.
            owner (str): The owner of the workspace.
            host (str): The host associated with the workspace.
            root (str): The root directory of the workspace.
            depot (str): The depot associated with the workspace.
            options (list[str], optional): Options for the workspace.
                Defaults to None.
            stream (str, optional): Stream associated with the workspace.
                Defaults to None.

        """
        self.name = name
        self.owner = owner
        self.host = host
        self.root = root
        self.depot = depot
        self.options = options or []
        if stream:
            self.stream = f"//{depot}/{stream}"

    @staticmethod
    def from_dict(data: dict[str, Any]) -> P4Workspace:
        """Create a P4Workspace instance from a dictionary.

        Args:
            data (dict[str, Any]): A dictionary containing workspace properties.

        Returns:
            P4Workspace: An instance of P4Workspace initialized
            with the provided data.

        Raises:
              P4WorkspaceError: If the dictionary is missing required keys
                or has invalid values.

        """
        try:
            ws = P4Workspace(
                name=data.get("Name"),
                owner=data["Owner"],
                host=data["Host"],
                root=data["Root"],
                depot=data.get("Depot"),
                options=data.get("Options", "").split(" "),
                stream=data.get("Stream")
            )
        except AttributeError as e:
            msg = f"Failed to create P4Workspace from dict: {e}"
            raise P4WorkspaceError(msg) from e

        return ws

    def __post_init__(self):
        """Post-initialization of the P4Workspace class.

        Ensures a local workspace is created and updated from its properties.

        Todo:
            - implement long running task spinner for p4 sync

        """
        if self.stream and not self.stream.startswith("//"):
            self.stream = f"//{self.depot}/{self.stream}"

    def switch(self, *, force: bool = False) -> None:
        """Update the workspace from generated spec and activates it.

        Args:
            force (bool, optional): If True, forces the switch even if there are
                opened files. Defaults to False.

        Raises:
            RuntimeError: If the depot or stream are not present,
            or if there are opened files and force is False.

        """
        # check if workspace name is already on the server
        if self.name not in P4Commands.get_available_workspaces(
                username=self.owner):
            msg = (
                f"Workspace `{self.name}` does not exist on "
                "the Perforce server. Creating one."
            )
            log.debug(msg)

        # check if the stream is available
        if self.stream not in P4Commands.get_available_streams(
                depot=self.depot):
            msg = (
                f"Stream `{self.stream}` does not exist on "
                "the Perforce server."
            )
            log.debug(
                "Streams: %s",
                P4Commands.get_available_streams(depot=self.depot))
            raise RuntimeError(msg)

        # check if the depot is available
        if self.depot not in P4Commands.get_available_depots():
            # depot is not in p4 spec
            msg = (
                f"Depot `{self.depot}` does not exist on the Perforce server."
            )
            log.debug(f"{P4Commands.get_available_depots() = }")
            raise RuntimeError(msg)

        # Check if workspace has opened files and
        # can be switched to a different stream.
        if force:
            try:
                P4Commands.run_p4("revert", "//...")
            except RuntimeError:
                log.warning("Failed to revert files. Ignoring.")
        else:
            curr_ws = P4Workspace.current()
            if not curr_ws.owner:
                curr_ws.owner = self.owner
            log.debug(f"{curr_ws = }")
            if P4Workspace.opened_files() and curr_ws.stream != self.stream:
                msg = "Workspace has opened files. Can't switch streams."
                raise RuntimeError(msg)

        # generate a new p4 spec and set it as current workspace
        spec = self.generate_spec()
        switch_cmd = ["client", "-i"]
        if force:
            switch_cmd.append("-f")
        P4Commands.run_p4(*switch_cmd, stdin_data=spec)
        P4Commands.run_p4("set", f"P4CLIENT={self.name}")

        # set instance variables based on created spec
        cmd_out = P4Commands.run_p4("client", "-o", self.name)[0]
        ws_specs = P4Workspace.from_dict(cmd_out)
        log.debug(f"{cmd_out = }")
        log.debug(f"cli spec: {ws_specs = }")
        self.name = ws_specs.name
        self.owner = ws_specs.owner
        self.root = ws_specs.root
        self.depot = ws_specs.depot
        self.options = ws_specs.options
        self.stream = ws_specs.stream

        # get latest files for workspace stream
        self.get_latest(force=force)

    def get_latest(self, *, force: bool = False) -> None:
        """Get the latest changes for this workspace.

        Todo:
            - implement long running task spinner for p4 sync
            - change stash:bool to mode:enum

        Args:
            force (bool, optional): If True, forces the sync even if there are
                opened files. Defaults to False.

        Raises:
            RuntimeError: If the sync command fails.
        """
        sync_dry_run = P4Commands.run_p4("sync", "-n", f"{self.root}/...")
        if len(sync_dry_run) == 1 and sync_dry_run[0].get("code") == "error":
            log.error(
                "%s %s (root %s)",
                sync_dry_run[0].get("code"),
                sync_dry_run[0].get("data"),
                self.root)
            return
        total_sync_changes: int = len(sync_dry_run)
        log.debug(f"total changes to sync: {total_sync_changes}")
        if total_sync_changes == 0:
            log.info("Nothing to sync, workspace already up-to-date.")
            return
        from pprint import pprint
        try:
            result = P4Commands.run_p4(
                "sync", "-f" if force else "", f"{self.root}/...")
            for idx, item in enumerate(result):
                msg = f"{idx + 1} / {total_sync_changes} - {item}"
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

    def generate_spec(
            self, stream: str | None = None) -> str:
        """Serialize specs structure from P4Workspace instance.

        Args:
            stream (str | None): The stream to use for the spec. If None, the
                current workspace stream is used.

        Returns:
            str: serialized spec structure.

        """
        options_str = " ".join(self.options or [])
        spec = {
            "Client": self.name,
            "Host": self.host,
            "Owner": self.owner,
            "Description": "Created by Ayon Launcher Hook",
            "Root": self.root,
            "Stream": stream or self.stream,
            "Options": options_str,
        }
        out = ""
        for key, value in spec.items():
            out = f"{key}: {value}\n"

        out += "\n"
        return out


    @classmethod
    def current(cls) -> P4Workspace:
        """Get the currently active Perforce workspace.

        Retrieved by calling `p4 client -o` and parsing the output.

        Returns:
            P4Workspace: The current Perforce workspace object.
        """
        cmd_out = P4Commands.run_p4("client", "-o")[0]
        return P4Workspace.from_dict(cmd_out)


    @staticmethod
    def opened_files() -> list[str]:
        """Get the list of opened files in the current workspace.

        Returns:
            list[str]: A list of opened files.
        """
        cmd_out: list = P4Commands.run_p4("opened")
        log.debug(f"opened files: {cmd_out}")
        return cmd_out


def get_local_login() -> tuple[str | None, str | None]:
    """Get the Perforce Login entry from the local registry."""
    try:
        reg = AYONSecureRegistry("perforce/username")
        username = reg.get_item("value")
        reg = AYONSecureRegistry("perforce/password")
        password = reg.get_item("value")
    except ValueError:
        # if not in registry, use what p4 give us
        info = P4Commands.run_p4("info")
        username = info[0].get("userName")
        password = None
        return username, password

    return username, password


def save_local_login(username: str, password: str) -> None:
    """Save the Perforce Login entry from the local registry."""
    reg = AYONSecureRegistry("perforce/username")
    reg.set_item("value", username)
    reg = AYONSecureRegistry("perforce/password")
    reg.set_item("value", password)
    environ["P4USER"] = username


def clear_local_login() -> None:
    """Clear the Perforce Login entry from the local registry."""
    reg = AYONSecureRegistry("perforce/username")
    if reg.get_item("value", None) is not None:
        reg.delete_item("value")
    reg = AYONSecureRegistry("perforce/password")
    if reg.get_item("value", None) is not None:
        reg.delete_item("value")
    environ["P4USER"] = ""

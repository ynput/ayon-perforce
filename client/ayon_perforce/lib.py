"""Library for Perforce client operations."""
from __future__ import annotations

import os
from dataclasses import dataclass
from os import environ
from typing import TYPE_CHECKING, Any

from ayon_core.lib import AYONSecureRegistry, Logger

from ayon_perforce.api.commands import P4Commands
from ayon_perforce.api.exceptions import P4Error
from ayon_perforce.api.models import MessageSeverity

if TYPE_CHECKING:
    from ayon_perforce.api.models import PerforceDict

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

    def __init__(  # ruff: ignore[too-many-arguments]
            self,
            *,
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
        self.stream = stream
        self.__post_init__()

    @staticmethod
    def from_dict(data: dict[str, Any]) -> P4Workspace:
        """Create a P4Workspace instance from a dictionary.

        Args:
            data (dict[str, Any]): A dictionary containing
                workspace properties.

        Returns:
            P4Workspace: An instance of P4Workspace initialized
            with the provided data.

        Raises:
              P4WorkspaceError: If the dictionary is missing required keys
                or has invalid values.

        """
        depot_stream_split_parts = 2
        stream = data.get("Stream")
        depot = data.get("Depot")
        if not depot and isinstance(stream, str) and stream.startswith("//"):
            parts = stream.split("/")
            if len(parts) > depot_stream_split_parts:
                depot = parts[depot_stream_split_parts]

        try:
            ws = P4Workspace(
                name=data.get("Client") or data.get("Name"),
                owner=data["Owner"],
                host=data.get("Host", ""),
                root=data["Root"],
                depot=depot,
                options=str(data.get("Options", "")).split(),
                stream=stream,
            )
        except (AttributeError, KeyError, TypeError) as e:
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

    def _get_existing_spec(self) -> PerforceDict | None:
        """Return spec of the workspace on the server, of any owner.

        Returns:
            Workspace spec, or ``None`` if the workspace doesn't exist.

        """
        # `client -o` of a missing workspace returns a template spec
        # without `Update`, errors (e.g. failed login) are raised
        spec = P4Commands.run_p4("client", "-o", str(self.name))[0]
        return spec if "Update" in spec else None

    def _check_existing(self, existing: PerforceDict) -> None:
        """Check the existing workspace can be used by this workspace.

        Owner and host must match, otherwise the workspace belongs to
        someone else or can't be used on this machine. Root must match,
        otherwise files would be synced where AYON doesn't expect them.

        Raises:
            RuntimeError: If owner, host or root differ.

        """
        owner = existing.get("Owner", "")
        if owner.casefold() != self.owner.casefold():
            msg = (
                f"Workspace `{self.name}` belongs to `{owner}`, "
                f"not `{self.owner}`. Change the workspace name template "
                "or the owner of the workspace."
            )
            raise RuntimeError(msg)

        host = existing.get("Host", "")
        if host and self.host and host.casefold() != self.host.casefold():
            msg = (
                f"Workspace `{self.name}` is bound to host `{host}`, "
                f"it can't be used on `{self.host}`."
            )
            raise RuntimeError(msg)

        root = existing.get("Root", "")
        if _normalize_path(root) != _normalize_path(self.root):
            # changing the root keeps the have list, p4 would consider
            # files at the new root as synced
            msg = (
                f"Workspace `{self.name}` has root `{root}`, but AYON "
                f"expects `{self.root}`. Move the workspace to the "
                "expected root or change the work template."
            )
            raise RuntimeError(msg)

    def _use_existing_stream(self, existing: PerforceDict) -> None:
        """Use stream of the existing workspace with the same name.

        Keeps an existing stream workspace on its stream instead of
        converting it to a classic workspace.
        """
        stream = existing.get("Stream")
        if not stream:
            return
        self.stream = stream
        # stream path is `//<depot>/<stream>`
        self.depot = stream.split("/")[2]
        log.debug(
            "Using stream `%s` of existing workspace `%s`.",
            self.stream, self.name)

    def _validate_stream_and_depot(self) -> None:
        """Check stream and depot of the workspace exist on the server.

        Raises:
            RuntimeError: If the depot or stream are not present.

        """
        # depot first, p4 error for streams of a missing depot is cryptic
        depot_types = {
            record["name"]: record.get("type", "")
            for record in P4Commands.run_p4("depots")
            if "name" in record
        }
        if self.depot not in depot_types:
            msg = (
                f"Depot `{self.depot}` does not exist on the Perforce server. "
                f"Available depots: {', '.join(sorted(depot_types))}"
            )
            raise RuntimeError(msg)

        # classic workspace has no stream
        if not self.stream:
            return

        if depot_types[self.depot] != "stream":
            msg = (
                f"Depot `{self.depot}` is not a stream depot "
                f"(type `{depot_types[self.depot]}`), but stream "
                f"`{self.stream}` is requested."
            )
            raise RuntimeError(msg)

        streams = P4Commands.get_available_streams(depot=self.depot)
        if self.stream not in streams:
            msg = (
                f"Stream `{self.stream}` does not exist on the Perforce "
                f"server. Available streams: {', '.join(sorted(streams))}"
            )
            raise RuntimeError(msg)

    def _check_opened_files(self, existing: PerforceDict) -> None:
        """Check opened files of the workspace don't block stream switch.

        Raises:
            RuntimeError: If there are opened files and the stream differs.

        """
        if existing.get("Stream") == self.stream:
            return
        # "file(s) not opened on this client." is a warning
        records = P4Commands.run_p4(
            "opened", "-C", str(self.name), "-m", "1",
            max_severity=MessageSeverity.WARNING,
        )
        if any(record.get("code") != "error" for record in records):
            msg = (
                f"Workspace `{self.name}` has opened files. Can't switch "
                f"stream `{existing.get('Stream')}` to `{self.stream}`."
            )
            raise RuntimeError(msg)

    def switch(
        self,
        *,
        force: bool = False,
        sync: bool = True,
        persist: bool = True,
    ) -> None:
        """Update the workspace from generated spec and activates it.

        Args:
            force (bool, optional): If True, forces the switch even
                if there are opened files. Defaults to False.
            sync (bool, optional): Sync the workspace to the latest
                revision after switching. Defaults to True.
            persist (bool, optional): Store the workspace with
                ``p4 set P4CLIENT`` for other processes on the machine.
                Otherwise it is used only by this process. Defaults
                to True.

        Existing workspace (of any owner) is updated, not replaced, only
        its stream and options are changed.

        `RuntimeError` is raised if the depot or stream are not present,
        if the existing workspace has a different owner, host or root,
        or if there are opened files and force is False.

        """
        existing = self._get_existing_spec()
        if existing is None:
            log.debug(
                "Workspace `%s` does not exist on the Perforce server. "
                "Creating one.", self.name)
        else:
            self._check_existing(existing)
            if not self.stream:
                self._use_existing_stream(existing)

        self._validate_stream_and_depot()

        # Check if workspace has opened files and
        # can be switched to a different stream.
        if force:
            try:
                P4Commands.run_p4("revert", "//...")
            except P4Error:
                log.warning("Failed to revert files. Ignoring.")
        elif existing is not None:
            # new workspace has no opened files
            self._check_opened_files(existing)

        # generate a new p4 spec and set it as current workspace
        spec = (
            self.generate_spec() if existing is None
            else self._update_spec(existing)
        )
        log.debug("Generated spec: %s", spec)
        switch_cmd = ["client", "-i"]
        if force:
            switch_cmd.append("-f")
        P4Commands.run_p4(*switch_cmd, stdin_data=spec)
        P4Commands.set_client(self.name)
        if persist:
            # make it the current workspace for other p4 tools too
            P4Commands.run_p4("set", f"P4CLIENT={self.name}")

        # set instance variables based on created spec
        cmd_out = P4Commands.run_p4("client", "-o", self.name)[0]
        ws_specs = P4Workspace.from_dict(cmd_out)
        log.debug("cmd_out: %s", cmd_out)
        log.debug("cli spec: %s", ws_specs)
        self.name = ws_specs.name
        self.owner = ws_specs.owner
        self.root = ws_specs.root
        self.depot = ws_specs.depot
        self.options = ws_specs.options
        self.stream = ws_specs.stream

        # get latest files for workspace stream
        if sync:
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
        try:
            # "file(s) up-to-date." is a warning, possibly with exit code 1
            sync_dry_run = P4Commands.run_p4(
                "sync", "-n", f"{self.root}/...",
                max_severity=MessageSeverity.WARNING,
            )
        except P4Error as err:
            msg = f"Failed to sync workspace `{self.name}`: {err}"
            raise RuntimeError(msg) from err
        sync_dry_run = [
            record for record in sync_dry_run
            if record.get("code") != "error"
        ]
        total_sync_changes: int = len(sync_dry_run)
        log.debug("total changes to sync: %s", total_sync_changes)
        if total_sync_changes == 0:
            log.info("Nothing to sync, workspace already up-to-date.")
            return
        args = ["sync"]
        if force:
            args.append("-f")
        args.append(f"{self.root}/...")
        cleaned = None
        try:
            result = P4Commands.run_p4(*args)
            if force:
                # preview files not matching the depot, takes some time
                cleaned = P4Commands.run_p4(
                    "clean", "-n", f"{self.root}/...",
                    max_severity=MessageSeverity.WARNING,
                )
        except P4Error as err:
            msg = f"Failed to sync workspace `{self.name}`: {err}"
            raise RuntimeError(msg) from err

        for idx, item in enumerate(result):
            log.debug("%s / %s - %s", idx + 1, total_sync_changes, item)
        if cleaned is not None:
            for item in cleaned:
                log.debug("%s", item.get("data") or item)
            log.info("p4 clean finished successfully.")

    def generate_spec(
            self, stream: str | None = None) -> PerforceDict:
        """Generate the spec structure from the P4Workspace instance.

        The result is a plain dict, directly usable as ``stdin_data``
        for :meth:`P4Commands.run_p4`, which marshals it for ``p4 -G``.

        Args:
            stream (str | None): The stream to use for the spec. If None, the
                current workspace stream is used.

        Returns:
            PerforceDict: spec structure ready to be marshalled.

        """
        options_str = " ".join(self.options or [])
        spec = {
            "Client": self.name or "",
            "Host": self.host or "",
            "Owner": self.owner,
            "Description": "Created by Ayon Launcher Hook",
            "Root": self.root,
            "Options": options_str,
        }
        stream = stream or self.stream
        if stream:
            spec["Stream"] = str(stream)
        else:
            # classic workspace, map the whole depot to the workspace root
            spec["View0"] = f"//{self.depot}/... //{self.name}/..."
        return spec

    def _update_spec(self, existing: PerforceDict) -> PerforceDict:
        """Update spec of the existing workspace from this workspace.

        Only stream and options are changed, the rest (description, view
        of classic workspace, submit options...) is kept.

        Args:
            existing: Spec of the existing workspace.

        Returns:
            PerforceDict: spec structure ready to be marshalled.

        """
        spec = {
            key: value for key, value in existing.items()
            if key not in {"code", "Update", "Access"}
        }
        spec["Options"] = " ".join(self.options or [])
        if self.stream:
            spec["Stream"] = str(self.stream)
            # view of stream workspace is generated from the stream
            spec = {
                key: value for key, value in spec.items()
                if not key.startswith("View")
            }
        return spec

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
        try:
            cmd_out: list = P4Commands.run_p4("opened")
        except P4Error as err:
            # e.g. client doesn't exist yet
            log.debug("Can't list opened files: %s", err)
            return []
        log.debug("opened files: %s", cmd_out)
        return cmd_out


def _normalize_path(path: str) -> str:
    """Return path comparable regardless of separators and trailing slash.

    Returns:
        Normalized path, case-insensitive on Windows.

    """
    return os.path.normcase(os.path.normpath(path)) if path else ""


def get_local_login() -> tuple[str | None, str | None]:
    """Get the Perforce Login entry from the local registry.

    Returns:
        tuple[str | None, str | None]: A tuple containing the username and
            password. If not found, returns (None, None).

    """
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

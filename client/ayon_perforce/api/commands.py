"""Perforce operations via direct p4 CLI subprocess calls.

This is enhanced by code taken from https://github.com/tahv/pyforce
but stripped of pydantic logic.

"""
from __future__ import annotations

import io
import itertools
import marshal
import os
import re
import shutil
import subprocess
from logging import getLogger
from typing import TYPE_CHECKING, Iterator, TypeVar

from ayon_perforce.api.exceptions import (
    ChangeUnknownError,
    ClientNotFoundError,
    CommandExecutionError,
    UserNotFoundError,
)
from ayon_perforce.api.models import (
    ActionInfo,
    ActionMessage,
    Change,
    ChangeInfo,
    ChangeStatus,
    Client,
    Connection,
    FStat,
    MarshalCode,
    MessageSeverity,
    PerforceDict,
    Revision,
    Sync,
    User,
)

if TYPE_CHECKING:
    import pathlib


log = getLogger(__name__)
R = TypeVar("R")


class P4Commands:  # ruff: ignore[too-many-public-methods]
    """Perforce stub that invokes the ``p4`` CLI directly via subprocess."""

    _connection: Connection | None = None

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @classmethod
    def _get_env(cls) -> dict:
        """Return an environment dict with P4 variables set."""
        env = os.environ.copy()
        if cls._connection:
            if cls._connection.port:
                env["P4PORT"] = cls._connection.port
            if cls._connection.user:
                env["P4USER"] = cls._connection.user
            if cls._connection.client:
                env["P4CLIENT"] = cls._connection.client
            if cls._connection.password:
                env["P4PASSWD"] = cls._connection.password
        return env

    @classmethod
    def _extract_indexed_values(
            cls, data: dict[str, R], prefix: str) -> list[R]:
        """Pop indexed keys, in `data` to list of value.

        Args:
            data: dict to pop process.
            prefix: Indexed key prefix. For example, the keys in
                `{'Files0': "//depot/foo", 'Files1': "//depot/bar"}` have
                the prefix `Files`.

        Returns:
            list of indexed values

        """
        result: list[R] = []
        counter = itertools.count()

        while True:
            index = next(counter)
            value: R | None = data.pop(f"{prefix}{index}", None)
            if value is None:
                break
            result.append(value)

        return result

    @classmethod
    def run_p4(
        cls,
        *args: str,
        stdin_data: PerforceDict | str | None = None,
        max_severity: MessageSeverity = MessageSeverity.EMPTY,
    ) -> list[dict]:
        """Execute ``p4 -G <args>`` and return decoded marshal records.

        Args:
            *args: Arguments forwarded to ``p4``.
            stdin_data: Optional dict written to the process stdin.
            max_severity: Maximum severity of error messages to ignore.
                Any error with a severity less than or equal to this value
                will be ignored.

        Returns:
            list[dict]: Non-error records from the marshal output.

        Raises:
            CommandExecutionError: If the ``p4`` command fails.
            ValueError: If data cannot be marshaled.

        """
        p4_exe = shutil.which("p4") or "p4"
        cmd = [p4_exe, "-G"] + [str(a) for a in args]
        log.debug("p4 cmd: %s", " ".join(cmd))
        # `marshal` requires exact builtin types, so str/dict subclasses
        # (e.g. AYON's `TemplateResult`) must be coerced before dumping.
        plain_stdin_data = (
            {str(k): str(v) for k, v in stdin_data.items()}
            if isinstance(stdin_data, dict) else stdin_data
        )
        try:
            proc = subprocess.run(
                cmd,
                input=marshal.dumps(
                    plain_stdin_data, 0) if plain_stdin_data else None,
                capture_output=True,
                check=False,
                env=cls._get_env(),
            )
        except ValueError as error:
            msg = f"Failed to marshall {stdin_data}: {error}"
            raise ValueError(msg) from error

        all_records = cls._parse_marshal(proc.stdout)
        error_records = [
            r for r in all_records
            if r.get("code") == MarshalCode.ERROR.value and int(r["severity"]) <= max_severity.value  # noqa: E501
        ]
        normal_records = [
            r for r in all_records
            if r.get("code") != MarshalCode.ERROR.value
        ]

        if proc.returncode != 0 and not normal_records:
            if error_records:
                msg = "; ".join(
                    r.get("data", "unknown p4 error") for r in error_records
                )
            else:
                msg = proc.stderr.decode(errors="replace").strip()
            raise CommandExecutionError(
                msg or f"p4 command failed (code: {proc.returncode}): {cmd}",
                command=cmd,
                data=error_records[0] if error_records else None,
            )
        return normal_records

    def get_user(self, user: str) -> User:
        """Get user from perforce.

        Args:
            user (str): Username.

        Returns:
            User: User object.

        Raises:
            UserNotFoundError: If user does not exist.

        """
        data = self.run_p4("user", "-o", user)[0]
        if "Update" not in data:
            msg = f"User {user!r} does not exist"
            raise UserNotFoundError(msg)
        return User(**data)  # type: ignore[arg-type]

    def get_client(self, client: str | None = None) -> Client:
        """Get client workspace specification.

        Command:
            `p4 client`_.

        Args:
            client: Perforce client name.

        Returns:
            Client: Perforce client (workspace) specification.

        Raises:
            ClientNotFoundError: If client does not exist.

        """
        if not client:
            data = self.run_p4("client", "-o")[0]
            return Client.client_from_data(**data)  # type: ignore[arg-type]
        data = self.run_p4("client", "-o", client)[0]
        if "Update" not in data:
            msg = f"Client {client!r} does not exists"
            raise ClientNotFoundError(msg)
        return Client.client_from_data(**data)  # type: ignore[arg-type]

    def get_change(self, change: int) -> Change:
        """Get changelist specification.

        Command:
            `p4 change`_.

        Args:
            change: The changelist number.

        Returns:
            Change: Perforce changelist specification.

        Raises:
            CommandExecutionError: If ``change`` not found.
            ChangeUnknownError: If ``change`` is unknown.

        """
        try:
            data = self.run_p4("change", "-o", str(change))[0]
        except CommandExecutionError as error:
            if error.data["data"].strip() == f"Change {change} unknown.":
                raise ChangeUnknownError(change) from error
            raise
        return Change(**data)  # type: ignore[arg-type]

    def create_changelist(self, description: str) -> ChangeInfo:
        """Create and return a new changelist.

        Command:
            `p4 change`_.

        Args:
            description: Changelist description.

        Returns:
            ChangeInfo

        """
        data = self.run_p4("change", "-o")[0]
        data["Description"] = description
        _ = self._extract_indexed_values(data, "Files")
        self.run_p4("change", "-i", stdin_data=data)

        data = self.run_p4("changes", "--me", "-m", "1", "-l")[0]
        return ChangeInfo(**data)  # type: ignore[arg-type]

    def changes(
        self,
        user: str | None = None,
        stream: str | None = None,
        client: str | None = None,
        *,
        status: ChangeStatus | None = None,
        long_output: bool = False,
    ) -> Iterator[ChangeInfo]:
        """Iter submitted and pending changelists.

        Command:
            `p4 changes`_.

        Args:
            user: List only changes made from that user.
            stream: List only changes for a given stream.
            client: Override the client workspace to list changes for.
                If not set, the current client is used.
            status: List only changes with the specified status.
            long_output: List long output, with full text of each
                changelist description.

        Yields:
            Iterator[ChangeInfo]: An iterator over `ChangeInfo` objects
                representing the changelists.

        Raises:
            CommandExecutionError: When command fails.

        """
        command = ["changes"]
        if user:
            command += ["-u", user]
        if stream:
            command += [f"{stream}/..."]
        if client:
            command += ["-c", client]
        if status:
            command += ["-s", status.value]
        if long_output:
            command += ["-l"]

        for data in self.run_p4(*command):
            if data.get("code") == MarshalCode.ERROR:
                msg = data.get("data", "unknown p4 error")
                raise CommandExecutionError(msg, command=command, data=data)
            yield ChangeInfo(**data)  # type: ignore[arg-type]

    def add(
        self,
        filespecs: list[str],
        *,
        changelist: int | None = None,
        preview: bool = False,
    ) -> tuple[list[ActionMessage], list[ActionInfo]]:
        """Open `filespecs` in client workspace for *addition* to the depot.

        Command:
            `p4 add`_.

        Args:
            filespecs: A list of `File specifications`_.
            changelist: Open the files within the specified changelist.
                If not set, the files are linked to the default changelist.
            preview: Preview which files would be opened for add,
                without actually changing any files or metadata.

        Returns:
            `ActionInfo` and `ActionMessage` objects. `ActionInfo` are
                only included if something unexpected happened
                during the operation.
        """
        command = ["add"]
        if changelist:
            command += ["-c", str(changelist)]
        if preview:
            command += ["-n"]
        command += filespecs

        messages: list[ActionMessage] = []
        infos: list[ActionInfo] = []
        for data in self.run_p4(*command):
            if data["code"] == MarshalCode.INFO:
                messages.append(ActionMessage.from_info_data(data))
            else:
                infos.append(ActionInfo(**data))  # type: ignore[arg-type]
        return messages, infos

    def edit(
        self,
        filespecs: list[str],
        *,
        changelist: int | None = None,
        preview: bool = False,
    ) -> tuple[list[ActionMessage], list[ActionInfo]]:
        """Open ``filespecs`` in client workspace for **edit**.

        Command:
            `p4 edit`_.

        Args:
            filespecs: A list of `File specifications`_.
            changelist: Open the files within the specified changelist.
                If not set, the files are linked to the default changelist.
            preview: Preview the result of the operation, without
                actually changing any files or metadata.

        Returns:
            `ActionInfo` and `ActionMessage` objects. `ActionInfo` are only
                included if something unexpected happened during
                the operation.
        """
        command = ["edit"]
        if changelist:
            command += ["-c", str(changelist)]
        if preview:
            command += ["-n"]
        command += filespecs

        messages: list[ActionMessage] = []
        infos: list[ActionInfo] = []
        for data in self.run_p4(*command):
            if data["code"] == MarshalCode.INFO:
                messages.append(ActionMessage.from_info_data(data))
            else:
                infos.append(ActionInfo(**data))  # type: ignore[arg-type]
        return messages, infos

    def delete(
        self,
        filespecs: list[str],
        *,
        changelist: int | None = None,
        preview: bool = False,
    ) -> tuple[list[ActionMessage], list[ActionInfo]]:
        """Open `filespecs` in client workspace for *deletion* from the depot.

        Command:
            `p4 delete`_.

        Args:
            filespecs: A list of `File specifications`_.
            changelist: Open the files within the specified changelist.
                If not set, the files are linked to the default changelist.
            preview: Preview the result of the operation, without actually
                changing any files or metadata.

        Returns:
            `ActionInfo` and `ActionMessage` objects. `ActionInfo` are
            only included if something unexpected happened during
            the operation.

        """
        command = ["delete"]
        if changelist:
            command += ["-c", str(changelist)]
        if preview:
            command += ["-n"]
        command += filespecs

        messages: list[ActionMessage] = []
        infos: list[ActionInfo] = []
        for data in self.run_p4(*command):
            if data["code"] == MarshalCode.INFO:
                messages.append(ActionMessage.from_info_data(data))
            else:
                infos.append(ActionInfo(**data))  # type: ignore[arg-type]
        return messages, infos

    def fstat(
        self,
        filespecs: list[str],
        *,
        include_deleted: bool = False,
    ) -> Iterator[FStat]:
        """List files information.

        Local files (not in depot and not opened for ``add``) are not included.

        Command:
            `p4 fstat`_.

        Args:
            filespecs: A list of `File specifications`_.
            include_deleted: Include files with a head action of ``delete`` or
                ``move/delete``.

        Yields:
            An iterator of `FStat` objects representing the file information.

        Raises:
            CommandExecutionError: If the ``p4 fstat`` command fails.

        """
        # NOTE: not using: '-Ol': include 'fileSize' and 'digest' fields.
        command = ["fstat"]
        if not include_deleted:
            command += ["-F", "^headAction=delete ^headAction=move/delete"]
        command += filespecs

        local_paths = set()
        for data in self.run_p4(
                *command, max_severity=MessageSeverity.WARNING
        ):
            if data["code"] == "error":
                path, _, message = data["data"].rpartition(" - ")
                path, message = path.strip(), message.strip()
                if message == "no such file(s).":
                    local_paths.add(path)
                else:
                    raise CommandExecutionError(
                        data["data"], command=command, data=data
                    )
            else:
                yield FStat(**data)  # type: ignore[arg-type]

    def opened(self, changelist: str | None = None) -> Iterator[FStat]:
        """Get opened files on the current server.

        Args:
            changelist: Changelist number or None for default changelist.

        Yields:
            An iterator of `FStat` objects representing the opened files.

        """
        command = ["opened"]
        if changelist is not None:
            command += ["-c", changelist]
        else:
            command += ["-c", "default"]

        for data in self.run_p4(*command):
            yield FStat(**data)  # type: ignore[arg-type]

    def get_revisions(
        self,
        filespecs: list[str],
        *,
        long_output: bool = False,
    ) -> Iterator[list[Revision]]:
        """List **all** revisions of files matching ``filespecs``.

        Command:
            `p4 filelog`_.

        Args:
            filespecs: A list of `File specifications`_.
            long_output: List long output, with full text of each
                changelist description.

        Yields:
            Iterator for Revisions

        Warning:
            The lists are not intentionally sorted despite being *naturally*
            sorted by descending revision (highest to lowset) due to how
            p4 filelog`_ data are processed. This behavior could change in
            the future, the order is not
            guaranteed.

        """
        # NOTE: Most fields ends with the rev number, like 'foo1', other
        # indicate a relationship, like 'bar0,1'
        regex = re.compile(r"([a-zA-Z]+)([0-9]+)(?:,([0-9]+))?")

        command = ["filelog"]
        if long_output:
            command += ["-l"]
        command += filespecs

        for data in self.run_p4(*command):
            revisions: dict[int, dict[str, str]] = {}
            shared: dict[str, str] = {}

            for key, value in data.items():
                match = regex.match(key)

                if match:
                    prefix: str = match.group(1)
                    index = int(match.group(2))
                    suffix = (
                        "" if match.group(3) is None else int(match.group(3))
                    )
                    revisions.setdefault(index, {})[f"{prefix}{suffix}"] = (
                        value
                    )
                else:
                    shared[key] = value

            yield [Revision(**rev, **shared) for rev in revisions.values()]  # type: ignore[arg-type]

    def sync(self, filespecs: list[str]) -> list[Sync]:
        """Update ``filespecs`` to the client workspace.

        Args:
            filespecs: A list of `File specifications`_.

        Returns:
            List of `Sync` objects representing the synced files.

        Raises:
            CommandExecutionError: If the ``p4 sync`` command fails.

        """
        command = ["sync", *filespecs]
        output = self.run_p4(*command, max_severity=MessageSeverity.WARNING)

        result: list[Sync] = []
        for data in output:
            if data["code"] == MarshalCode.ERROR:
                _, _, message = data["data"].rpartition(" - ")
                message = message.strip()
                if message == "file(s) up-to-date.":
                    log.debug(data["data"].strip())
                    continue
                raise CommandExecutionError(
                    message, command=command, data=data
                )

            if data["code"] == MarshalCode.INFO:
                log.info(data["data"].strip())
                continue

            # NOTE: The first item contain info about total
            # file count and size.
            if not result and "totalFileCount" in data:
                total_files = int(data.pop("totalFileCount", 0))
                total_bytes = int(data.pop("totalFileSize", 0))
                log.info(
                    "Synced %s files (%s bytes)", total_files, total_bytes
                )

            result.append(Sync(**data))  # type: ignore[arg-type]

        return result

    @staticmethod
    def _parse_marshal(data: bytes) -> list[dict]:
        """Decode raw marshal bytes from ``p4 -G`` into a list of dicts.

        Returns:
            list[dict]: All records decoded from the marshal stream.

        """
        records: list[dict] = []
        stream = io.BytesIO(data)
        while True:
            try:
                record = marshal.load(stream)  # noqa: S302
            except EOFError:
                break
            if not isinstance(record, dict):
                continue
            decoded: dict = {}
            for k, v in record.items():
                key = k.decode() if isinstance(k, bytes) else k
                val = v.decode() if isinstance(v, bytes) else v
                decoded[key] = val
            records.append(decoded)
        return records

    @classmethod
    def _get_current_stream(cls) -> str | None:
        """Return the stream configured in the active client workspace."""
        try:
            records = cls.run_p4("client", "-o")
        except RuntimeError:
            return None
        return records[0].get("Stream") if records else None

    # ------------------------------------------------------------------
    # Public API  (mirrors the previous HTTP-based interface)
    # ------------------------------------------------------------------

    @classmethod
    def login(
        cls,
        host: str,
        port: int,
        username: str,
        password: str,
        workspace_name: str,
    ) -> dict:
        """Store connection parameters and authenticate with ``p4 login``.

        Args:
            host (str): Perforce server hostname.
            port (int): Perforce server port.
            username (str): Perforce username.
            password (str): Perforce password.
            workspace_name (str): Client workspace name.

        Returns:
            dict: ``{"status": "ok"}`` on success.

        Raises:
            RuntimeError: If ``p4 login`` exits with a non-zero status.

        """
        cls._connection = Connection(
            port=f"{host}:{port}",
            user=username,
            client=workspace_name,
            password=password,
        )
        env = cls._get_env()
        p4_exe = shutil.which("p4") or "p4"
        proc = subprocess.run(
            [p4_exe, "login"],
            input=f"{password}\n".encode(),
            capture_output=True,
            check=False,
            env=env,
        )
        if proc.returncode != 0:
            raise RuntimeError(proc.stderr.decode(errors="replace").strip())

        log.debug("p4 login: %s", proc.stdout.decode(errors="replace").strip())
        return {"status": "ok"}

    @classmethod
    def is_in_any_workspace(
        cls, path: str | pathlib.Path
    ) -> bool:
        """Return ``True`` if *path* is mapped under any client workspace root.

        Args:
            path: Local or depot path to test.

        Returns:
            bool: Whether the path belongs to a workspace.

        """
        try:
            records = cls.run_p4("where", str(path))
            return bool(records)
        except RuntimeError:
            return False

    @classmethod
    def sync_latest_version(
        cls, path: str | pathlib.Path
    ) -> dict:
        """Sync *path* to the head revision.

        Args:
            path: Depot or local path to sync.

        Returns:
            dict: First sync record, or ``{}``.

        """
        records = cls.run_p4("sync", str(path))
        return records[0] if records else {}

    @classmethod
    def sync_to_version(
        cls,
        path: str | pathlib.Path,
        version: int,
    ) -> dict:
        """Sync *path* to a specific changelist *version*.

        Args:
            path: Depot or local path.
            version (int): Target changelist number.

        Returns:
            dict: First sync record, or ``{}``.

        """
        records = cls.run_p4("sync", f"{path}@{version}")
        return records[0] if records else {}

    @classmethod
    def checkout(
        cls,
        path: str | pathlib.Path,
        comment: str = "",
    ) -> dict:
        """Open *path* for edit (checkout).

        Args:
            path: Local file path.
            comment: Unused; kept for API compatibility.  Files are always
                opened in the default changelist.

        Returns:
            dict: First record from ``p4 edit``, or ``{}``.

        """
        if comment:
            log.debug("'comment' is ignored in subprocess mode: %r", comment)
        records = cls.run_p4("edit", str(path))
        return records[0] if records else {}

    @classmethod
    def is_checkouted(
        cls, path: str | pathlib.Path
    ) -> bool:
        """Return ``True`` if *path* is currently open for edit or add.

        Args:
            path: Local or depot file path.

        Returns:
            bool: Whether the file is open in the workspace.

        """
        try:
            records = cls.run_p4("fstat", str(path))
        except RuntimeError:
            return False
        if not records:
            return False
        return records[0].get("action", "") in {"edit", "add"}

    @classmethod
    def get_last_change_list(cls) -> dict:
        """Return the most-recently submitted changelist on the current stream.

        Returns:
            dict: Changelist record (contains at least ``"change"``),
                or ``{}``.

        """
        stream = cls._get_current_stream()
        args = ["changes", "-s", "submitted", "-m", "1"]
        if stream:
            args.append(f"{stream}/...")
        records = cls.run_p4(*args)
        return records[0] if records else {}

    def get_submitted_changes(self) -> list[ChangeInfo]:
        """Return all submitted changelists on the current stream.

        Returns:
            list[ChangeInfo]: List of changelist records.

        """
        stream = self._get_current_stream()
        args = ["changes", "-s", "submitted"]
        if stream:
            args.append(f"{stream}/...")
        records = self.changes(stream=stream, status=ChangeStatus.SUBMITTED)
        return list(records)

    @staticmethod
    def get_uncommitted_changes(client: str | None = None) -> list[ChangeInfo]:
        """Return pending (uncommitted) changelists for the current client.

        Args:
            client: Optional Perforce client name.
                If not set, the current client is used.

        Returns:
            list[ChangeInfo]: Pending changelist records, including any
                open files in the default changelist.

        """
        p4 = P4Commands()
        records = list(
            p4.changes(
                client=client,
                status=ChangeStatus.PENDING
            )
        )
        try:
            default_open = list(p4.opened())
        except RuntimeError:
            default_open = []
        if default_open:
            records.extend(default_open)
        return list(records)

    def submit_change_list(self, comment: str) -> dict:
        """Submit the pending changelist whose description matches *comment*.

        Args:
            comment (str): Description of the changelist to submit.

        Returns:
            dict: Submit result record, or ``{}``.

        Raises:
            RuntimeError: If no pending changelist with *comment* is found.

        """
        pending = self.changes(status=ChangeStatus.PENDING, long_output=True)
        cl_number: str | None = None
        for record in pending:
            if record.description.strip() == comment.strip():
                cl_number = str(record.change)
                break

        if cl_number is None:
            msg = f"No pending changelist found with description: {comment!r}"
            raise RuntimeError(msg)
        records = self.run_p4("submit", "-c", cl_number)
        return records[0] if records else {}

    @classmethod
    def submit_default_changelist(cls, comment: str) -> dict:
        """Submit the default changelist using *comment* as the description.

        Args:
            comment (str): Submit description.

        Returns:
            dict: Submit result record, or ``{}``.

        """
        records = cls.run_p4("submit", "-d", comment)
        return records[0] if records else {}

    @classmethod
    def revert(cls, path: str | pathlib.Path) -> dict:
        """Revert *path* to the depot revision, discarding local changes.

        Args:
            path: Local or depot file path.

        Returns:
            dict: First revert record, or ``{}``.

        """
        records = cls.run_p4("revert", str(path))
        return records[0] if records else {}

    @classmethod
    def exists_on_server(
        cls, path: str | pathlib.Path
    ) -> bool:
        """Return ``True`` if *path* exists in the Perforce depot.

        Args:
            path: Local or depot path to check.

        Returns:
            bool: Whether the path is tracked in the depot.

        """
        try:
            records = cls.run_p4("fstat", "-m", "1", str(path))
            return bool(records)
        except RuntimeError:
            return False

    @classmethod
    def get_stream(cls, workspace_name: str) -> str:
        """Return the depot stream associated with *workspace_name*.

        Args:
            workspace_name (str): Client workspace name.

        Returns:
            str: Stream path, or ``""`` if not set.

        """
        try:
            records = cls.run_p4("client", "-o", workspace_name)
        except RuntimeError:
            return ""
        return records[0].get("Stream", "") if records else ""

    @classmethod
    def get_workspace_dir(cls, workspace_name: str) -> str:
        """Return the local root directory for *workspace_name*.

        Args:
            workspace_name (str): Client workspace name.

        Returns:
            str: Absolute path to the workspace root, or ``""`` if not found.

        """
        try:
            records = cls.run_p4("client", "-o", workspace_name)
        except RuntimeError:
            return ""
        return records[0].get("Root", "") if records else ""

    @classmethod
    def get_available_depots(cls) -> list[str]:
        """Return a list of all depot names on the server.

        Returns:
            list[str]: List of depot names.

        """
        try:
            records = cls.run_p4("depots")
        except RuntimeError:
            return []
        return [r.get("name", "") for r in records if "name" in r]

    @classmethod
    def get_available_workspaces(cls, username: str) -> list[str]:
        """Return a list of all client workspaces for *username*.

        Args:
            username (str): Perforce username.

        Returns:
            list[str]: List of workspace names.

        """
        try:
            records = cls.run_p4("clients", "-u", username)
        except RuntimeError:
            return []
        return [r.get("client", "") for r in records if "client" in r]

    @classmethod
    def get_available_streams(cls, depot: str | None = None) -> list[str]:
        """Return a list of all streams on the server.

        Args:
            depot (str | None): Optional depot name to filter streams.

        Returns:
            list[str]: List of stream paths.

        """
        if depot:
            if not depot.startswith("//"):
                depot = f"//{depot}"
            if not depot.endswith("/..."):
                depot = f"{depot}/..."
        try:
            records = cls.run_p4("streams", depot or "")
        except RuntimeError:
            return []
        return [
            r.get("Stream", "") for r in records if "Stream" in r
        ]

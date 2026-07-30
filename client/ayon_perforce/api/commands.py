"""Perforce operations via direct p4 CLI subprocess calls.

Eliminates the need for a running REST server by shelling out to the
``p4`` command-line client.  Connection parameters (port, user, client,
password) are stored as class-level state after :meth:`login` is called
and are injected into the environment for every subsequent subprocess.
"""
from __future__ import annotations

import io
import marshal
import os
import shutil
import subprocess
from logging import getLogger
from typing import TYPE_CHECKING, Union

if TYPE_CHECKING:
    import pathlib


log = getLogger(__name__)


class P4Commands:
    """Perforce stub that invokes the ``p4`` CLI directly via subprocess."""

    _p4_port: str | None = None
    _p4_user: str | None = None
    _p4_client: str | None = None
    _p4_password: str | None = None

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @classmethod
    def _get_env(cls) -> dict:
        """Return an environment dict with P4 variables set."""
        env = os.environ.copy()
        if cls._p4_port:
            env["P4PORT"] = cls._p4_port
        if cls._p4_user:
            env["P4USER"] = cls._p4_user
        if cls._p4_client:
            env["P4CLIENT"] = cls._p4_client
        if cls._p4_password:
            env["P4PASSWD"] = cls._p4_password
        return env

    @classmethod
    def run_p4(
        cls,
        *args: str,
        stdin_data: bytes | None = None,
    ) -> list[dict]:
        """Execute ``p4 -G <args>`` and return decoded marshal records.

        Args:
            *args: Arguments forwarded to ``p4``.
            stdin_data: Optional bytes written to the process stdin.

        Returns:
            list[dict]: Non-error records from the marshal output.

        Raises:
            RuntimeError: When the command exits non-zero and produced no
                successful records.

        """
        p4_exe = shutil.which("p4") or "p4"
        cmd = [p4_exe, "-G"] + [str(a) for a in args]
        log.debug("p4 cmd: %s", " ".join(cmd))
        proc = subprocess.run(
            cmd,
            input=stdin_data,
            capture_output=True,
            check=False,
            env=cls._get_env(),
        )

        all_records = cls._parse_marshal(proc.stdout)
        error_records = [r for r in all_records if r.get("code") == "error"]
        normal_records = [r for r in all_records if r.get("code") != "error"]

        if proc.returncode != 0 and not normal_records:
            if error_records:
                msg = "; ".join(
                    r.get("data", "unknown p4 error") for r in error_records
                )
            else:
                msg = proc.stderr.decode(errors="replace").strip()
            raise RuntimeError(
                msg or f"p4 command failed (exit {proc.returncode}): {cmd}"
            )

        return normal_records

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
                record = marshal.load(stream)  # nosec: B302 - output from local p4 subprocess
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
        cls._p4_port = f"{host}:{port}"
        cls._p4_user = username
        cls._p4_client = workspace_name
        cls._p4_password = password

        env = cls._get_env()
        p4_exe = shutil.which("p4") or "p4"
        proc = subprocess.run(
            [p4_exe, "login"],
            input=(f"{password}\n").encode(),
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
        cls, path: Union[str, pathlib.Path]
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
    def add(
        cls,
        path: Union[str, pathlib.Path],
        comment: str = "",
    ) -> dict:
        """Mark *path* for add in Perforce.

        Args:
            path: Local file path to add.
            comment: Unused; kept for API compatibility.  Files are always
                added to the default changelist.

        Returns:
            dict: First record returned by ``p4 add``, or ``{}``.

        """
        if comment:
            log.debug("'comment' is ignored in subprocess mode: %r", comment)
        records = cls.run_p4("add", str(path))
        return records[0] if records else {}

    @classmethod
    def sync_latest_version(
        cls, path: Union[str, pathlib.Path]
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
        path: Union[str, pathlib.Path],
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
        path: Union[str, pathlib.Path],
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
        cls, path: Union[str, pathlib.Path]
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

    @classmethod
    def get_changes(cls) -> list[dict]:
        """Return all submitted changelists on the current stream.

        Returns:
            list[dict]: List of changelist records.

        """
        stream = cls._get_current_stream()
        args = ["changes", "-s", "submitted"]
        if stream:
            args.append(f"{stream}/...")
        records = cls.run_p4(*args)
        if not isinstance(records, list):
            return [records] if records else []
        return records

    @classmethod
    def get_uncommitted_changes(cls) -> list:
        """Return pending (uncommitted) changelists for the current client.

        Returns:
            list: Pending changelist records, including any open files in
                the default changelist.

        """
        args = ["changes", "-s", "pending"]
        if cls._p4_client:
            args += ["-c", cls._p4_client]
        records = cls.run_p4(*args)
        try:
            default_open = cls.run_p4("opened", "-c", "default")
        except RuntimeError:
            default_open = []
        if default_open:
            records.extend(default_open)
        return records

    @classmethod
    def submit_change_list(cls, comment: str) -> dict:
        """Submit the pending changelist whose description matches *comment*.

        Args:
            comment (str): Description of the changelist to submit.

        Returns:
            dict: Submit result record, or ``{}``.

        Raises:
            RuntimeError: If no pending changelist with *comment* is found.

        """
        pending = cls.run_p4("changes", "-s", "pending", "-l")
        cl_number: str | None = None
        for record in pending:
            if record.get("desc", "").strip() == comment.strip():
                cl_number = record["change"]
                break

        if cl_number is None:
            msg = f"No pending changelist found with description: {comment!r}"
            raise RuntimeError(msg)
        records = cls.run_p4("submit", "-c", cl_number)
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
    def revert(cls, path: Union[str, pathlib.Path]) -> dict:
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
        cls, path: Union[str, pathlib.Path]
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
        return [r.get("Stream", "") for r in records if "Stream" in r]
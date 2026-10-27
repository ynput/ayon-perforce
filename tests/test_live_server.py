"""Read-only tests against a real Perforce server.

Deselected by default, run them with::

    pytest -m live

Connection is read from the environment, missing variables are filled
from ``tests/.env`` (``P4PORT``, ``P4USER``, ``P4CLIENT``, ``P4PASSWD``).
``P4CLIENT`` should be an existing workspace. Optionally set
``AYON_P4_LIVE_FILESPEC`` (default ``//...``) to limit which files
``fstat``/``filelog`` inspect.

Only read-only commands are used, records returned by the server must
parse into the models.
"""

from __future__ import annotations

import os
import pathlib
from typing import TYPE_CHECKING

import pytest
from ayon_perforce.api.commands import P4Commands
from ayon_perforce.api.models import (
    ChangeInfo,
    ChangeStatus,
    Client,
    MessageSeverity,
    UncommittedChange,
    User,
)

if TYPE_CHECKING:
    from collections.abc import Iterator

pytestmark = pytest.mark.live

FILESPEC = os.getenv("AYON_P4_LIVE_FILESPEC", "//...")
LIMIT = "20"


@pytest.fixture(scope="module")
def p4(live_env: dict[str, str]) -> Iterator[P4Commands]:
    """Log in with the live connection.

    Yields:
        Commands instance.

    """
    host, _, port = live_env["P4PORT"].rpartition(":")
    P4Commands.login(
        host=host,
        port=int(port),
        username=live_env.get("P4USER", ""),
        password=live_env.get("P4PASSWD"),
        workspace_name=live_env.get("P4CLIENT", ""),
    )
    yield P4Commands()
    P4Commands._connection = None  # ruff: ignore[private-member-access]


@pytest.fixture(scope="module")
def client(p4: P4Commands) -> Client:
    """Return the current workspace."""
    return p4.get_client()


@pytest.fixture(scope="module")
def depot_files(p4: P4Commands) -> list[str]:  # ruff: ignore[unused-function-argument]
    """Return a few depot files matching `FILESPEC`."""
    records = P4Commands.run_p4("files", "-m", LIMIT, FILESPEC)
    files = [record["depotFile"] for record in records]
    if not files:
        pytest.skip(f"No files in {FILESPEC}")
    return files


def test_live_login_ticket(p4: P4Commands) -> None:  # ruff: ignore[unused-function-argument]
    """Session has a valid ticket after login."""
    assert P4Commands.run_p4("login", "-s")


def test_live_user(p4: P4Commands) -> None:
    """Current user spec parses."""
    user_name = P4Commands.run_p4("info")[0]["userName"]
    user = p4.get_user(user_name)
    assert isinstance(user, User)
    assert user.name == user_name


def test_live_client(client: Client, live_env: dict[str, str]) -> None:
    """Current workspace spec parses."""
    if "P4CLIENT" in live_env:
        assert client.name == live_env["P4CLIENT"]
    assert client.root


def test_live_workspace_helpers(client: Client) -> None:
    """Workspace root and stream helpers match the client spec."""
    root = P4Commands.get_workspace_dir(client.name)
    assert pathlib.Path(root) == pathlib.Path(client.root)
    assert P4Commands.get_stream(client.name) == (client.stream or "")


def test_live_available_lists(
    client: Client, live_env: dict[str, str]
) -> None:
    """Depots, workspaces and streams of the workspace are listed."""
    if live_env.get("P4USER"):
        workspaces = P4Commands.get_available_workspaces(live_env["P4USER"])
        assert client.name in workspaces
    depots = P4Commands.get_available_depots()
    assert depots
    if client.stream:
        depot = client.stream.split("/")[2]
        assert depot in depots
        assert client.stream in P4Commands.get_available_streams(depot)


@pytest.mark.parametrize(
    "status", [ChangeStatus.SUBMITTED, ChangeStatus.PENDING]
)
def test_live_changes_on_stream(
    p4: P4Commands, client: Client, status: ChangeStatus
) -> None:
    """Changes filtered by stream and status parse.

    Also checks the order of options and file spec is accepted by p4.
    """
    changes = list(p4.changes(stream=client.stream, status=status))
    assert all(change.status == status for change in changes)


def test_live_changes(p4: P4Commands) -> None:
    """Recent changes and their specs parse."""
    for data in P4Commands.run_p4("changes", "-l", "-m", LIMIT):
        change_info = ChangeInfo(**data)
        assert p4.get_change(change_info.change).change == change_info.change


def test_live_last_change(p4: P4Commands) -> None:
    """Last submitted change on the current stream is returned."""
    last = P4Commands.get_last_change_list()
    submitted = p4.get_submitted_changes()
    if not submitted:
        assert last == {}
        return
    assert int(last["change"]) == max(change.change for change in submitted)


def test_live_fstat(p4: P4Commands, depot_files: list[str]) -> None:
    """Fstat of files parses (incl. deleted)."""
    for depot_file in depot_files:
        list(p4.fstat([depot_file], include_deleted=True))


def test_live_filelog(p4: P4Commands, depot_files: list[str]) -> None:
    """Filelog of files parses."""
    for revisions in p4.get_revisions(depot_files):
        assert revisions


def test_live_opened(p4: P4Commands, client: Client) -> None:
    """Opened files of the default and all changelists parse."""
    default = list(p4.opened())
    every = list(p4.opened(client=client.name, all_changelists=True))
    assert len(every) >= len(default)


def test_live_opened_of_workspace(client: Client) -> None:
    """Opened files check used by workspace switch doesn't fail.

    Workspace without opened files returns a warning only.
    """
    records = P4Commands.run_p4(
        "opened", "-C", client.name, "-m", "1",
        max_severity=MessageSeverity.WARNING,
    )
    assert len(records) <= 1


def test_live_existing_client_spec(client: Client) -> None:
    """Existing workspace spec has `Update`, a missing one doesn't."""
    existing = P4Commands.run_p4("client", "-o", client.name)[0]
    assert "Update" in existing
    missing = P4Commands.run_p4("client", "-o", "ayon_missing_ws_xyz")[0]
    assert "Update" not in missing


def test_live_uncommitted_changes(client: Client) -> None:
    """Uncommitted work of the workspace is listed as one row type."""
    rows = P4Commands.get_uncommitted_changes(client.name)
    assert all(isinstance(row, UncommittedChange) for row in rows)

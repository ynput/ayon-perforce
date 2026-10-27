"""Tests for `ayon_perforce.lib` workspace handling with faked ``p4``."""

from __future__ import annotations

from typing import TYPE_CHECKING

import p4_records as rec
import pytest

if TYPE_CHECKING:
    import types

    from conftest import FakeP4

OWNER = "artist"
STREAM_DEPOT = {"code": "stat", "name": "stream_depot", "type": "stream"}
LOCAL_DEPOT = {"code": "stat", "name": "depot", "type": "local"}
STREAM_RECORD = {"code": "stat", "Stream": rec.STREAM, "Type": "mainline"}
CLIENT_SAVED = {"code": "info", "data": f"Client {rec.WS} saved.", "level": 0}


def make_workspace(
    lib: types.ModuleType, **kwargs: object
) -> object:
    """Return stream workspace, `kwargs` override the defaults.

    Returns:
        P4Workspace instance.

    """
    values = {
        "name": rec.WS,
        "owner": OWNER,
        "host": "WORKSTATION",
        "root": rec.ROOT,
        "depot": "stream_depot",
        "stream": rec.STREAM,
        "options": ["allwrite", "clobber", "rmdir"],
    }
    values.update(kwargs)
    return lib.P4Workspace(**values)


# ---------------------------------------------------------------------------
# P4Workspace construction
# ---------------------------------------------------------------------------
def test_relative_stream_is_prefixed(p4lib: types.ModuleType) -> None:
    """Stream name without depot becomes full stream path."""
    workspace = make_workspace(p4lib, stream="main")
    assert workspace.stream == rec.STREAM


def test_from_dict_stream_client(p4lib: types.ModuleType) -> None:
    """Client spec is converted, depot is taken from the stream."""
    workspace = p4lib.P4Workspace.from_dict(rec.CAPTURED_CLIENT_SPEC_STREAM)
    assert workspace.name == rec.WS
    assert workspace.owner == "artist"
    assert workspace.root == rec.ROOT
    assert workspace.depot == "stream_depot"
    assert workspace.stream == rec.STREAM
    assert workspace.options == [
        "noallwrite", "noclobber", "nocompress", "unlocked", "nomodtime",
        "rmdir",
    ]


def test_from_dict_missing_keys_raises(p4lib: types.ModuleType) -> None:
    """Spec without owner or root is rejected."""
    with pytest.raises(p4lib.P4WorkspaceError):
        p4lib.P4Workspace.from_dict({"Client": rec.WS})


def test_generate_spec_stream(p4lib: types.ModuleType) -> None:
    """Stream workspace spec has stream and options, no view."""
    spec = make_workspace(p4lib).generate_spec()
    assert spec["Client"] == rec.WS
    assert spec["Stream"] == rec.STREAM
    assert spec["Options"] == "allwrite clobber rmdir"
    assert "View0" not in spec


def test_generate_spec_classic(p4lib: types.ModuleType) -> None:
    """Classic workspace maps the whole depot to the workspace."""
    spec = make_workspace(p4lib, stream=None, depot="depot").generate_spec()
    assert "Stream" not in spec
    assert spec["View0"] == f"//depot/... //{rec.WS}/..."


# ---------------------------------------------------------------------------
# validation
# ---------------------------------------------------------------------------
def test_validate_missing_depot_lists_available(
    p4lib: types.ModuleType, fake_p4: FakeP4
) -> None:
    """Missing depot is reported before the stream, with available depots."""
    fake_p4.respond(LOCAL_DEPOT)
    workspace = make_workspace(p4lib)
    with pytest.raises(RuntimeError, match=r"Depot `stream_depot`.*: depot"):
        workspace._validate_stream_and_depot()  # ruff: ignore[private-member-access]
    assert fake_p4.args == [["depots"]]


def test_validate_requires_stream_depot(
    p4lib: types.ModuleType, fake_p4: FakeP4
) -> None:
    """Stream workspace on non-stream depot is rejected."""
    fake_p4.respond({**STREAM_DEPOT, "type": "local"})
    workspace = make_workspace(p4lib)
    with pytest.raises(RuntimeError, match="not a stream depot"):
        workspace._validate_stream_and_depot()  # ruff: ignore[private-member-access]


def test_validate_missing_stream_lists_available(
    p4lib: types.ModuleType, fake_p4: FakeP4
) -> None:
    """Missing stream error lists available streams."""
    fake_p4.respond(STREAM_DEPOT)
    fake_p4.respond(STREAM_RECORD)
    workspace = make_workspace(p4lib, stream="//stream_depot/other")
    with pytest.raises(RuntimeError, match=f"other`.*{rec.STREAM}"):
        workspace._validate_stream_and_depot()  # ruff: ignore[private-member-access]
    assert fake_p4.args[1] == ["streams", "//stream_depot/..."]


def test_validate_classic_workspace_skips_streams(
    p4lib: types.ModuleType, fake_p4: FakeP4
) -> None:
    """Classic workspace only needs the depot."""
    fake_p4.respond(LOCAL_DEPOT)
    workspace = make_workspace(p4lib, stream=None, depot="depot")
    workspace._validate_stream_and_depot()  # ruff: ignore[private-member-access]
    assert fake_p4.args == [["depots"]]


def test_validate_propagates_p4_errors(
    p4lib: types.ModuleType, fake_p4: FakeP4
) -> None:
    """Login failure is not reported as a missing depot."""
    from ayon_perforce.api.exceptions import CommandExecutionError

    fake_p4.respond(returncode=1, stderr=b"Perforce password invalid")
    with pytest.raises(CommandExecutionError, match="password"):
        make_workspace(p4lib)._validate_stream_and_depot()  # ruff: ignore[private-member-access]


# ---------------------------------------------------------------------------
# switch
# ---------------------------------------------------------------------------
def test_switch_creates_new_workspace(
    p4lib: types.ModuleType, fake_p4: FakeP4
) -> None:
    """New workspace is created without checking its opened files."""
    from ayon_perforce.api.commands import P4Commands

    fake_p4.respond(rec.DOCUMENTED_CLIENT_SPEC_NEW)
    fake_p4.respond(STREAM_DEPOT)
    fake_p4.respond(STREAM_RECORD)
    fake_p4.respond(CLIENT_SAVED)
    fake_p4.respond(rec.CAPTURED_CLIENT_SPEC_STREAM)

    workspace = make_workspace(p4lib)
    workspace.switch(sync=False, persist=False)

    assert fake_p4.args == [
        ["client", "-o", rec.WS],
        ["depots"],
        ["streams", "//stream_depot/..."],
        ["client", "-i"],
        ["client", "-o", rec.WS],
    ]
    spec = fake_p4.calls[3]["stdin"]
    assert spec["Options"] == "allwrite clobber rmdir"
    assert spec["Description"] == "Created by Ayon Launcher Hook"
    connection = P4Commands._connection  # ruff: ignore[private-member-access]
    assert connection is not None
    assert connection.client == rec.WS


def test_switch_persist_sets_p4_client(
    p4lib: types.ModuleType, fake_p4: FakeP4
) -> None:
    """With ``persist`` the workspace is stored by ``p4 set``."""
    fake_p4.respond(rec.DOCUMENTED_CLIENT_SPEC_NEW)
    fake_p4.respond(STREAM_DEPOT)
    fake_p4.respond(STREAM_RECORD)
    fake_p4.respond(CLIENT_SAVED)
    fake_p4.respond()  # p4 set
    fake_p4.respond(rec.CAPTURED_CLIENT_SPEC_STREAM)

    make_workspace(p4lib).switch(sync=False)

    assert ["set", f"P4CLIENT={rec.WS}"] in fake_p4.args


def test_switch_existing_with_opened_files_on_other_stream(
    p4lib: types.ModuleType, fake_p4: FakeP4
) -> None:
    """Opened files block switching an existing workspace to new stream."""
    other_stream = "//stream_depot/other"
    fake_p4.respond(rec.CAPTURED_CLIENT_SPEC_STREAM)
    fake_p4.respond(STREAM_DEPOT)
    fake_p4.respond({**STREAM_RECORD, "Stream": other_stream})
    fake_p4.respond(rec.DOCUMENTED_OPENED_DEFAULT)

    workspace = make_workspace(p4lib, stream=other_stream)
    with pytest.raises(RuntimeError, match="opened files"):
        workspace.switch(sync=False, persist=False)
    assert fake_p4.args[3] == ["opened", "-C", rec.WS, "-m", "1"]
    assert ["client", "-i"] not in fake_p4.args


def test_switch_existing_to_other_stream_without_opened_files(
    p4lib: types.ModuleType, fake_p4: FakeP4
) -> None:
    """Existing workspace without opened files is moved to new stream."""
    other_stream = "//stream_depot/other"
    fake_p4.respond(rec.CAPTURED_CLIENT_SPEC_STREAM)
    fake_p4.respond(STREAM_DEPOT)
    fake_p4.respond({**STREAM_RECORD, "Stream": other_stream})
    fake_p4.respond(
        {
            "code": "error",
            "data": "file(s) not opened on this client.\n",
            "severity": 2,
            "generic": 17,
        },
        returncode=1,
    )
    fake_p4.respond(CLIENT_SAVED)
    fake_p4.respond(rec.CAPTURED_CLIENT_SPEC_STREAM)

    make_workspace(p4lib, stream=other_stream).switch(
        sync=False, persist=False)

    spec = fake_p4.calls[4]["stdin"]
    assert spec["Stream"] == other_stream
    assert not any(key.startswith("View") for key in spec)


def test_switch_existing_without_stream_keeps_its_stream(
    p4lib: types.ModuleType, fake_p4: FakeP4
) -> None:
    """Existing stream workspace isn't converted to classic workspace."""
    fake_p4.respond(rec.CAPTURED_CLIENT_SPEC_STREAM)
    fake_p4.respond(STREAM_DEPOT)
    fake_p4.respond(STREAM_RECORD)
    fake_p4.respond(CLIENT_SAVED)
    fake_p4.respond(rec.CAPTURED_CLIENT_SPEC_STREAM)

    workspace = make_workspace(p4lib, stream=None, depot="other_depot")
    workspace.switch(sync=False, persist=False)

    assert fake_p4.calls[3]["stdin"]["Stream"] == rec.STREAM
    assert workspace.depot == "stream_depot"


def test_switch_existing_keeps_spec(
    p4lib: types.ModuleType, fake_p4: FakeP4
) -> None:
    """Existing workspace keeps its spec, only options are changed."""
    fake_p4.respond(rec.DOCUMENTED_CLIENT_SPEC_CLASSIC)
    fake_p4.respond(LOCAL_DEPOT)
    fake_p4.respond(CLIENT_SAVED)
    fake_p4.respond(rec.DOCUMENTED_CLIENT_SPEC_CLASSIC)

    make_workspace(p4lib, stream=None, depot="depot").switch(
        sync=False, persist=False)

    spec = fake_p4.calls[2]["stdin"]
    expected = {
        key: value for key, value in rec.DOCUMENTED_CLIENT_SPEC_CLASSIC.items()
        if key not in {"code", "Update", "Access"}
    }
    expected["Options"] = "allwrite clobber rmdir"
    assert spec == expected


@pytest.mark.parametrize(
    ("override", "message"),
    [
        ({"Owner": "someone"}, "belongs to `someone`"),
        ({"Host": "OTHER"}, "bound to host `OTHER`"),
        ({"Root": "D:/elsewhere"}, "has root `D:/elsewhere`"),
    ],
)
def test_switch_existing_mismatch_raises(
    p4lib: types.ModuleType,
    fake_p4: FakeP4,
    override: dict[str, str],
    message: str,
) -> None:
    """Workspace of other owner, host or root is not touched."""
    fake_p4.respond({**rec.CAPTURED_CLIENT_SPEC_STREAM, **override})
    with pytest.raises(RuntimeError, match=message):
        make_workspace(p4lib).switch(force=True, sync=False, persist=False)
    assert fake_p4.args == [["client", "-o", rec.WS]]


def test_switch_existing_equivalent_values_accepted(
    p4lib: types.ModuleType, fake_p4: FakeP4
) -> None:
    """Owner/host case and root separators don't make a mismatch."""
    import os

    existing = {
        **rec.CAPTURED_CLIENT_SPEC_STREAM,
        "Owner": OWNER.upper(),
        "Host": "workstation",
        "Root": os.path.normpath(rec.ROOT) + os.sep,
    }
    fake_p4.respond(existing)
    fake_p4.respond(STREAM_DEPOT)
    fake_p4.respond(STREAM_RECORD)
    fake_p4.respond(CLIENT_SAVED)
    fake_p4.respond(existing)

    make_workspace(p4lib).switch(sync=False, persist=False)

    assert fake_p4.args[3] == ["client", "-i"]


def test_switch_propagates_p4_errors(
    p4lib: types.ModuleType, fake_p4: FakeP4
) -> None:
    """Login failure is not mistaken for a missing workspace."""
    from ayon_perforce.api.exceptions import CommandExecutionError

    fake_p4.respond(returncode=1, stderr=b"Perforce password invalid")
    with pytest.raises(CommandExecutionError, match="password"):
        make_workspace(p4lib).switch(sync=False, persist=False)
    assert len(fake_p4.args) == 1


def test_switch_force_reverts_and_forces_spec(
    p4lib: types.ModuleType, fake_p4: FakeP4
) -> None:
    """Force reverts opened files instead of checking them."""
    fake_p4.respond(rec.CAPTURED_CLIENT_SPEC_STREAM)
    fake_p4.respond(STREAM_DEPOT)
    fake_p4.respond(STREAM_RECORD)
    fake_p4.respond()  # revert
    fake_p4.respond(CLIENT_SAVED)
    fake_p4.respond(rec.CAPTURED_CLIENT_SPEC_STREAM)

    make_workspace(p4lib).switch(force=True, sync=False, persist=False)

    assert fake_p4.args[3] == ["revert", "//..."]
    assert fake_p4.args[4] == ["client", "-i", "-f"]


def test_opened_files_of_unknown_client(
    p4lib: types.ModuleType, fake_p4: FakeP4
) -> None:
    """Opened files of a not yet created client are empty."""
    fake_p4.respond(
        {
            "code": "error",
            "data": f"Client '{rec.WS}' unknown - use 'client' command.\n",
            "severity": 3,
            "generic": 2,
        },
        returncode=1,
    )
    assert p4lib.P4Workspace.opened_files() == []


# ---------------------------------------------------------------------------
# get_latest
# ---------------------------------------------------------------------------
def test_get_latest_up_to_date(
    p4lib: types.ModuleType, fake_p4: FakeP4
) -> None:
    """Up-to-date warning (even with exit code 1) skips the sync."""
    fake_p4.respond(rec.CAPTURED_SYNC_UP_TO_DATE_WARNING, returncode=1)
    make_workspace(p4lib).get_latest()
    assert fake_p4.args == [["sync", "-n", f"{rec.ROOT}/..."]]


def test_get_latest_syncs(p4lib: types.ModuleType, fake_p4: FakeP4) -> None:
    """Pending changes are synced."""
    fake_p4.respond(rec.DOCUMENTED_SYNC_FIRST, rec.DOCUMENTED_SYNC_NEXT)
    fake_p4.respond(rec.DOCUMENTED_SYNC_FIRST, rec.DOCUMENTED_SYNC_NEXT)
    make_workspace(p4lib).get_latest()
    assert fake_p4.args[1] == ["sync", f"{rec.ROOT}/..."]


def test_get_latest_force_cleans(
    p4lib: types.ModuleType, fake_p4: FakeP4
) -> None:
    """Forced sync is followed by clean preview."""
    fake_p4.respond(rec.DOCUMENTED_SYNC_FIRST)
    fake_p4.respond(rec.DOCUMENTED_SYNC_FIRST)
    fake_p4.respond()
    make_workspace(p4lib).get_latest(force=True)
    assert fake_p4.args[1:] == [
        ["sync", "-f", f"{rec.ROOT}/..."],
        ["clean", "-n", f"{rec.ROOT}/..."],
    ]


def test_get_latest_failure_raises(
    p4lib: types.ModuleType, fake_p4: FakeP4
) -> None:
    """Failed sync is raised with the workspace name."""
    fake_p4.respond(rec.DOCUMENTED_SYNC_FIRST)
    fake_p4.respond(
        rec.DOCUMENTED_SYNC_FIRST,
        rec.DOCUMENTED_SYNC_CANT_CLOBBER_ERROR,
        returncode=1,
    )
    with pytest.raises(RuntimeError, match=f"`{rec.WS}`.*clobber"):
        make_workspace(p4lib).get_latest()


# ---------------------------------------------------------------------------
# local login
# ---------------------------------------------------------------------------
def test_local_login_falls_back_to_p4_info(
    p4lib: types.ModuleType, fake_p4: FakeP4
) -> None:
    """Without stored login the p4 user is used, without password."""
    fake_p4.respond({"code": "stat", "userName": "p4_user"})
    assert p4lib.get_local_login() == ("p4_user", None)


def test_local_login_roundtrip(
    p4lib: types.ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Saved login is returned and cleared."""
    monkeypatch.setenv("P4USER", "")
    p4lib.save_local_login("artist", "secret")
    assert p4lib.get_local_login() == ("artist", "secret")
    assert p4lib.environ["P4USER"] == "artist"

    p4lib.clear_local_login()
    assert not p4lib.environ["P4USER"]

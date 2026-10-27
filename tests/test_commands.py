"""Tests for `P4Commands` with a faked ``p4 -G`` subprocess."""

from __future__ import annotations

from typing import TYPE_CHECKING

import p4_records as rec
import pytest
from ayon_perforce.api.commands import P4Commands
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
    MessageSeverity,
    Revision,
    Sync,
    UncommittedChange,
    User,
)

if TYPE_CHECKING:
    from conftest import FakeP4


# ---------------------------------------------------------------------------
# run_p4 / marshal handling
# ---------------------------------------------------------------------------
def test_run_p4_returns_records(fake_p4: FakeP4) -> None:
    """Records are decoded to str keys/values."""
    fake_p4.respond(rec.CAPTURED_USER_SPEC)
    assert P4Commands.run_p4("user", "-o") == [rec.CAPTURED_USER_SPEC]
    assert fake_p4.args == [["user", "-o"]]


def test_run_p4_passes_stdin_spec(fake_p4: FakeP4) -> None:
    """Spec dict is marshaled to stdin with plain str values."""

    class StrSubclass(str):  # ruff: ignore[subclass-builtin]
        __slots__ = ()

    fake_p4.respond()
    P4Commands.run_p4(
        "change", "-i", stdin_data={"Description": StrSubclass("x")}
    )
    assert fake_p4.calls[0]["stdin"] == {"Description": "x"}


def test_run_p4_sets_connection_env(
    fake_p4: FakeP4, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Connection values are exported as P4 env variables."""
    monkeypatch.setattr(
        P4Commands,
        "_connection",
        Connection(
            port="host:1666",
            user="artist",
            client="ws",
            password="pw",  # ruff: ignore[hardcoded-password-func-arg]
        ),
    )
    fake_p4.respond()
    P4Commands.run_p4("info")
    env = fake_p4.calls[0]["env"]
    assert env["P4PORT"] == "host:1666"
    assert env["P4USER"] == "artist"
    assert env["P4CLIENT"] == "ws"
    assert env["P4PASSWD"] == "pw"


def test_run_p4_drops_warnings_on_success(fake_p4: FakeP4) -> None:
    """Warning records of a successful command are not returned."""
    fake_p4.respond(rec.CAPTURED_SYNC_UP_TO_DATE_WARNING)
    assert P4Commands.run_p4("sync") == []


def test_run_p4_failure_raises_with_error_data(fake_p4: FakeP4) -> None:
    """Failed command raises with p4 error message and record."""
    fake_p4.respond(
        rec.DOCUMENTED_CHANGE_UNKNOWN_ERROR, returncode=1, stderr=b""
    )
    with pytest.raises(CommandExecutionError) as exc_info:
        P4Commands.run_p4("change", "-o", "99999")
    assert "Change 99999 unknown." in str(exc_info.value)
    assert exc_info.value.data == rec.DOCUMENTED_CHANGE_UNKNOWN_ERROR


def test_run_p4_partial_failure_raises(fake_p4: FakeP4) -> None:
    """Failed record raises even if other records succeeded."""
    fake_p4.respond(
        rec.DOCUMENTED_SYNC_FIRST,
        rec.DOCUMENTED_SYNC_CANT_CLOBBER_ERROR,
        returncode=1,
    )
    with pytest.raises(CommandExecutionError, match="clobber") as exc_info:
        P4Commands.run_p4("sync", max_severity=MessageSeverity.WARNING)
    assert exc_info.value.data == rec.DOCUMENTED_SYNC_CANT_CLOBBER_ERROR


def test_run_p4_failed_record_with_zero_exit_code_raises(
    fake_p4: FakeP4,
) -> None:
    """Failed record raises regardless of the exit code."""
    fake_p4.respond(
        {"code": "info", "data": "Submitting change 5.", "level": 0},
        rec.DOCUMENTED_CHANGE_UNKNOWN_ERROR,
    )
    with pytest.raises(CommandExecutionError, match="Change 99999"):
        P4Commands.run_p4("submit", "-c", "5")


def test_run_p4_failure_without_records_uses_stderr(fake_p4: FakeP4) -> None:
    """Without marshaled records stderr becomes the message."""
    fake_p4.respond(returncode=1, stderr=b"Connect to server failed")
    with pytest.raises(CommandExecutionError, match="Connect to server"):
        P4Commands.run_p4("info")


def test_parse_marshal_non_utf8() -> None:
    """Non UTF-8 values fall back to cp1252."""
    import marshal

    data = marshal.dumps({
        b"code": b"stat",
        b"desc": "caf\xe9".encode("cp1252"),
    })
    assert P4Commands._parse_marshal(data) == [  # ruff: ignore[private-member-access]
        {"code": "stat", "desc": "caf\xe9"}
    ]


def test_extract_indexed_values() -> None:
    """Indexed keys are popped in order."""
    data = {"Files0": "a", "Files1": "b", "Files3": "d", "Other": "x"}
    assert P4Commands._extract_indexed_values(data, "Files") == ["a", "b"]  # ruff: ignore[private-member-access]
    assert data == {"Files3": "d", "Other": "x"}


# ---------------------------------------------------------------------------
# user / client
# ---------------------------------------------------------------------------
def test_get_user(fake_p4: FakeP4) -> None:
    """User spec is returned as `User`."""
    fake_p4.respond(rec.CAPTURED_USER_SPEC)
    user = P4Commands().get_user("artist")
    assert isinstance(user, User)
    assert user.name == "artist"


def test_get_user_with_reviews(fake_p4: FakeP4) -> None:
    """User spec with ``Reviews#`` and ``JobView`` is supported."""
    fake_p4.respond(rec.DOCUMENTED_USER_SPEC_WITH_REVIEWS)
    assert isinstance(P4Commands().get_user("artist"), User)


def test_get_user_not_found(fake_p4: FakeP4) -> None:
    """Template spec of missing user raises `UserNotFoundError`."""
    fake_p4.respond(rec.DOCUMENTED_USER_SPEC_MISSING)
    with pytest.raises(UserNotFoundError):
        P4Commands().get_user("nobody")


@pytest.mark.parametrize(
    "record",
    [
        rec.CAPTURED_CLIENT_SPEC_STREAM,
        rec.DOCUMENTED_CLIENT_SPEC_CLASSIC,
        rec.DOCUMENTED_CLIENT_SPEC_STREAM_AT_CHANGE,
    ],
    ids=["stream", "classic", "stream-at-change"],
)
def test_get_client(fake_p4: FakeP4, record: dict) -> None:
    """Client spec is returned as `Client`."""
    fake_p4.respond(record)
    client = P4Commands().get_client(rec.WS)
    assert isinstance(client, Client)
    assert client.name == rec.WS
    assert fake_p4.args == [["client", "-o", rec.WS]]


def test_get_current_client_not_created_yet(fake_p4: FakeP4) -> None:
    """Current client template (no Update/Access) is still returned."""
    fake_p4.respond(rec.DOCUMENTED_CLIENT_SPEC_NEW)
    assert isinstance(P4Commands().get_client(), Client)


def test_get_client_not_found(fake_p4: FakeP4) -> None:
    """Template spec of missing client raises `ClientNotFoundError`."""
    fake_p4.respond(rec.DOCUMENTED_CLIENT_SPEC_NEW)
    with pytest.raises(ClientNotFoundError):
        P4Commands().get_client("missing")


# ---------------------------------------------------------------------------
# change / changes
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "record",
    [
        rec.DOCUMENTED_CHANGE_SPEC_PENDING,
        rec.DOCUMENTED_CHANGE_SPEC_PENDING_WITH_JOBS,
        rec.DOCUMENTED_CHANGE_SPEC_SUBMITTED,
        rec.DOCUMENTED_CHANGE_SPEC_SHELVED,
    ],
    ids=["pending", "pending-jobs", "submitted", "shelved"],
)
def test_get_change(fake_p4: FakeP4, record: dict) -> None:
    """Change spec (with indexed Files/Jobs) is returned as `Change`."""
    fake_p4.respond(record)
    change = P4Commands().get_change(int(record["Change"]))
    assert isinstance(change, Change)
    assert change.files == [
        v for k, v in record.items() if k.startswith("Files")
    ]


def test_get_change_unknown(fake_p4: FakeP4) -> None:
    """Unknown changelist raises `ChangeUnknownError`."""
    fake_p4.respond(
        rec.DOCUMENTED_CHANGE_UNKNOWN_ERROR, returncode=1, stderr=b""
    )
    with pytest.raises(ChangeUnknownError):
        P4Commands().get_change(99999)


def test_create_changelist(fake_p4: FakeP4) -> None:
    """New changelist spec is submitted without files, result is parsed."""
    fake_p4.respond(rec.DOCUMENTED_CHANGE_SPEC_NEW)
    fake_p4.respond({
        "code": "info",
        "data": "Change 130 created.",
        "level": 0,
    })
    fake_p4.respond({
        **rec.DOCUMENTED_CHANGE_SPEC_PENDING,
        "Description": "My change\n",
    })

    info = P4Commands().create_changelist("My change")

    assert isinstance(info, ChangeInfo)
    assert info.change == 130
    assert info.status == ChangeStatus.PENDING
    assert fake_p4.args[-1] == ["change", "-o", "130"]
    stdin = fake_p4.calls[1]["stdin"]
    assert stdin["Description"] == "My change"
    assert not any(key.startswith("Files") for key in stdin)


def test_create_changelist_unparsed_output_falls_back(
    fake_p4: FakeP4,
) -> None:
    """Latest changelist of the user is used when output is not parsed."""
    fake_p4.respond(rec.DOCUMENTED_CHANGE_SPEC_NEW)
    fake_p4.respond({"code": "info", "data": "Unexpected.", "level": 0})
    fake_p4.respond({**rec.CAPTURED_CHANGES_PENDING, "change": "130"})

    info = P4Commands().create_changelist("My change")

    assert info.change == 130
    assert fake_p4.args[-1] == ["changes", "--me", "-m", "1", "-l"]


@pytest.mark.parametrize(
    "record",
    [
        rec.CAPTURED_CHANGES_SUBMITTED,
        rec.CAPTURED_CHANGES_PENDING,
        rec.DOCUMENTED_CHANGES_PENDING_SHELVED_FILES,
        rec.DOCUMENTED_CHANGES_RESTRICTED,
        rec.DOCUMENTED_CHANGES_RENUMBERED,
    ],
    ids=["submitted", "pending", "shelved-files", "restricted", "renumbered"],
)
def test_changes(fake_p4: FakeP4, record: dict) -> None:
    """``p4 changes`` records are returned as `ChangeInfo`."""
    fake_p4.respond(record)
    result = list(P4Commands().changes())
    assert len(result) == 1
    assert isinstance(result[0], ChangeInfo)


def test_changes_arguments(fake_p4: FakeP4) -> None:
    """Filters are translated to ``p4 changes`` arguments."""
    fake_p4.respond()
    list(
        P4Commands().changes(
            user="artist",
            stream=rec.STREAM,
            client=rec.WS,
            status=ChangeStatus.PENDING,
            long_output=True,
        )
    )
    assert fake_p4.args == [
        [
            "changes",
            "-u",
            "artist",
            "-c",
            rec.WS,
            "-s",
            "pending",
            "-l",
            f"{rec.STREAM}/...",
        ]
    ]


def test_submit_change_list_matches_description(fake_p4: FakeP4) -> None:
    """Pending changelist is found by description and submitted."""
    fake_p4.respond(
        {**rec.CAPTURED_CHANGES_PENDING, "change": "7", "desc": "other\n"},
        {**rec.CAPTURED_CHANGES_PENDING, "change": "8", "desc": "mine\n"},
    )
    fake_p4.respond({"code": "stat", "submittedChange": "9"})

    P4Commands().submit_change_list("mine")

    assert fake_p4.args[1] == ["submit", "-c", "8"]


def test_submit_change_list_missing(fake_p4: FakeP4) -> None:
    """Missing description raises ``RuntimeError``."""
    fake_p4.respond(rec.CAPTURED_CHANGES_PENDING)
    with pytest.raises(RuntimeError):
        P4Commands().submit_change_list("not there")


def test_get_uncommitted_changes(fake_p4: FakeP4) -> None:
    """Opened files and pending changelists without files are combined."""
    empty_pending = {**rec.CAPTURED_CHANGES_PENDING, "change": "103"}
    fake_p4.respond(
        {**rec.CAPTURED_CHANGES_PENDING, "change": "102"}, empty_pending
    )
    fake_p4.respond(rec.DOCUMENTED_OPENED_DEFAULT, rec.CAPTURED_OPENED_ADD)
    result = P4Commands.get_uncommitted_changes(rec.WS)

    assert fake_p4.args[1] == ["opened", "-C", rec.WS]
    assert all(isinstance(item, UncommittedChange) for item in result)
    rows = [(item.change, item.action, item.client_file) for item in result]
    assert rows == [
        ("default", "edit", f"//{rec.WS}/a.txt"),
        (102, "add", f"//{rec.WS}/new.uasset"),
        (103, None, None),
    ]
    assert str(result[0]) == f"[edit]\t//{rec.WS}/a.txt"
    assert str(result[2]).startswith("[change 103]")


# ---------------------------------------------------------------------------
# add / edit / delete
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("method", "record"),
    [
        ("add", rec.DOCUMENTED_ADD_RESULT),
        ("edit", rec.DOCUMENTED_EDIT_RESULT),
        ("delete", rec.DOCUMENTED_DELETE_RESULT),
    ],
)
def test_action_commands(fake_p4: FakeP4, method: str, record: dict) -> None:
    """Action commands split stat and info records."""
    fake_p4.respond(record, rec.DOCUMENTED_INFO_ALSO_OPENED)
    messages, infos = getattr(P4Commands(), method)(
        ["a.txt"], changelist=130, preview=True
    )
    assert fake_p4.args == [[method, "-c", "130", "-n", "a.txt"]]
    assert [type(m) for m in messages] == [ActionMessage]
    assert [type(i) for i in infos] == [ActionInfo]
    assert infos[0].work_rev == int(record["workRev"])


def test_edit_not_on_client_warning(fake_p4: FakeP4) -> None:
    """Warnings (file not on client) don't produce bogus results."""
    fake_p4.respond(rec.CAPTURED_EDIT_NOT_ON_CLIENT_WARNING)
    assert P4Commands().edit(["nonexist.txt"]) == ([], [])


# ---------------------------------------------------------------------------
# fstat / opened
# ---------------------------------------------------------------------------
FSTAT_RECORDS = {
    "synced": rec.CAPTURED_FSTAT_SYNCED,
    "opened-edit": rec.DOCUMENTED_FSTAT_OPENED_FOR_EDIT,
    "opened-add": rec.DOCUMENTED_FSTAT_OPENED_FOR_ADD,
    "other-open": rec.DOCUMENTED_FSTAT_OTHER_OPEN,
    "locked-resolve": rec.DOCUMENTED_FSTAT_LOCKED_AND_RESOLVE,
    "size-digest": rec.DOCUMENTED_FSTAT_FILE_SIZE_DIGEST,
    "moved": rec.DOCUMENTED_FSTAT_MOVED,
    "shelved": rec.DOCUMENTED_FSTAT_SHELVED,
}


@pytest.mark.parametrize(
    "record", FSTAT_RECORDS.values(), ids=FSTAT_RECORDS.keys()
)
def test_fstat(fake_p4: FakeP4, record: dict) -> None:
    """``p4 fstat`` records are returned as `FStat`."""
    fake_p4.respond(record)
    result = list(P4Commands().fstat(["//..."]))
    assert [type(item) for item in result] == [FStat]
    assert result[0].depot_file == record["depotFile"]


def test_fstat_head_info(fake_p4: FakeP4) -> None:
    """Head revision data is accessible on the result."""
    fake_p4.respond(rec.CAPTURED_FSTAT_SYNCED)
    (fstat,) = P4Commands().fstat(["//..."])
    assert fstat.head is not None
    assert fstat.head.revision == 1


def test_fstat_other_open(fake_p4: FakeP4) -> None:
    """``otherOpen#``/``otherAction#``/``otherChange#`` fill others_open."""
    fake_p4.respond(rec.DOCUMENTED_FSTAT_OTHER_OPEN)
    (fstat,) = P4Commands().fstat(["//..."])
    assert fstat.others_open
    assert fstat.others_open[0].user == "bob"
    assert fstat.others_open[0].client == "bob_ws"


def test_fstat_filters_deleted(fake_p4: FakeP4) -> None:
    """Deleted files are filtered unless requested."""
    fake_p4.respond()
    fake_p4.respond()
    list(P4Commands().fstat(["//a"]))
    list(P4Commands().fstat(["//a"], include_deleted=True))
    assert fake_p4.args == [
        ["fstat", "-F", "^headAction=delete ^headAction=move/delete", "//a"],
        ["fstat", "//a"],
    ]


def test_fstat_missing_file_warning(fake_p4: FakeP4) -> None:
    """Local-only file warning is not raised."""
    fake_p4.respond(
        rec.CAPTURED_FSTAT_SYNCED, rec.DOCUMENTED_FSTAT_NO_SUCH_FILE_WARNING
    )
    result = list(P4Commands().fstat(["//..."]))
    assert len(result) == 1


@pytest.mark.parametrize(
    "record",
    [rec.DOCUMENTED_OPENED_DEFAULT, rec.DOCUMENTED_OPENED_LOCKED_MOVED],
    ids=["default", "locked-moved"],
)
def test_opened(fake_p4: FakeP4, record: dict) -> None:
    """``p4 opened`` records are returned as `FStat`."""
    fake_p4.respond(record)
    result = list(P4Commands().opened())
    assert [type(item) for item in result] == [FStat]
    assert fake_p4.args == [["opened", "-c", "default"]]


# ---------------------------------------------------------------------------
# filelog
# ---------------------------------------------------------------------------
FILELOG_RECORDS = {
    "two-revisions": rec.CAPTURED_FILELOG_TWO_REVISIONS,
    "integrated": rec.DOCUMENTED_FILELOG_INTEGRATED,
    "deleted": rec.DOCUMENTED_FILELOG_DELETED,
}


@pytest.mark.parametrize(
    "record", FILELOG_RECORDS.values(), ids=FILELOG_RECORDS.keys()
)
def test_get_revisions(fake_p4: FakeP4, record: dict) -> None:
    """``p4 filelog`` records are split to `Revision` per revision."""
    fake_p4.respond(record)
    (revisions,) = P4Commands().get_revisions(["//..."])
    expected = sorted(
        int(v)
        for k, v in record.items()
        if k.startswith("rev") and k[3:].isdigit()
    )
    assert all(isinstance(rev, Revision) for rev in revisions)
    assert sorted(rev.revision for rev in revisions) == expected
    assert {rev.depot_file for rev in revisions} == {record["depotFile"]}


def test_get_revisions_file_size(fake_p4: FakeP4) -> None:
    """``fileSize#`` is mapped to `Revision.file_size`."""
    fake_p4.respond(rec.CAPTURED_FILELOG_TWO_REVISIONS)
    (revisions,) = P4Commands().get_revisions(["//..."])
    assert {rev.file_size for rev in revisions} == {3086}


# ---------------------------------------------------------------------------
# sync
# ---------------------------------------------------------------------------
def test_sync(fake_p4: FakeP4) -> None:
    """Sync records are returned as `Sync`, totals are removed."""
    fake_p4.respond(
        rec.DOCUMENTED_SYNC_FIRST,
        rec.DOCUMENTED_SYNC_NEXT,
        rec.DOCUMENTED_SYNC_DELETED,
    )
    result = P4Commands().sync(["//..."])
    assert [type(item) for item in result] == [Sync, Sync, Sync]
    assert fake_p4.calls[0]["args"] == ["sync", "//..."]


def test_sync_up_to_date(fake_p4: FakeP4) -> None:
    """Up-to-date warning results in empty list."""
    fake_p4.respond(rec.CAPTURED_SYNC_UP_TO_DATE_WARNING)
    assert P4Commands().sync(["//..."]) == []


def test_run_p4_ignored_warning_is_returned(fake_p4: FakeP4) -> None:
    """Errors within ``max_severity`` don't raise and are returned.

    Even with non-zero exit code, so callers like `P4Commands.sync` can
    handle them.
    """
    fake_p4.respond(rec.CAPTURED_SYNC_UP_TO_DATE_WARNING, returncode=1)
    assert P4Commands.run_p4("sync", max_severity=MessageSeverity.WARNING) == [
        rec.CAPTURED_SYNC_UP_TO_DATE_WARNING
    ]


def test_run_p4_error_above_max_severity_raises(fake_p4: FakeP4) -> None:
    """Errors above ``max_severity`` fail the command with their data."""
    fake_p4.respond(
        rec.CAPTURED_SYNC_UP_TO_DATE_WARNING,
        rec.DOCUMENTED_CHANGE_UNKNOWN_ERROR,
        returncode=1,
    )
    with pytest.raises(CommandExecutionError) as exc_info:
        P4Commands.run_p4("sync", max_severity=MessageSeverity.WARNING)
    assert exc_info.value.data == rec.DOCUMENTED_CHANGE_UNKNOWN_ERROR


def test_sync_up_to_date_on_failure(fake_p4: FakeP4) -> None:
    """Up-to-date warning with non-zero exit code results in empty list."""
    fake_p4.respond(rec.CAPTURED_SYNC_UP_TO_DATE_WARNING, returncode=1)
    assert P4Commands().sync(["//..."]) == []


def test_fstat_local_file_on_failure(fake_p4: FakeP4) -> None:
    """Only local files (no such file warning) yield nothing."""
    fake_p4.respond(rec.DOCUMENTED_FSTAT_NO_SUCH_FILE_WARNING, returncode=1)
    assert list(P4Commands().fstat(["//..."])) == []


# ---------------------------------------------------------------------------
# dict-returning helpers
# ---------------------------------------------------------------------------
def test_get_workspace_dir_and_stream(fake_p4: FakeP4) -> None:
    """Workspace root and stream are read from the client spec."""
    fake_p4.respond(rec.CAPTURED_CLIENT_SPEC_STREAM)
    fake_p4.respond(rec.CAPTURED_CLIENT_SPEC_STREAM)
    assert P4Commands.get_workspace_dir(rec.WS) == rec.ROOT
    assert P4Commands.get_stream(rec.WS) == rec.STREAM


def test_get_stream_failure_returns_empty(fake_p4: FakeP4) -> None:
    """Failing ``p4 client`` returns empty stream instead of raising."""
    fake_p4.respond(returncode=1, stderr=b"Connect to server failed")
    assert not P4Commands.get_stream(rec.WS)


def test_is_checkouted(fake_p4: FakeP4) -> None:
    """File opened for edit is reported as checked out."""
    fake_p4.respond(rec.DOCUMENTED_FSTAT_OPENED_FOR_EDIT)
    fake_p4.respond(rec.CAPTURED_FSTAT_SYNCED)
    assert P4Commands.is_checkouted("a.txt") is True
    assert P4Commands.is_checkouted("a.txt") is False


def test_available_lists(fake_p4: FakeP4) -> None:
    """Depots, workspaces and streams are listed by name."""
    fake_p4.respond({"code": "stat", "name": "depot", "type": "local"})
    fake_p4.respond({"code": "stat", "client": rec.WS, "Owner": "artist"})
    fake_p4.respond({"code": "stat", "Stream": rec.STREAM, "Type": "mainline"})
    assert P4Commands.get_available_depots() == ["depot"]
    assert P4Commands.get_available_workspaces("artist") == [rec.WS]
    assert P4Commands.get_available_streams("stream_depot") == [rec.STREAM]
    assert fake_p4.args[2] == ["streams", "//stream_depot/..."]


PASSWORD_INVALID = b"Perforce password (P4PASSWD) invalid or unset."


def test_available_lists_propagate_errors(fake_p4: FakeP4) -> None:
    """Connection or login failure is not reported as missing objects."""
    fake_p4.respond(returncode=1, stderr=PASSWORD_INVALID)
    fake_p4.respond(returncode=1, stderr=PASSWORD_INVALID)
    with pytest.raises(CommandExecutionError, match="P4PASSWD"):
        P4Commands.get_available_depots()
    with pytest.raises(CommandExecutionError, match="P4PASSWD"):
        P4Commands.get_available_streams()
    assert fake_p4.args[1] == ["streams"]


# ---------------------------------------------------------------------------
# connection / login
# ---------------------------------------------------------------------------
LOGIN = {
    "host": "p4.local",
    "port": 1666,
    "username": "artist",
    "workspace_name": rec.WS,
}


def test_login_with_valid_ticket_skips_login(fake_p4: FakeP4) -> None:
    """Existing ticket is used, password isn't sent."""
    fake_p4.respond(stderr=b"")
    P4Commands.login(**LOGIN, password="pw")  # ruff: ignore[hardcoded-password-func-arg]
    assert fake_p4.args == [["login", "-s"]]
    assert P4Commands._connection == Connection(  # ruff: ignore[private-member-access]
        port="p4.local:1666", user="artist", client=rec.WS, password="pw",  # ruff: ignore[hardcoded-password-func-arg]
    )


def test_login_without_password_relies_on_ticket(fake_p4: FakeP4) -> None:
    """Missing password doesn't send "None" to ``p4 login``."""
    fake_p4.respond(returncode=1, stderr=PASSWORD_INVALID)
    P4Commands.login(**LOGIN, password=None)
    assert fake_p4.args == [["login", "-s"]]
    connection = P4Commands._connection  # ruff: ignore[private-member-access]
    assert connection is not None
    assert connection.password is None


def test_login_with_password(fake_p4: FakeP4) -> None:
    """Password is sent to ``p4 login`` when there is no ticket."""
    fake_p4.respond(returncode=1)
    fake_p4.respond()
    P4Commands.login(**LOGIN, password="pw")  # ruff: ignore[hardcoded-password-func-arg]
    assert fake_p4.args == [["login", "-s"], ["login"]]
    assert fake_p4.calls[1]["stdin"] == "pw\n"


def test_login_failure_resets_connection(fake_p4: FakeP4) -> None:
    """Failed login doesn't leave half-configured connection behind."""
    fake_p4.respond(returncode=1)
    fake_p4.respond(returncode=1, stderr=b"Password invalid.")
    with pytest.raises(RuntimeError, match="Password invalid"):
        P4Commands.login(**LOGIN, password="wrong")  # ruff: ignore[hardcoded-password-func-arg]
    assert P4Commands._connection is None  # ruff: ignore[private-member-access]


def test_set_client_is_process_local(fake_p4: FakeP4) -> None:
    """Client is passed to p4 by env, ``p4 set`` isn't used."""
    P4Commands.set_connection(host="p4.local", port=1666, username="artist")
    P4Commands.set_client("render_ws")
    fake_p4.respond()
    P4Commands.run_p4("info")
    assert fake_p4.args == [["info"]]
    env = fake_p4.calls[0]["env"]
    assert env["P4CLIENT"] == "render_ws"
    assert env["P4PORT"] == "p4.local:1666"


def test_get_submitted_changes_uses_stream(fake_p4: FakeP4) -> None:
    """Submitted changes are filtered by the current stream."""
    fake_p4.respond(rec.CAPTURED_CLIENT_SPEC_STREAM)
    fake_p4.respond(rec.CAPTURED_CHANGES_SUBMITTED)
    result = P4Commands().get_submitted_changes()
    assert [type(item) for item in result] == [ChangeInfo]
    assert fake_p4.args[1] == [
        "changes",
        "-s",
        "submitted",
        f"{rec.STREAM}/...",
    ]

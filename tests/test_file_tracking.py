"""Tests for `track_file_changes` with a faked ``p4 -G`` subprocess."""

from __future__ import annotations

from typing import TYPE_CHECKING

import p4_records as rec
import pytest
from ayon_perforce.api.file_tracking import track_file_changes

if TYPE_CHECKING:
    from pathlib import Path

    from conftest import FakeP4


CHANGE = "130"
PATH = f"{rec.ROOT}\\Content\\Ayon\\context.json"
WHERE_RESULT = {
    "code": "stat",
    "depotFile": f"{rec.STREAM}/Content/Ayon/context.json",
    "clientFile": f"//{rec.WS}/Content/Ayon/context.json",
    "path": PATH,
}


def _respond_create_changelist(fake_p4: FakeP4) -> None:
    fake_p4.respond(rec.DOCUMENTED_CHANGE_SPEC_NEW)
    fake_p4.respond({
        "code": "info",
        "data": f"Change {CHANGE} created.",
        "level": 0,
    })
    fake_p4.respond({**rec.DOCUMENTED_CHANGE_SPEC_PENDING, "Change": CHANGE})


def test_outside_workspace_does_nothing(fake_p4: FakeP4) -> None:
    """Paths not mapped to any workspace are not touched."""
    fake_p4.respond(returncode=1, stderr=b"file(s) not in client view.")

    with track_file_changes([PATH], "desc"):
        pass

    assert fake_p4.args == [["where", PATH]]


def test_existing_file_is_edited_and_submitted(fake_p4: FakeP4) -> None:
    """Synced file is opened for edit in new changelist and submitted."""
    fake_p4.respond(WHERE_RESULT)
    _respond_create_changelist(fake_p4)
    fake_p4.respond(rec.CAPTURED_FSTAT_SYNCED)
    fake_p4.respond(rec.DOCUMENTED_EDIT_RESULT)

    with track_file_changes([PATH], "desc"):
        assert fake_p4.args[-1] == ["edit", "-c", CHANGE, PATH]
        fake_p4.respond()  # revert -a
        fake_p4.respond({**rec.DOCUMENTED_OPENED_DEFAULT, "change": CHANGE})
        fake_p4.respond({"code": "stat", "submittedChange": "131"})

    assert fake_p4.args[-3:] == [
        ["revert", "-a", "-c", CHANGE, PATH],
        ["opened", "-c", CHANGE],
        ["submit", "-c", CHANGE],
    ]


def test_unchanged_file_deletes_changelist(fake_p4: FakeP4) -> None:
    """Changelist is deleted when nothing changed."""
    fake_p4.respond(WHERE_RESULT)
    _respond_create_changelist(fake_p4)
    fake_p4.respond(rec.CAPTURED_FSTAT_SYNCED)
    fake_p4.respond(rec.DOCUMENTED_EDIT_RESULT)
    fake_p4.respond()  # revert -a
    fake_p4.respond()  # opened, nothing left
    fake_p4.respond({"code": "info", "data": "deleted", "level": 0})

    with track_file_changes([PATH], "desc"):
        pass

    assert fake_p4.args[-1] == ["change", "-d", CHANGE]


def test_new_file_is_added_after_write(
    fake_p4: FakeP4, tmp_path: Path,
) -> None:
    """File not in depot is added once it exists."""
    path = str(tmp_path / "context.json")
    fake_p4.respond({**WHERE_RESULT, "path": path})
    _respond_create_changelist(fake_p4)
    fake_p4.respond(rec.DOCUMENTED_FSTAT_NO_SUCH_FILE_WARNING)

    with track_file_changes([path], "desc"):
        (tmp_path / "context.json").write_text("{}")
        fake_p4.respond(rec.DOCUMENTED_ADD_RESULT)
        fake_p4.respond({**rec.DOCUMENTED_OPENED_DEFAULT, "change": CHANGE})
        fake_p4.respond({"code": "stat", "submittedChange": "131"})

    assert fake_p4.args[-3:] == [
        ["add", "-c", CHANGE, path],
        ["opened", "-c", CHANGE],
        ["submit", "-c", CHANGE],
    ]


def test_opened_file_is_reopened(fake_p4: FakeP4) -> None:
    """File already opened by artist is moved to the changelist."""
    fake_p4.respond(WHERE_RESULT)
    _respond_create_changelist(fake_p4)
    fake_p4.respond(rec.DOCUMENTED_FSTAT_OPENED_FOR_EDIT)
    fake_p4.respond()  # reopen

    with track_file_changes([PATH], "desc"):
        fake_p4.respond({**rec.DOCUMENTED_OPENED_DEFAULT, "change": CHANGE})
        fake_p4.respond({"code": "stat", "submittedChange": "131"})

    assert ["reopen", "-c", CHANGE, PATH] in fake_p4.args
    assert not any(args[0] in {"edit", "revert"} for args in fake_p4.args)
    assert fake_p4.args[-1] == ["submit", "-c", CHANGE]


def test_failed_write_discards_changelist(fake_p4: FakeP4) -> None:
    """Exception in the block reverts files and deletes the changelist."""
    fake_p4.respond(WHERE_RESULT)
    _respond_create_changelist(fake_p4)
    fake_p4.respond(rec.CAPTURED_FSTAT_SYNCED)
    fake_p4.respond(rec.DOCUMENTED_EDIT_RESULT)
    fake_p4.respond()  # revert
    fake_p4.respond({"code": "info", "data": "deleted", "level": 0})

    def write() -> None:
        """Simulate failed write.

        Raises:
            OSError: Always.

        """
        with track_file_changes([PATH], "desc"):
            msg = "disk full"
            raise OSError(msg)

    with pytest.raises(OSError, match="disk full"):
        write()

    assert fake_p4.args[-2:] == [
        ["revert", "-c", CHANGE, PATH],
        ["change", "-d", CHANGE],
    ]


def test_p4_error_on_submit_is_not_raised(fake_p4: FakeP4) -> None:
    """Perforce failure after write is logged, not raised."""
    fake_p4.respond(WHERE_RESULT)
    _respond_create_changelist(fake_p4)
    fake_p4.respond(rec.CAPTURED_FSTAT_SYNCED)
    fake_p4.respond(rec.DOCUMENTED_EDIT_RESULT)
    fake_p4.respond()  # revert -a
    fake_p4.respond({**rec.DOCUMENTED_OPENED_DEFAULT, "change": CHANGE})
    fake_p4.respond(returncode=1, stderr=b"Submit failed")

    with track_file_changes([PATH], "desc"):
        pass

    assert fake_p4.args[-1] == ["submit", "-c", CHANGE]


def test_p4_error_on_open_discards_changelist(fake_p4: FakeP4) -> None:
    """Failure to open files deletes the changelist, the write still runs."""
    fake_p4.respond(WHERE_RESULT)
    _respond_create_changelist(fake_p4)
    fake_p4.respond(returncode=1, stderr=b"Connect to server failed")
    fake_p4.respond()  # change -d

    written = False
    with track_file_changes([PATH], "desc"):
        written = True

    assert written
    assert fake_p4.args[-1] == ["change", "-d", CHANGE]


def test_p4_error_on_discard_is_not_raised(fake_p4: FakeP4) -> None:
    """Failed clean up of the changelist is only logged."""
    fake_p4.respond(WHERE_RESULT)
    _respond_create_changelist(fake_p4)
    fake_p4.respond(returncode=1, stderr=b"Connect to server failed")
    fake_p4.respond(returncode=1, stderr=b"Connect to server failed")

    with track_file_changes([PATH], "desc"):
        pass

    assert fake_p4.args[-1] == ["change", "-d", CHANGE]

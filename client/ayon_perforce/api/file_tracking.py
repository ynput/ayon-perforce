"""Track files written by host integrations and submit them to Perforce.

Host integrations store pipeline data (e.g. publish context data) in files
inside the Perforce workspace. Without handling, these files end up as
uncommitted changes which then fail ``ValidateWorkspace`` on publish.

`track_file_changes` opens the files in a dedicated changelist before the
write (so read-only synced files become writable) and submits only that
changelist after the write. Artist's own pending changes are not touched.

"""
from __future__ import annotations

from contextlib import contextmanager
from logging import getLogger
from pathlib import Path
from typing import TYPE_CHECKING

from ayon_perforce.api.commands import P4Commands
from ayon_perforce.api.exceptions import P4Error
from ayon_perforce.api.models import Action, MarshalCode, MessageSeverity

if TYPE_CHECKING:
    from collections.abc import Generator, Iterable


log = getLogger(__name__)


class _TrackedChange:
    """State of files opened in the dedicated changelist."""

    def __init__(self, change: int) -> None:
        self.change = change
        # Files opened for edit by this tracker.
        self.edited: list[str] = []
        # Files not in depot yet, opened for add after the write.
        self.new: list[str] = []
        # Files already opened by artist, moved to the changelist.
        self.reopened: list[str] = []


def _open_files(
        p4: P4Commands, tracked: _TrackedChange, paths: list[str]) -> None:
    """Open existing files for edit in the tracked changelist."""
    for path in paths:
        fstat = next(iter(p4.fstat([path])), None)
        if fstat is None:
            tracked.new.append(path)
        elif fstat.action is not None:
            if fstat.change != tracked.change:
                P4Commands.run_p4("reopen", "-c", str(tracked.change), path)
            tracked.reopened.append(path)
        elif fstat.head is None or fstat.head.action in {
            Action.DELETE, Action.MOVE_DELETE
        }:
            tracked.new.append(path)
        else:
            tracked.edited.append(path)

    if tracked.edited:
        p4.edit(tracked.edited, changelist=tracked.change)


def _opened_in_change(change: int) -> list[str]:
    """Return depot paths of files opened in *change*.

    Returns:
        list[str]: Depot paths.

    """
    records = P4Commands.run_p4(
        "opened", "-c", str(change), max_severity=MessageSeverity.WARNING
    )
    return [
        record["depotFile"]
        for record in records
        if record.get("code") == MarshalCode.STAT.value
    ]


def _submit(p4: P4Commands, tracked: _TrackedChange) -> None:
    """Add new files, drop unchanged ones and submit the changelist."""
    new = [path for path in tracked.new if Path(path).exists()]
    if new:
        p4.add(new, changelist=tracked.change)
    if tracked.edited:
        # Do not revert files opened by artist, only those opened here.
        P4Commands.run_p4(
            "revert", "-a", "-c", str(tracked.change), *tracked.edited,
            max_severity=MessageSeverity.WARNING,
        )

    change = str(tracked.change)
    if _opened_in_change(tracked.change):
        P4Commands.run_p4("submit", "-c", change)
        log.info("Submitted changelist %s", change)
    else:
        P4Commands.run_p4("change", "-d", change)
        log.debug("No changes, deleted changelist %s", change)


def _discard(tracked: _TrackedChange) -> None:
    """Revert files opened here and delete the changelist."""
    change = str(tracked.change)
    try:
        if tracked.edited:
            P4Commands.run_p4(
                "revert", "-c", change, *tracked.edited,
                max_severity=MessageSeverity.WARNING,
            )
        if tracked.reopened:
            P4Commands.run_p4("reopen", "-c", "default", *tracked.reopened)
        P4Commands.run_p4("change", "-d", change)
    except P4Error:
        log.warning(
            "Failed to clean up changelist %s", change, exc_info=True)


@contextmanager
def track_file_changes(
    filepaths: Iterable[str | Path],
    description: str,
) -> Generator[None, None, None]:
    """Open files for edit before the block, submit them after it.

    Paths outside any Perforce workspace are ignored. Perforce errors are
    logged and never raised, so the write itself is not blocked by them;
    leftover changes are still caught by publish validation.

    Args:
        filepaths: Local paths of files that will be written in the block.
        description: Description of the submitted changelist.

    Yields:
        None

    """
    paths = [
        str(path) for path in filepaths
        if P4Commands.is_in_any_workspace(path)
    ]
    if not paths:
        yield
        return

    p4 = P4Commands()
    tracked: _TrackedChange | None = None
    try:
        tracked = _TrackedChange(p4.create_changelist(description).change)
        _open_files(p4, tracked, paths)
    except P4Error:
        log.warning("Failed to open files in Perforce", exc_info=True)
        if tracked is not None:
            _discard(tracked)
        tracked = None

    try:
        yield
    except BaseException:
        if tracked is not None:
            _discard(tracked)
        raise

    if tracked is None:
        return
    try:
        _submit(p4, tracked)
    except P4Error:
        log.warning(
            "Failed to submit changelist %s", tracked.change, exc_info=True)

"""Opt-in, read-only tests against a real Helix Core server.

They use the current ``p4`` environment (``P4PORT``, ``P4USER``,
``P4CLIENT``, tickets) and only run read-only commands. Enable with::

    AYON_P4_LIVE_TESTS=1 pytest tests/test_live_server.py

Optionally set ``AYON_P4_LIVE_FILESPEC`` (default ``//...``) to limit
which files ``fstat``/``filelog`` inspect.
"""

from __future__ import annotations

import os
import shutil

import pytest
from ayon_perforce.api.commands import P4Commands
from ayon_perforce.api.models import ChangeInfo

pytestmark = pytest.mark.skipif(
    os.getenv("AYON_P4_LIVE_TESTS") != "1" or not shutil.which("p4"),
    reason="Set AYON_P4_LIVE_TESTS=1 and have p4 on PATH to run",
)

FILESPEC = os.getenv("AYON_P4_LIVE_FILESPEC", "//...")
LIMIT = "20"


@pytest.fixture(scope="module")
def p4() -> P4Commands:
    """Return commands instance using current p4 environment."""
    return P4Commands()


def test_live_user(p4: P4Commands) -> None:
    """Current user spec parses."""
    user = P4Commands.run_p4("info")[0]["userName"]
    p4.get_user(user)


def test_live_client(p4: P4Commands) -> None:
    """Current client spec parses."""
    p4.get_client()


def test_live_changes(p4: P4Commands) -> None:
    """Recent submitted and pending changes parse."""
    for data in P4Commands.run_p4("changes", "-l", "-m", LIMIT):
        ChangeInfo(**data)
        p4.get_change(int(data["change"]))


def test_live_fstat(p4: P4Commands) -> None:
    """Fstat of files parses (incl. deleted)."""
    for data in P4Commands.run_p4("files", "-m", LIMIT, FILESPEC):
        list(p4.fstat([data["depotFile"]], include_deleted=True))


def test_live_filelog(p4: P4Commands) -> None:
    """Filelog of files parses."""
    for data in P4Commands.run_p4("files", "-m", LIMIT, FILESPEC):
        list(p4.get_revisions([data["depotFile"]]))


def test_live_opened(p4: P4Commands) -> None:
    """Opened files of default changelist parse."""
    list(p4.opened())

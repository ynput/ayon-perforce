"""Pytest configuration for ayon-perforce tests."""

from __future__ import annotations

import marshal
import subprocess
import sys
import types
from pathlib import Path
from typing import Any

import pytest

CLIENT_DIR = Path(__file__).resolve().parents[1] / "client"
if str(CLIENT_DIR) not in sys.path:
    sys.path.insert(0, str(CLIENT_DIR))


def _register_package_without_init() -> None:
    """Make ``ayon_perforce.api`` importable without ``ayon_core``.

    The package ``__init__`` imports the addon, which requires ``ayon_core``.
    The wrapped p4 API is standalone, so when ``ayon_core`` is missing an
    empty package module pointing at the source directory is registered
    instead.
    """
    try:
        import ayon_perforce  # ruff: ignore[unused-import]
    except ImportError:
        for name in list(sys.modules):
            if name == "ayon_perforce" or name.startswith("ayon_perforce."):
                sys.modules.pop(name)
        module = types.ModuleType("ayon_perforce")
        module.__path__ = [str(CLIENT_DIR / "ayon_perforce")]
        sys.modules["ayon_perforce"] = module


_register_package_without_init()


def _encode(value: Any) -> Any:  # ruff: ignore[any-type]
    """Encode value the same way ``p4 -G`` does (str as bytes).

    Returns:
        Encoded value.

    """
    if isinstance(value, str):
        return value.encode("utf-8")
    return value


def _load_stdin(data: bytes | None) -> Any:  # ruff: ignore[any-type]
    """Decode stdin, marshaled spec or plain text (``p4 login``).

    Returns:
        Decoded stdin or None.

    """
    if not data:
        return None
    try:
        return marshal.loads(data)  # ruff: ignore[suspicious-marshal-usage]
    except (EOFError, ValueError, TypeError):
        return data.decode()


class FakeP4:
    """Replacement for ``subprocess.run`` emulating ``p4 -G``.

    Responses are queued with `respond` and served in order. Every call is
    recorded in `calls` with the p4 arguments (without ``p4 -G``) and the
    decoded stdin spec.
    """

    def __init__(self) -> None:
        """Initialize empty response queue."""
        self.calls: list[dict[str, Any]] = []
        self._responses: list[tuple[list[dict], int, bytes]] = []

    def respond(
        self,
        *records: dict,
        returncode: int = 0,
        stderr: bytes = b"",
    ) -> FakeP4:
        """Queue response for the next p4 call.

        Returns:
            Self for chaining.

        """
        self._responses.append((list(records), returncode, stderr))
        return self

    def __call__(
        self,
        cmd: list[str],
        input: bytes | None = None,  # ruff: ignore[builtin-argument-shadowing]
        **kwargs: Any,  # ruff: ignore[any-type]
    ) -> subprocess.CompletedProcess:
        """Emulate ``subprocess.run``.

        Returns:
            Completed process with marshaled records on stdout.

        Raises:
            AssertionError: When there is no queued response.

        """
        args = cmd[2:] if cmd[1:2] == ["-G"] else cmd[1:]
        self.calls.append({
            "args": args,
            "stdin": _load_stdin(input),
            "env": kwargs.get("env"),
        })
        if not self._responses:
            msg = f"Unexpected p4 call: {args}"
            raise AssertionError(msg)
        records, returncode, stderr = self._responses.pop(0)
        stdout = b"".join(
            marshal.dumps(
                {_encode(k): _encode(v) for k, v in record.items()}, 0
            )
            for record in records
        )
        return subprocess.CompletedProcess(
            cmd, returncode, stdout=stdout, stderr=stderr
        )

    @property
    def args(self) -> list[list[str]]:
        """Arguments of all recorded calls."""
        return [call["args"] for call in self.calls]


@pytest.fixture
def fake_p4(monkeypatch: pytest.MonkeyPatch) -> FakeP4:
    """Patch subprocess in the commands module with `FakeP4`.

    Returns:
        FakeP4 instance to queue responses on.

    """
    from ayon_perforce.api import commands

    fake = FakeP4()
    monkeypatch.setattr(commands.subprocess, "run", fake)
    monkeypatch.setattr(commands.shutil, "which", lambda _: "p4")
    monkeypatch.setattr(commands.P4Commands, "_connection", None)
    return fake

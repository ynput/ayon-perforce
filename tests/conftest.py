"""Pytest configuration for ayon-perforce tests."""

from __future__ import annotations

import marshal
import subprocess
import sys
import types
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest

if TYPE_CHECKING:
    from collections.abc import Iterator

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


# ---------------------------------------------------------------------------
# ayon_core stub for `ayon_perforce.lib`
# ---------------------------------------------------------------------------
class FakeSecureRegistry:
    """In-memory replacement of ``AYONSecureRegistry``."""

    storage: dict[str, dict[str, str]] = {}  # ruff: ignore[mutable-class-default]

    def __init__(self, name: str) -> None:
        """Use storage of the registry name."""
        self._items = self.storage.setdefault(name, {})

    def get_item(self, key: str, *args: Any) -> Any:  # ruff: ignore[any-type]
        """Return stored value, default or raise like the original.

        Returns:
            Stored value or default.

        Raises:
            ValueError: When missing and no default is given.

        """
        if key in self._items:
            return self._items[key]
        if args:
            return args[0]
        msg = f"Item {key} not found"
        raise ValueError(msg)

    def set_item(self, key: str, value: str) -> None:
        """Store value."""
        self._items[key] = value

    def delete_item(self, key: str) -> None:
        """Delete value."""
        self._items.pop(key, None)


def _register_ayon_core_stub() -> None:
    """Register minimal ``ayon_core.lib`` when ``ayon_core`` is missing."""
    try:
        import ayon_core.lib  # ruff: ignore[unused-import]
    except ImportError:
        import logging

        core = types.ModuleType("ayon_core")
        core.__path__ = []
        core_lib = types.ModuleType("ayon_core.lib")
        core_lib.AYONSecureRegistry = FakeSecureRegistry
        core_lib.Logger = types.SimpleNamespace(get_logger=logging.getLogger)
        core.lib = core_lib
        sys.modules["ayon_core"] = core
        sys.modules["ayon_core.lib"] = core_lib


@pytest.fixture
def p4lib(fake_p4: FakeP4) -> types.ModuleType:  # ruff: ignore[unused-function-argument]
    """Import ``ayon_perforce.lib`` with faked p4 and secure registry.

    Returns:
        The ``ayon_perforce.lib`` module.

    """
    _register_ayon_core_stub()
    from ayon_perforce import lib

    FakeSecureRegistry.storage.clear()
    return lib


# ---------------------------------------------------------------------------
# live server
# ---------------------------------------------------------------------------
ENV_FILE = Path(__file__).with_name(".env")


def _read_env_file(path: Path) -> dict[str, str]:
    """Parse simple ``KEY=value`` lines, ``#`` comments are skipped.

    Returns:
        Parsed variables.

    """
    result: dict[str, str] = {}
    if not path.is_file():
        return result
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        result[key.strip()] = value.strip().strip("'\"")
    return result


@pytest.fixture(scope="session")
def live_env() -> Iterator[dict[str, str]]:
    """Connection to the live server from ``tests/.env`` or environment.

    Variables from the file don't override the environment. They are
    exported only for the live session and restored afterwards.

    Yields:
        Effective p4 variables.

    """
    import os
    import shutil

    if not shutil.which("p4"):
        pytest.skip("'p4' executable is not on PATH")
    added = {
        key: value
        for key, value in _read_env_file(ENV_FILE).items()
        if key not in os.environ
    }
    os.environ.update(added)
    try:
        if not os.environ.get("P4PORT"):
            pytest.skip(f"P4PORT is not set (environment or {ENV_FILE})")
        yield {
            key: os.environ[key]
            for key in ("P4PORT", "P4USER", "P4CLIENT", "P4PASSWD")
            if os.environ.get(key)
        }
    finally:
        for key in added:
            os.environ.pop(key, None)

"""Check that model fields cover every key p4 returns.

Models are dataclasses, so any key in a ``p4 -G`` record that is not
a field name or alias makes the constructor raise ``TypeError``. Required
fields missing from a record fail the same way.

Records are pre-processed exactly like the matching `P4Commands` method
does before passing them to the model constructor.
"""

from __future__ import annotations

import dataclasses
import re
from typing import Callable

import p4_records as rec
import pytest
from ayon_perforce.api.models import (
    ActionInfo,
    Change,
    ChangeInfo,
    Client,
    FStat,
    Revision,
    Sync,
    User,
)

_INDEX_RE = re.compile(r"\d+(,\d+)?$")
_FILELOG_RE = re.compile(r"([a-zA-Z]+)([0-9]+)(?:,([0-9]+))?")


def unmapped_keys(model: type, data: dict) -> set[str]:
    """Return keys of the record no field of the model consumes.

    Keys are consumed by field names, aliases, ``code`` (dropped by the
    model) and ``collect`` metadata (indexed, prefixed or nested keys).

    Returns:
        Keys left over after all fields took theirs.

    """
    remaining = dict(data)
    remaining.pop("code", None)
    for data_field in dataclasses.fields(model):
        remaining.pop(data_field.name, None)
        remaining.pop(data_field.metadata.get("alias", data_field.name), None)
    for data_field in dataclasses.fields(model):
        if collect := data_field.metadata.get("collect"):
            collect(remaining)
    return set(remaining)


def required_keys(model: type) -> dict[str, set[str]]:
    """Return required fields mapped to keys that can fill them.

    Returns:
        Field name to accepted key names.

    """
    result = {}
    for data_field in dataclasses.fields(model):
        if (
            data_field.default is dataclasses.MISSING
            and data_field.default_factory is dataclasses.MISSING
        ):
            names = {data_field.name}
            if alias := data_field.metadata.get("alias"):
                names.add(alias)
            result[data_field.name] = names
    return result


def _raw(data: dict) -> list[dict]:
    return [dict(data)]


def _client(data: dict) -> list[dict]:
    data = {k: v for k, v in data.items() if not re.match(r"View\d+$", k)}
    data["views"] = []
    return [data]


def _sync(data: dict) -> list[dict]:
    data = dict(data)
    data.pop("totalFileCount", None)
    data.pop("totalFileSize", None)
    return [data]


def _filelog(data: dict) -> list[dict]:
    revisions: dict[int, dict] = {}
    shared = {}
    for key, value in data.items():
        match = _FILELOG_RE.match(key)
        if match:
            suffix = "" if match.group(3) is None else int(match.group(3))
            revisions.setdefault(int(match.group(2)), {})[
                f"{match.group(1)}{suffix}"
            ] = value
        else:
            shared[key] = value
    return [{**rev, **shared} for rev in revisions.values()]


# (model, preprocess, record name) - preprocess mirrors P4Commands code.
CASES: list[tuple[type, Callable[[dict], list[dict]], str]] = [
    (User, _raw, "CAPTURED_USER_SPEC"),
    (User, _raw, "DOCUMENTED_USER_SPEC_WITH_REVIEWS"),
    (Client, _client, "CAPTURED_CLIENT_SPEC_STREAM"),
    (Client, _client, "DOCUMENTED_CLIENT_SPEC_CLASSIC"),
    (Client, _client, "DOCUMENTED_CLIENT_SPEC_STREAM_AT_CHANGE"),
    (Change, _raw, "DOCUMENTED_CHANGE_SPEC_PENDING"),
    (Change, _raw, "DOCUMENTED_CHANGE_SPEC_PENDING_WITH_JOBS"),
    (Change, _raw, "DOCUMENTED_CHANGE_SPEC_SUBMITTED"),
    (Change, _raw, "DOCUMENTED_CHANGE_SPEC_SHELVED"),
    (ChangeInfo, _raw, "CAPTURED_CHANGES_SUBMITTED"),
    (ChangeInfo, _raw, "CAPTURED_CHANGES_PENDING"),
    (ChangeInfo, _raw, "DOCUMENTED_CHANGES_PENDING_SHELVED_FILES"),
    (ChangeInfo, _raw, "DOCUMENTED_CHANGES_RESTRICTED"),
    (ChangeInfo, _raw, "DOCUMENTED_CHANGES_RENUMBERED"),
    (FStat, _raw, "CAPTURED_FSTAT_SYNCED"),
    (FStat, _raw, "DOCUMENTED_FSTAT_OPENED_FOR_EDIT"),
    (FStat, _raw, "DOCUMENTED_FSTAT_OPENED_FOR_ADD"),
    (FStat, _raw, "DOCUMENTED_FSTAT_OTHER_OPEN"),
    (FStat, _raw, "DOCUMENTED_FSTAT_LOCKED_AND_RESOLVE"),
    (FStat, _raw, "DOCUMENTED_FSTAT_FILE_SIZE_DIGEST"),
    (FStat, _raw, "DOCUMENTED_FSTAT_MOVED"),
    (FStat, _raw, "DOCUMENTED_FSTAT_SHELVED"),
    (FStat, _raw, "DOCUMENTED_OPENED_DEFAULT"),
    (FStat, _raw, "DOCUMENTED_OPENED_LOCKED_MOVED"),
    (Revision, _filelog, "CAPTURED_FILELOG_TWO_REVISIONS"),
    (Revision, _filelog, "DOCUMENTED_FILELOG_INTEGRATED"),
    (Revision, _filelog, "DOCUMENTED_FILELOG_DELETED"),
    (Sync, _sync, "DOCUMENTED_SYNC_FIRST"),
    (Sync, _sync, "DOCUMENTED_SYNC_NEXT"),
    (Sync, _sync, "DOCUMENTED_SYNC_DELETED"),
    (ActionInfo, _raw, "DOCUMENTED_EDIT_RESULT"),
    (ActionInfo, _raw, "DOCUMENTED_ADD_RESULT"),
    (ActionInfo, _raw, "DOCUMENTED_DELETE_RESULT"),
]


def _case_id(case: tuple) -> str:
    model, _, name = case
    return f"{model.__name__}-{name}"


@pytest.mark.parametrize("case", CASES, ids=_case_id)
def test_all_returned_keys_are_mapped(case: tuple) -> None:
    """Every key of the p4 record has a matching field or alias."""
    model, preprocess, name = case
    unknown: set[str] = set()
    for data in preprocess(getattr(rec, name)):
        unknown |= {
            _INDEX_RE.sub("#", key) for key in unmapped_keys(model, data)
        }
    assert not unknown, (
        f"{model.__name__} has no field/alias for keys from {name}: "
        f"{sorted(unknown)}"
    )


@pytest.mark.parametrize("case", CASES, ids=_case_id)
def test_required_fields_are_present(case: tuple) -> None:
    """Every required field of the model is filled by the p4 record."""
    model, preprocess, name = case
    missing: set[str] = set()
    for data in preprocess(getattr(rec, name)):
        missing |= {
            field_name
            for field_name, keys in required_keys(model).items()
            if not keys & data.keys()
        }
    assert not missing, (
        f"{model.__name__} required fields missing in {name}: "
        f"{sorted(missing)}"
    )


@pytest.mark.parametrize("case", CASES, ids=_case_id)
def test_model_constructs_from_record(case: tuple) -> None:
    """The model can be instantiated from the p4 record."""
    model, preprocess, name = case
    for data in preprocess(getattr(rec, name)):
        model(**data)

"""Tests for the p4 dataclass models and helpers."""

from __future__ import annotations

import datetime  # ruff: ignore[unconventional-import-alias]
import pathlib

import p4_records as rec
import pytest
from ayon_perforce.api.models import (
    Action,
    ActionMessage,
    AuthMethod,
    Change,
    ChangeInfo,
    ChangeStatus,
    ChangeType,
    Client,
    ClientOptions,
    ClientType,
    FStat,
    HeadInfo,
    MarshalCode,
    MessageLevel,
    SubmitOptions,
    User,
    UserType,
    View,
    datetime_to_perforce_date,
    perforce_date_to_datetime,
    perforce_datetime_to_timestamp,
    perforce_timestamp_to_datetime,
)

UTC = datetime.timezone.utc


# ---------------------------------------------------------------------------
# Date helpers
# ---------------------------------------------------------------------------
def test_perforce_date_round_trip() -> None:
    """P4 date string converts to UTC datetime and back."""
    date = perforce_date_to_datetime("2026/10/20 08:35:33")
    assert date == datetime.datetime(2026, 10, 20, 8, 35, 33, tzinfo=UTC)
    assert datetime_to_perforce_date(date) == "2026/10/20 08:35:33"


def test_perforce_timestamp_round_trip() -> None:
    """P4 timestamp converts to UTC datetime and back."""
    date = perforce_timestamp_to_datetime("1789994944")
    assert date.tzinfo == UTC
    assert perforce_datetime_to_timestamp(date) == "1789994944"


# ---------------------------------------------------------------------------
# alias_dataclass behavior
# ---------------------------------------------------------------------------
def test_alias_and_field_name_are_both_accepted() -> None:
    """Constructor accepts field names as well as aliases."""
    by_alias = User(**rec.CAPTURED_USER_SPEC)
    by_name = User(
        access=by_alias.access,
        auth_method=by_alias.auth_method,
        email=by_alias.email,
        full_name=by_alias.full_name,
        type=by_alias.type,
        update=by_alias.update,
        name=by_alias.name,
    )
    assert by_alias == by_name


def test_alias_and_name_together_raise() -> None:
    """Passing both alias and field name is rejected."""
    with pytest.raises(TypeError, match="both"):
        User(**rec.CAPTURED_USER_SPEC, name="other")


def test_code_key_is_dropped() -> None:
    """The ``code`` key added by ``p4 -G`` is not passed to the model."""
    user = User(**rec.CAPTURED_USER_SPEC)
    assert not hasattr(user, "code")


def test_unknown_key_is_dropped() -> None:
    """Fields added by newer p4 servers don't break the model."""
    user = User(**rec.CAPTURED_USER_SPEC, extraTag0="x")
    assert user == User(**rec.CAPTURED_USER_SPEC)


def test_none_value_for_optional_field() -> None:
    """p4 "none" (``haveRev`` of a file opened for add) becomes None."""
    fstat = FStat(**rec.CAPTURED_OPENED_ADD)
    assert fstat.have_rev is None
    assert fstat.action == Action.ADD


def test_none_value_for_required_field_raises() -> None:
    """"none" is converted only for optional fields."""
    with pytest.raises(ValueError, match="none"):
        ChangeInfo(**{**rec.CAPTURED_CHANGES_PENDING, "change": "none"})


def test_datetime_from_date_string() -> None:
    """Datetime fields accept p4 date strings."""
    user = User(**rec.CAPTURED_USER_SPEC)
    assert user.update == datetime.datetime(
        2023, 2, 10, 16, 17, 48, tzinfo=UTC
    )


def test_datetime_from_timestamp() -> None:
    """Datetime fields accept p4 timestamps."""
    info = ChangeInfo(**rec.CAPTURED_CHANGES_SUBMITTED)
    assert info.date == perforce_timestamp_to_datetime("1789994944")


def test_optional_datetime_from_string() -> None:
    """Optional datetime fields (``X | None``) are coerced too."""
    data = dict(rec.DOCUMENTED_CHANGE_SPEC_SHELVED)
    files = [data.pop("Files0"), data.pop("Files1")]
    change = Change(**data, files=files)
    assert isinstance(change.shelve_access, datetime.datetime)


def test_nested_head_info_from_fstat_record() -> None:
    """``head*`` keys of fstat end up in `FStat.head`."""
    fstat = FStat(**rec.CAPTURED_FSTAT_SYNCED)
    assert isinstance(fstat.head, HeadInfo)
    assert fstat.head.revision == 1
    assert fstat.head.change == 121


# ---------------------------------------------------------------------------
# Value coercion: p4 returns only strings, models are typed.
# ---------------------------------------------------------------------------
def test_coerce_user_enums() -> None:
    """User enums are coerced."""
    user = User(**rec.CAPTURED_USER_SPEC)
    assert user.type is UserType.STANDARD
    assert user.auth_method is AuthMethod.PERFORCE


def test_coerce_client_options() -> None:
    """Client ``Options`` string becomes `ClientOptions`."""
    client = Client.client_from_data(**rec.CAPTURED_CLIENT_SPEC_STREAM)
    assert isinstance(client.options, ClientOptions)
    assert client.options.rmdir is True
    assert client.options.locked is False


def test_coerce_client_enums_and_root() -> None:
    """Client enums and root path are coerced."""
    client = Client.client_from_data(**rec.CAPTURED_CLIENT_SPEC_STREAM)
    assert client.type is ClientType.STANDARD
    assert client.submit_options is SubmitOptions.SUBMIT_UNCHANGED
    assert isinstance(client.root, pathlib.Path)


def test_coerce_change_info() -> None:
    """ChangeInfo number and enums are coerced."""
    info = ChangeInfo(**rec.CAPTURED_CHANGES_SUBMITTED)
    assert info.change == 128
    assert info.status is ChangeStatus.SUBMITTED
    assert info.type is ChangeType.PUBLIC


def test_coerce_fstat_flags_and_numbers() -> None:
    """FStat presence flags and numbers are coerced.

    ``p4`` marks flags like ``isMapped`` by an empty value, so the
    presence of the key means ``True``.
    """
    fstat = FStat(**rec.DOCUMENTED_FSTAT_OPENED_FOR_ADD)
    assert fstat.is_mapped is True
    assert fstat.action is Action.ADD
    assert fstat.change == 130


def test_coerce_fstat_default_changelist() -> None:
    """``change`` of a file in the default changelist is ``default``."""
    fstat = FStat(**rec.DOCUMENTED_OPENED_DEFAULT)
    assert fstat.change == "default"
    assert fstat.rev == 3
    assert fstat.have_rev == 3


# ---------------------------------------------------------------------------
# ClientOptions / View / MarshalCode / ActionMessage
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "options",
    [
        "noallwrite noclobber nocompress unlocked nomodtime rmdir",
        "allwrite clobber compress locked modtime normdir",
    ],
)
def test_client_options_parse(options: str) -> None:
    """Option string is parsed and serializes back unchanged."""
    parsed = ClientOptions.from_string(options)
    assert str(parsed) == options


def test_client_options_unlocked_is_not_locked() -> None:
    """``unlocked`` must not be read as ``locked``."""
    assert ClientOptions.from_string("unlocked").locked is False


@pytest.mark.parametrize(
    ("string", "expected"),
    [
        ("//depot/foo/... //ws/bar/...", ("//depot/foo/...", "//ws/bar/...")),
        (
            '"//depot/with space/..." "//ws/with space/..."',
            ("//depot/with space/...", "//ws/with space/..."),
        ),
        (
            "-//depot/foo/x/... //ws/x/...",
            ("-//depot/foo/x/...", "//ws/x/..."),
        ),
    ],
)
def test_view_from_string(string: str, expected: tuple[str, str]) -> None:
    """View lines incl. quoted paths and exclusions are split."""
    assert View.from_string(string) == View(*expected)


def test_client_views_are_collected_in_order() -> None:
    """``View#`` keys become ordered `View` list."""
    data = {
        **rec.CAPTURED_CLIENT_SPEC_STREAM,
        "View1": rec.DOCUMENTED_CLIENT_SPEC_CLASSIC["View1"],
    }
    client = Client.client_from_data(**data)
    assert client.views == [
        View(f"{rec.STREAM}/...", f"//{rec.WS}/..."),
        View("//depot/with space/...", f"//{rec.WS}/with space/..."),
    ]


def test_marshal_code_compares_to_string() -> None:
    """MarshalCode compares equal to its raw value."""
    assert MarshalCode.ERROR == "error"
    assert "info" == MarshalCode.INFO  # ruff: ignore[yoda-conditions]
    assert MarshalCode.STAT != "error"


def test_action_message_from_info() -> None:
    """Info record is split into path, message and level."""
    message = ActionMessage.from_info_data(rec.DOCUMENTED_INFO_ALSO_OPENED)
    assert message.path == f"{rec.STREAM}/a.txt"
    assert message.message == "also opened by bob@bob_ws"
    assert message.level is MessageLevel.NONE

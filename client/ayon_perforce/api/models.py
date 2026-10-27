"""P4 models.

This is taken from https://github.com/tahv/pyforce but modified
so it doesn't depend on pydantic as that is binary dependency unusable
in current dependency package AYON architecture and various DCC environments.

To support pydantic aliases, some hacky stuff is introduced, but p4
isn't really consistent on the names it returns, so lesser evil was chosen.

Models are built directly from ``p4 -G`` records. Besides aliases, the
constructor converts raw p4 strings to the annotated types and fields with
``collect`` metadata gather indexed (``Files0``, ``Files1``...), prefixed
(``attr-<name>``) or flattened (``headRev``, ``headAction``...) keys.

"""
from __future__ import annotations

import datetime as dt
import pathlib
import re
import shlex
import types
from collections.abc import Callable
from dataclasses import MISSING, dataclass, field, fields
from enum import Enum
from logging import getLogger
from typing import (
    Any,
    Literal,
    NamedTuple,
    TypeVar,
    Union,
    get_args,
    get_origin,
    get_type_hints,
)

__all__ = [
    "Action",
    "ActionInfo",
    "ActionMessage",
    "AuthMethod",
    "Change",
    "ChangeInfo",
    "ChangeStatus",
    "ChangeType",
    "Client",
    "ClientOptions",
    "ClientType",
    "Connection",
    "FStat",
    "HeadInfo",
    "Integration",
    "MarshalCode",
    "MessageSeverity",
    "OtherOpen",
    "PerforceDict",
    "ResolveInfo",
    "Revision",
    "SubmitOptions",
    "Sync",
    "UncommittedChange",
    "User",
    "UserType",
    "View",
    "datetime_to_perforce_date",
    "perforce_date_to_datetime",
    "perforce_datetime_to_timestamp",
    "perforce_timestamp_to_datetime",
]


PerforceDict = dict[str, str]
Collector = Callable[[dict[str, Any]], Any]

R = TypeVar("R")
log = getLogger(__name__)

_UNION_TYPES = {Union, getattr(types, "UnionType", Union)}


def perforce_date_to_datetime(string: str) -> dt.datetime:
    """Convert p4 date to dt.

    Args:
        string: date string.

    Returns:
        dt.datetime with UTC timezone.

    """
    utc = dt.UTC
    return dt.datetime.strptime(string, PERFORCE_DATE_FORMAT).replace(
        tzinfo=utc
    )


def perforce_timestamp_to_datetime(time: str) -> dt.datetime:
    """Convert p4 timestamp to dt.

    Args:
        time: timestamp.

    Returns:
        dt.datetime with UTC timezone.

    """
    return dt.datetime.fromtimestamp(int(time), tz=dt.UTC)


def perforce_datetime_to_timestamp(date: dt.datetime) -> str:
    """Convert datetime to timestamp.

    Args:
        date: datetime object.

    Returns:
        timestamp for p4.

    """
    return str(round(date.timestamp()))


def datetime_to_perforce_date(date: dt.datetime) -> str:
    """Convert datetime to p4 date.

    Args:
        date: datetime instance.

    Returns:
        p4 date string.

    """
    return date.strftime(PERFORCE_DATE_FORMAT)


def _coerce_perforce_datetime(value: str) -> dt.datetime:
    if value.lstrip("-").isdigit():
        return perforce_timestamp_to_datetime(value)
    return perforce_date_to_datetime(value)


def _union_members(annotation: Any) -> tuple[Any, ...]:  # ruff: ignore[any-type]
    if get_origin(annotation) in _UNION_TYPES:
        return tuple(
            arg for arg in get_args(annotation) if arg is not type(None)
        )
    return (annotation,)


def _is_optional(annotation: Any) -> bool:  # ruff: ignore[any-type]
    return (
        get_origin(annotation) in _UNION_TYPES
        and type(None) in get_args(annotation)
    )


def _convert(member: Any, value: str) -> Any:  # ruff: ignore[any-type, too-many-return-statements]
    """Convert value to a single (non-union) annotation member.

    ``ValueError`` from the conversion (``int``, enum...) is propagated.

    Returns:
        Converted value or `MISSING` if member doesn't apply.

    """
    if member is str or member is Any:
        return value
    if get_origin(member) is Literal:
        return value if value in get_args(member) else MISSING
    if member is bool:
        # Flags like ``isMapped`` or ``ourLock`` are sent with an empty
        # value, presence of the key means ``True``.
        return True
    if member is dt.datetime:
        return _coerce_perforce_datetime(value)
    if member is int:
        return int(value)
    if not isinstance(member, type):
        return MISSING
    from_string = getattr(member, "from_string", None)
    if from_string is not None:
        return from_string(value)
    if issubclass(member, (Enum, pathlib.PurePath)):
        return member(value)
    return MISSING


def _coerce_value(annotation: Any, value: Any) -> Any:  # ruff: ignore[any-type]
    """Convert a raw p4 string to the annotated type.

    ``p4 -G`` returns only strings, so each member of the annotation is
    tried in order and the first successful conversion wins.

    Returns:
        Converted value. Non-string values are returned unchanged.

    Raises:
        ValueError: If the value can't be converted to any annotated type.

    """
    if not isinstance(value, str):
        return value
    errors: list[ValueError] = []
    for member in _union_members(annotation):
        try:
            result = _convert(member, value)
        except ValueError as error:
            errors.append(error)
            continue
        if result is not MISSING:
            return result
    # p4 uses "none" for missing values, e.g. ``haveRev`` of a file
    # opened for add.
    if value == "none" and _is_optional(annotation):
        return None
    msg = f"Can't convert {value!r} to {annotation}"
    if errors:
        msg = f"{msg}: {errors[-1]}"
    raise ValueError(msg)


def indexed(
    prefix: str,
    converter: Callable[[str], Any] | None = None,
) -> Collector:
    """Collect indexed keys like ``Files0``, ``Files1`` into a list.

    Args:
        prefix: Key prefix, e.g. ``Files``.
        converter: Optional function applied to every value.

    Returns:
        Collector function for the ``collect`` field metadata.

    """
    regex = re.compile(rf"{re.escape(prefix)}(\d+)$")

    def collect(data: dict[str, Any]) -> Any:  # ruff: ignore[any-type]
        items = []
        for key in list(data):
            match = regex.match(key)
            if match:
                items.append((int(match.group(1)), data.pop(key)))
        if not items:
            return MISSING
        values = [value for _, value in sorted(items)]
        if converter is not None:
            values = [converter(value) for value in values]
        return values

    return collect


def indexed_group(
    factory: Callable[..., Any],
    prefixes: dict[str, str],
) -> Collector:
    """Collect related indexed keys into a list of objects.

    For example ``otherOpen0``, ``otherAction0`` and ``otherChange0``
    describe the first "other open" of a file.

    Args:
        factory: Called with keyword arguments of each index.
        prefixes: Key prefix to ``factory`` keyword argument name.

    Returns:
        Collector function for the ``collect`` field metadata.

    """
    regex = re.compile(
        rf"({'|'.join(re.escape(prefix) for prefix in prefixes)})(\d+)$"
    )

    def collect(data: dict[str, Any]) -> Any:  # ruff: ignore[any-type]
        groups: dict[int, dict[str, Any]] = {}
        for key in list(data):
            match = regex.match(key)
            if match:
                groups.setdefault(int(match.group(2)), {})[
                    prefixes[match.group(1)]
                ] = data.pop(key)
        if not groups:
            return MISSING
        return [factory(**groups[index]) for index in sorted(groups)]

    return collect


def prefixed(prefix: str) -> Collector:
    """Collect keys starting with ``prefix`` into a dict.

    Used for file attributes (``attr-<name>``) returned by ``fstat -Oa``.

    Args:
        prefix: Key prefix, e.g. ``attr-``.

    Returns:
        Collector function for the ``collect`` field metadata.

    """
    def collect(data: dict[str, Any]) -> Any:  # ruff: ignore[any-type]
        result = {
            key[len(prefix):]: data.pop(key)
            for key in list(data)
            if key.startswith(prefix)
        }
        return result or MISSING

    return collect


def nested(cls: type) -> Collector:
    """Collect flat keys of another model into its instance.

    ``fstat`` returns head revision fields (``headRev``, ``headAction``...)
    on the top level, they are grouped into one object.

    Args:
        cls: Model to instantiate, keys are matched to its aliases.

    Returns:
        Collector function for the ``collect`` field metadata.

    """
    keys = {
        data_field.metadata.get("alias", data_field.name)
        for data_field in fields(cls)
    }

    def collect(data: dict[str, Any]) -> Any:  # ruff: ignore[any-type]
        values = {key: data.pop(key) for key in list(data) if key in keys}
        if not values:
            return MISSING
        return cls(**values)

    return collect


def _collect_and_coerce(
    kwargs: dict[str, Any],
    collectors: dict[str, Collector],
    type_hints: dict[str, Any],
) -> None:
    """Run field collectors and convert raw values in place."""
    for name, collect in collectors.items():
        if name in kwargs:
            continue
        value = collect(kwargs)
        if value is not MISSING:
            kwargs[name] = value
    for name, value in kwargs.items():
        if name in type_hints:
            kwargs[name] = _coerce_value(type_hints[name], value)


def _enable_alias_kwargs(cls: type[R]) -> type[R]:
    """Allow dataclass constructor kwargs by field name and alias.

    Args:
        cls: dataclass with aliases.

    Returns:
        Modified class.

    """
    # Must not fail silently, models without the alias constructor can't
    # be created from p4 records. Requires Python 3.11 (Unreal 5.4+).
    type_hints = get_type_hints(cls)
    data_fields = fields(cls)  # type: ignore[arg-type]
    alias_to_name = {
        alias: data_field.name
        for data_field in data_fields
        if (alias := data_field.metadata.get("alias"))
        and alias != data_field.name
    }
    collectors: dict[str, Collector] = {
        data_field.name: collect
        for data_field in data_fields
        if (collect := data_field.metadata.get("collect"))
    }
    field_names = {data_field.name for data_field in data_fields}

    original_init = cls.__init__

    def __init__(self, *args: Any, **kwargs: Any) -> None:  # ruff: ignore[any-type, dunder-function-name, missing-type-function-argument]
        """Constructor to handle aliases.

        Raises:
            TypeError: when alias and property name is the same.
        """
        # "code" is added by ``p4 -G`` to every response (e.g. "stat")
        # and is not part of the model, so it must be discarded.
        if "code" not in field_names:
            kwargs.pop("code", None)
        for alias, name in alias_to_name.items():
            if alias not in kwargs:
                continue
            if name in kwargs:
                msg = f"{cls.__name__} got both {name!r} and alias {alias!r}"
                raise TypeError(msg)
            kwargs[name] = kwargs.pop(alias)
        _collect_and_coerce(kwargs, collectors, type_hints)
        # Newer p4 servers add fields to their records (e.g.
        # ``streamStatus`` or ``extraTag0``), the model must not fail
        # on them.
        unknown = [key for key in kwargs if key not in field_names]
        if unknown:
            log.debug(
                "%s: ignoring unknown p4 fields %s", cls.__name__, unknown
            )
            for key in unknown:
                kwargs.pop(key)
        original_init(self, *args, **kwargs)

    cls.__init__ = __init__
    return cls


def alias_dataclass(_cls: type[R] | None = None, **kwargs: Any):  # ruff: ignore[any-type, missing-return-type-private-function]
    """Dataclass decorator with support for alias kwargs in constructor.

    This is poor-man substitution for pydantic aliases. It breaks mypy
    unfortunately, but it works for runtime. Raw p4 string values are
    converted to the annotated field types (``int``, enums, ``bool`` flags,
    paths, datetimes in either p4 date or timestamp format, classes with
    ``from_string``).

    Args:
        _cls: class to decorate.
        **kwargs: dataclass kwargs.

    Returns:
        wrapped class.

    """
    def wrap(cls: type[R]) -> type[R]:
        return _enable_alias_kwargs(dataclass(cls, **kwargs))

    if _cls is not None:
        return wrap(_cls)
    return wrap


class Connection(NamedTuple):
    """Perforce connection information."""

    port: str
    """Perforce host and port (``P4PORT``)."""

    user: str | None = None
    """Helix server username (``P4USER``)."""

    client: str | None = None
    """Client workspace name (``P4CLIENT``)."""

    password: str | None = None
    """Helix server password (``P4PASSWD``)."""


class MarshalCode(Enum):
    """Values of the ``code`` field from a marshaled P4 response.

    The output dictionary from ``p4 -G`` must have a ``code`` field.
    """

    STAT = "stat"
    """Means 'status' and is the default status."""

    ERROR = "error"
    """An error has occured.

    The full error message is contained in the 'data' field.
    """

    INFO = "info"
    """There was some feedback from the command.

    The message is contained in the 'data' field.
    """

    def __hash__(self) -> int:
        return hash(self.value)

    def __eq__(self, other: object) -> bool:
        if isinstance(other, MarshalCode):
            return self.value == other.value
        if isinstance(other, str):
            return self.value == other
        return NotImplemented


class MessageSeverity(Enum):
    """Perforce message severity levels."""

    EMPTY = 0
    """No Error."""

    INFO = 1
    """Informational message, something good happened."""

    WARNING = 2
    """Warning message, something not good happened."""

    FAILED = 3
    """Command failed, user did something wrong."""

    FATAL = 4
    """System broken, severe error, cannot continue."""


class MessageLevel(Enum):
    """Perforce generic 'level' codes, as described in `P4.Message`_.

    .. _P4.Message:
        https://www.perforce.com/manuals/p4python/Content/P4Python/python.p4_message.html
    """

    NONE = 0
    """Miscellaneous."""

    USAGE = 0x01
    """Request is not consistent with dox."""

    UNKNOWN = 0x02
    """Using unknown entity."""

    CONTEXT = 0x03
    """Using entity in the wrong context."""

    ILLEGAL = 0x04
    """You do not have permission to perform this action."""

    NOTYET = 0x05
    """An issue needs to be fixed before you can perform this action."""

    PROTECT = 0x06
    """Protections prevented operation."""

    EMPTY = 0x11
    """Action returned empty results."""

    FAULT = 0x21
    """Inexplicable program fault."""

    CLIENT = 0x22
    """Client side program errors."""

    ADMIN = 0x23
    """Server administrative action required."""

    CONFIG = 0x24
    """Client configuration is inadequate."""

    UPGRADE = 0x25
    """Client or server too old to interact."""

    COMM = 0x26
    """Communications error."""

    TOOBIG = 0x27
    """Too big to handle."""


PERFORCE_DATE_FORMAT = "%Y/%m/%d %H:%M:%S"


class UserType(Enum):
    """Types of user enum."""

    STANDARD = "standard"
    OPERATOR = "operator"
    SERVICE = "service"


class AuthMethod(Enum):
    """User authentication enum."""

    PERFORCE = "perforce"
    PERFORCE_2FA = "perforce+2fa"
    LDAP = "ldap"
    LDAP_2FA = "ldap+2fa"


@alias_dataclass
class User:
    """A Perforce user specification.

    Command:
        `p4 user`_
    """

    access: dt.datetime = field(metadata={"alias": "Access"}, repr=False)
    """The date and time this user last ran a Helix Server command."""

    auth_method: AuthMethod = field(
        metadata={"alias": "AuthMethod"}, repr=False)
    email: str = field(metadata={"alias": "Email"})
    full_name: str = field(metadata={"alias": "FullName"})
    type: UserType = field(metadata={"alias": "Type"})

    update: dt.datetime = field(metadata={"alias": "Update"}, repr=False)
    """The date and time this user was last updated."""

    name: str = field(metadata={"alias": "User"})

    job_view: str | None = field(
        metadata={"alias": "JobView"}, default=None, repr=False)
    """Jobs the user is automatically interested in."""

    password: str | None = field(
        metadata={"alias": "Password"}, default=None, repr=False)
    """Masked password field (``******``) when the password is set."""

    reviews: list[str] = field(
        metadata={"collect": indexed("Reviews")},
        default_factory=list,
        repr=False,
    )
    """Files the user is interested in reviewing (``Reviews#``)."""


class SubmitOptions(Enum):
    """Options to govern the default behavior of ``p4 submit``."""

    SUBMIT_UNCHANGED = "submitunchanged"
    SUBMIT_UNCHANGED_AND_REOPEN = "submitunchanged+reopen"
    REVERT_UNCHANGED = "revertunchanged"
    REVERT_UNCHANGED_AND_REOPEN = "revertunchanged+reopen"
    LEAVE_UNCHANGED = "leaveunchanged"
    LEAVE_UNCHANGED_AND_REOPEN = "leaveunchanged+reopen"


class ClientType(Enum):
    """Types of client workspace."""

    STANDARD = "writeable"
    OPERATOR = "readonly"
    SERVICE = "partitioned"


class View(NamedTuple):
    """A perforce `View specification`_."""

    left: str
    right: str

    @staticmethod
    def from_string(string: str) -> View:
        """New instance from a view string.

        Args:
            string: view string.

        Returns:
            View from given string.

        Example:
            >>> View.from_string("//depot/foo/... //ws/bar/...")
            View(left='//depot/foo/...', right='//ws/bar/...')
        """
        return View(*shlex.split(string))


@alias_dataclass
class ClientOptions:
    """A set of switches that control particular `Client options`_.

    .. _Client options:
        https://www.perforce.com/manuals/cmdref/Content/CmdRef/p4_client.html#Options2
    """

    allwrite: bool
    clobber: bool
    compress: bool
    locked: bool
    modtime: bool
    rmdir: bool

    @classmethod
    def from_string(cls, string: str) -> ClientOptions:
        """Instantiate class from an option line returned by p4.

        Args:
            string: A string containing the options, e.g.
                "allwrite clobber compress locked modtime rmdir".

        Returns:
            ClientOptions

        """
        data = set(string.split())
        return cls(
            allwrite="allwrite" in data,
            clobber="clobber" in data,
            compress="compress" in data,
            locked="locked" in data,
            modtime="modtime" in data,
            rmdir="rmdir" in data,
        )

    def __str__(self) -> str:
        options = [
            "allwrite" if self.allwrite else "noallwrite",
            "clobber" if self.clobber else "noclobber",
            "compress" if self.compress else "nocompress",
            "locked" if self.locked else "unlocked",
            "modtime" if self.modtime else "nomodtime",
            "rmdir" if self.rmdir else "normdir",
        ]
        return " ".join(options)


@alias_dataclass
class Client:
    """A Perforce client workspace specification.

    Command:
        `p4 client`_
    """

    name: str = field(metadata={"alias": "Client"})
    description: str = field(metadata={"alias": "Description"}, repr=False)

    options: ClientOptions = field(metadata={"alias": "Options"}, repr=False)

    owner: str = field(metadata={"alias": "Owner"})
    """The name of the user who owns the workspace."""

    root: pathlib.Path = field(metadata={"alias": "Root"})
    """Workspace root directory on the local host

    All the file in `views` are relative to this directory.
    """

    submit_options: SubmitOptions = field(
        metadata={"alias": "SubmitOptions"}, repr=False)
    type: ClientType = field(metadata={"alias": "Type"})

    views: list[View] = field(
        metadata={"collect": indexed("View", View.from_string)},
        default_factory=list,
    )
    """Specifies the mappings between files in the depot
    and files in the workspace (``View#``)."""

    host: str | None = field(metadata={"alias": "Host"}, default=None)
    """The name of the workstation on which this workspace resides."""

    access: dt.datetime | None = field(
        metadata={"alias": "Access"}, default=None, repr=False)
    """The date and time that the workspace was last used in any way.

    ``None`` for a workspace that doesn't exist yet.
    """

    update: dt.datetime | None = field(
        metadata={"alias": "Update"}, default=None, repr=False)
    """The date the workspace specification was last modified.

    ``None`` for a workspace that doesn't exist yet.
    """

    stream: str | None = field(metadata={"alias": "Stream"}, default=None)

    stream_at_change: int | None = field(
        metadata={"alias": "StreamAtChange"}, default=None)
    """Stream view is fixed at this changelist."""

    line_end: str | None = field(metadata={"alias": "LineEnd"}, default=None)

    backup: str | None = field(metadata={"alias": "Backup"}, default=None)

    server_id: str | None = field(
        metadata={"alias": "ServerID"}, default=None, repr=False)

    alt_roots: list[str] = field(
        metadata={"collect": indexed("AltRoots")},
        default_factory=list,
        repr=False,
    )
    """Alternate workspace roots (``AltRoots#``)."""

    change_view: list[str] = field(
        metadata={"collect": indexed("ChangeView")},
        default_factory=list,
        repr=False,
    )
    """Paths limited to a changelist (``ChangeView#``)."""

    @staticmethod
    def client_from_data(**data: Any) -> Client:  # ruff: ignore[any-type]
        """Create a Client instance from a ``p4 client -o`` record.

        Kept for backward compatibility, ``Client(**data)`` does the same.

        Returns:
            Client: A Client instance with the data from the dictionary.

        """
        return Client(**data)


class ChangeStatus(Enum):
    """Types of changelist status."""

    NEW = "new"
    PENDING = "pending"
    SHELVED = "shelved"
    SUBMITTED = "submitted"


class ChangeType(Enum):
    """Types of changelist."""

    RESTRICTED = "restricted"
    PUBLIC = "public"


@alias_dataclass
class Change:
    """A Perforce changelist specification.

    Command:
        `p4 change`_
    """

    change: int | Literal["new"] = field(metadata={"alias": "Change"})
    """Changelist number, ``new`` for a not yet created changelist."""

    client: str = field(metadata={"alias": "Client"})
    description: str = field(metadata={"alias": "Description"}, repr=False)
    status: ChangeStatus = field(metadata={"alias": "Status"})
    type: ChangeType = field(metadata={"alias": "Type"})

    user: str = field(metadata={"alias": "User"})
    """Name of the change owner."""

    date: dt.datetime | None = field(
        metadata={"alias": "Date"}, default=None)
    """Date the changelist was last modified. ``None`` for new changelist."""

    files: list[str] = field(
        metadata={"collect": indexed("Files")},
        default_factory=list,
        repr=False,
    )
    """The list of files being submitted in this changelist (``Files#``)."""

    jobs: list[str] = field(
        metadata={"collect": indexed("Jobs")},
        default_factory=list,
        repr=False,
    )
    """Jobs fixed by this changelist (``Jobs#``)."""

    stream: str | None = field(metadata={"alias": "Stream"}, default=None)
    imported_by: str | None = field(
        metadata={"alias": "ImportedBy"}, default=None, repr=False)
    identity: str | None = field(
        metadata={"alias": "Identity"}, default=None, repr=False)

    shelve_access: dt.datetime | None = field(
        metadata={"alias": "shelveAccess"},
        default=None,
    )
    shelve_update: dt.datetime | None = field(
        metadata={"alias": "shelveUpdate"},
        default=None,
    )


@alias_dataclass
class ChangeInfo:
    """A Perforce changelist.

    Compared to a `Change`, this model does not contain
    files in the changelist.

    Command:
        `p4 changes`_
    """

    change: int = field(metadata={"alias": "change"})
    client: str = field(metadata={"alias": "client"})

    date: dt.datetime = field(metadata={"alias": "time"})
    """Date the changelist was last modified."""

    description: str = field(metadata={"alias": "desc"}, repr=False)
    status: ChangeStatus = field(metadata={"alias": "status"})
    type: ChangeType = field(metadata={"alias": "changeType"})

    user: str = field(metadata={"alias": "user"})
    """Name of the change owner."""

    path: str | None = field(default=None)
    stream: str | None = field(default=None)
    streamStatus: str | None = field(default=None)  # ruff: ignore[mixed-case-variable-in-class-scope]
    oldChange: int | None = field(default=None)  # ruff: ignore[mixed-case-variable-in-class-scope]
    """Original number of a renumbered changelist."""

    shelved: bool = field(default=False)
    """Pending changelist has shelved files."""


class Action(Enum):
    """A file action."""

    ADD = "add"
    EDIT = "edit"
    DELETE = "delete"
    BRANCH = "branch"
    MOVE_ADD = "move/add"
    MOVE_DELETE = "move/delete"
    INTEGRATE = "integrate"
    IMPORT = "import"
    PURGE = "purge"
    ARCHIVE = "archive"


@alias_dataclass(frozen=True)
class ActionMessage:
    """Information on a file during an action operation.

    Actions can be, for example, ``add``, ``edit`` or ``remove``.

    Notable messages:
        - "can't add (already opened for edit)"
        - "can't add existing file"
        - "empty, assuming text."
        - "also opened by user@client"
    """

    path: str
    message: str
    level: MessageLevel

    @classmethod
    def from_info_data(cls, data: PerforceDict) -> ActionMessage:
        """Create instance from an 'info' dict of an action command.

        Args:
            data (PerforceDict): The 'info' dict from an action command.

        Returns:
            ActionMessage: An instance of ActionMessage.

        """
        path, _, message = data["data"].rpartition(" - ")
        level = MessageLevel(int(data["level"]))
        return cls(
            path=path.strip(),
            message=message.strip(),
            level=level,
        )


@alias_dataclass
class ActionInfo:
    """The result of an action operation.

    Actions can be, for example, ``add``, ``edit`` or ``remove``.
    """

    action: str
    client_file: str = field(metadata={"alias": "clientFile"})
    depot_file: str = field(metadata={"alias": "depotFile"})
    file_type: str = field(metadata={"alias": "type"})
    work_rev: int = field(metadata={"alias": "workRev"})
    """Open revision."""


@alias_dataclass(frozen=True)
class Integration:
    """Integration record of a file revision (``p4 filelog``)."""

    how: str
    """How the revision was integrated, e.g. ``branch from``."""

    file: str
    """The other file of the integration."""

    start_rev: str
    """Start revision of the other file, e.g. ``#none``."""

    end_rev: str
    """End revision of the other file, e.g. ``#3``."""


@alias_dataclass
class Revision:
    """A file revision information.

    Command:
        `p4 filelog`_
    """

    action: Action
    """The operation the file was open for."""

    change: int
    """The number of the submitting changelist."""

    client: str
    depot_file: str = field(metadata={"alias": "depotFile"})
    description: str = field(metadata={"alias": "desc"}, repr=False)
    revision: int = field(metadata={"alias": "rev"})
    time: dt.datetime

    file_type: str = field(metadata={"alias": "type"})

    user: str
    """The name of the user who submitted the revision."""

    digest: str | None = field(default=None, repr=False)
    """MD5 digest of the file. ``None`` if ``action`` is `Action.DELETE`."""

    file_size: int | None = field(metadata={"alias": "fileSize"}, default=None)
    """File length in bytes. ``None`` if ``action`` is `Action.DELETE`."""

    integrations: list[Integration] = field(
        metadata={"collect": indexed_group(Integration, {
            "how": "how",
            "file": "file",
            "srev": "start_rev",
            "erev": "end_rev",
        })},
        default_factory=list,
        repr=False,
    )
    """Integration records (``how#,#``, ``file#,#``, ``srev#,#``...)."""


@alias_dataclass
class Sync:
    """The result of a file sync operation."""

    action: str
    """Sync action, e.g. ``added``, ``updated``, ``deleted``."""

    client_file: str = field(metadata={"alias": "clientFile"})
    depot_file: str = field(metadata={"alias": "depotFile"})
    revision: int = field(metadata={"alias": "rev"})

    file_size: int | None = field(metadata={"alias": "fileSize"}, default=None)
    """File length in bytes. ``None`` for deleted files."""

    change: int | None = field(default=None)
    """Changelist of the synced revision."""


@alias_dataclass(frozen=True)
class OtherOpen:
    """Other Open information."""

    action: Action
    change: int | Literal["default"]
    user: str
    client: str


def _other_open_from_data(
    open_by: str, action: str, change: str = "default"
) -> OtherOpen:
    user, _, client = open_by.partition("@")
    return OtherOpen(action=action, change=change, user=user, client=client)


@alias_dataclass(frozen=True)
class ResolveInfo:
    """Pending resolve record of an opened file (``p4 fstat``)."""

    action: str | None = None
    base_file: str | None = None
    base_rev: str | None = None
    from_file: str | None = None
    start_from_rev: str | None = None
    end_from_rev: str | None = None


@alias_dataclass
class HeadInfo:
    """Head revision information."""

    action: Action = field(metadata={"alias": "headAction"})
    change: int = field(metadata={"alias": "headChange"})
    revision: int = field(metadata={"alias": "headRev"})
    """Revision number.

    If you used a `Revision specifier`_ in your query, this field is set to the
    specified value. Otherwise, it's the head revision.

    .. _Revision specifier:
        https://www.perforce.com/manuals/cmdref/Content/CmdRef/filespecs.html#Using_revision_specifiers
    """

    file_type: str = field(metadata={"alias": "headType"})
    time: dt.datetime = field(metadata={"alias": "headTime"})
    """Revision **changelist** time."""

    mod_time: dt.datetime | None = field(
        metadata={"alias": "headModTime"}, default=None)
    """Revision modification time.

    The time that the file was last modified on the client before submit.
    """

    charset: str | None = field(
        metadata={"alias": "headCharset"}, default=None)
    """Head charset, for unicode type files."""


@alias_dataclass
class FStat:
    """A file information.

    Command:
        `p4 fstat`_, `p4 opened`_
    """

    depot_file: str = field(metadata={"alias": "depotFile"})
    client_file: str | None = field(
        metadata={"alias": "clientFile"}, default=None)
    """Local path (``fstat``) or client syntax path (``opened``).

    ``None`` if the file is not mapped to the workspace.
    """

    moved_file: str | None = field(
        metadata={"alias": "movedFile"}, default=None)
    """The name of the file this file was moved from/to."""

    moved_rev: int | None = field(metadata={"alias": "movedRev"}, default=None)

    head: HeadInfo | None = field(
        metadata={"collect": nested(HeadInfo)}, default=None)
    """Head revision (``head*`` keys). ``None`` if not in depot yet."""

    have_rev: int | None = field(metadata={"alias": "haveRev"}, default=None)
    """Revision last synced to workspace, if on workspace."""
    rev: int | None = field(metadata={"alias": "rev"}, default=None)
    """Revision number (``p4 opened``)."""

    action: Action | None = field(metadata={"alias": "action"}, default=None)
    """Action performed on the file, if opened."""

    action_owner: str | None = field(
        metadata={"alias": "actionOwner"}, default=None)
    """User who opened the file, if opened."""

    work_rev: int | None = field(metadata={"alias": "workRev"}, default=None)
    """Open revision, if opened."""

    change: int | Literal["default"] | None = field(
        metadata={"alias": "change"}, default=None)
    """Open changelist number or ``default``, if opened."""

    type: str | None = field(metadata={"alias": "type"}, default=None)
    """Open file type, if opened."""

    charset: str | None = field(default=None)
    user: str | None = field(metadata={"alias": "user"}, default=None)
    client: str | None = field(metadata={"alias": "client"}, default=None)

    description: str | None = field(
        metadata={"alias": "desc"}, default=None, repr=False)
    """Changelist description (``fstat -e``)."""

    digest: str | None = field(default=None, repr=False)
    """MD5 digest of the file (``fstat -Ol``)."""

    file_size: int | None = field(metadata={"alias": "fileSize"}, default=None)
    """File length in bytes (``fstat -Ol``)."""

    is_mapped: bool = field(metadata={"alias": "isMapped"}, default=False)
    """Is the file is mapped to client workspace."""

    shelved: bool = field(default=False)
    """Is the file shelved."""

    our_lock: bool = field(metadata={"alias": "ourLock"}, default=False)
    """Is the file locked by the current user."""

    other_lock: bool = field(metadata={"alias": "otherLock"}, default=False)
    """Is the file locked by another user."""

    other_locks: list[str] = field(
        metadata={"collect": indexed("otherLock")},
        default_factory=list,
        repr=False,
    )
    """``user@client`` holding the lock (``otherLock#``)."""

    other_open_count: int | None = field(
        metadata={"alias": "otherOpen"}, default=None)
    """Number of other users who have the file open."""

    others_open: list[OtherOpen] | None = field(
        metadata={"collect": indexed_group(_other_open_from_data, {
            "otherOpen": "open_by",
            "otherAction": "action",
            "otherChange": "change",
        })},
        default=None,
    )
    """Other users who have the file open (``otherOpen#``...)."""

    resolved: int | None = field(default=None)
    """Number of resolved integration records."""

    unresolved: int | None = field(default=None)
    """Number of unresolved integration records."""

    reresolvable: int | None = field(default=None)
    """Number of reresolvable integration records."""

    resolves: list[ResolveInfo] = field(
        metadata={"collect": indexed_group(ResolveInfo, {
            "resolveAction": "action",
            "resolveBaseFile": "base_file",
            "resolveBaseRev": "base_rev",
            "resolveFromFile": "from_file",
            "resolveStartFromRev": "start_from_rev",
            "resolveEndFromRev": "end_from_rev",
        })},
        default_factory=list,
        repr=False,
    )
    """Pending resolve records (``resolve*#``)."""

    attributes: dict[str, str] = field(
        metadata={"collect": prefixed("attr-")},
        default_factory=dict,
        repr=False,
    )
    """File attributes (``fstat -Oa``)."""

    attribute_props: dict[str, str] = field(
        metadata={"collect": prefixed("attrProp-")},
        default_factory=dict,
        repr=False,
    )

    open_attributes: dict[str, str] = field(
        metadata={"collect": prefixed("openattr-")},
        default_factory=dict,
        repr=False,
    )
    """Attributes of the opened file (``fstat -Oa``)."""

    open_attribute_props: dict[str, str] = field(
        metadata={"collect": prefixed("openattrProp-")},
        default_factory=dict,
        repr=False,
    )


@dataclass
class UncommittedChange:
    """Row of uncommitted work in a client workspace.

    Unifies files opened in any changelist (`FStat`) and pending
    changelists without opened files (`ChangeInfo`), e.g. empty or with
    shelved files only.
    """

    change: int | Literal["default"] | None
    action: str | None = None
    client_file: str | None = None
    depot_file: str | None = None
    user: str | None = None
    description: str | None = None

    @classmethod
    def from_fstat(cls, fstat: FStat) -> UncommittedChange:
        """Create row from an opened file.

        Returns:
            Uncommitted change of the file.

        """
        return cls(
            change=fstat.change,
            action=fstat.action.value if fstat.action else None,
            client_file=fstat.client_file,
            depot_file=fstat.depot_file,
            user=fstat.user,
        )

    @classmethod
    def from_change_info(cls, change: ChangeInfo) -> UncommittedChange:
        """Create row from a pending changelist.

        Returns:
            Uncommitted change of the changelist.

        """
        return cls(
            change=change.change,
            user=change.user,
            description=change.description,
        )

    def __str__(self) -> str:
        if self.client_file:
            return f"[{self.action}]\t{self.client_file}"
        description = (self.description or "").strip()
        return f"[change {self.change}]\t{description}"

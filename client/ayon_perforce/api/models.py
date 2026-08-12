"""P4 models.

This is taken from https://github.com/tahv/pyforce but modified
so it doesn't depend on pydantic as that is binary dependency unusable
in current dependency package AYON architecture and various DCC environments.

To support pydantic aliases, some hacky stuff is introduced, but p4
isn't really consistent on the names it returns, so lesser evil was chosen.

"""
from __future__ import annotations

import datetime
import shlex
from dataclasses import dataclass, field, fields
from enum import Enum
from typing import (
    TYPE_CHECKING,
    Any,
    Literal,
    NamedTuple,
    TypeVar,
    get_args,
    get_origin,
    get_type_hints,
)

if TYPE_CHECKING:
    import pathlib

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
    "MarshalCode",
    "MessageSeverity",
    "OtherOpen",
    "PerforceDict",
    "Revision",
    "SubmitOptions",
    "Sync",
    "User",
    "UserType",
    "View",
    "datetime_to_perforce_date",
    "perforce_date_to_datetime",
    "perforce_datetime_to_timestamp",
    "perforce_timestamp_to_datetime",
]


PerforceDict = dict[str, str]

R = TypeVar("R")


def perforce_date_to_datetime(string: str) -> datetime.datetime:
    """Convert p4 date to datetime.

    Args:
        string: date string.

    Returns:
        datetime.datetime with UTC timezone.

    """
    utc = datetime.timezone.utc
    return datetime.datetime.strptime(string, PERFORCE_DATE_FORMAT).replace(
        tzinfo=utc
    )


def perforce_timestamp_to_datetime(time: str) -> datetime.datetime:
    """Convert p4 timestamp to datetime.

    Args:
        time: timestamp.

    Returns:
        datetime.datetime with UTC timezone.

    """
    return datetime.datetime.fromtimestamp(int(time), tz=datetime.timezone.utc)


def perforce_datetime_to_timestamp(date: datetime.datetime) -> str:
    """Convert datetime to timestamp.

    Args:
        date: datetime object.

    Returns:
        timestamp for p4.

    """
    return str(round(date.timestamp()))


def datetime_to_perforce_date(date: datetime.datetime) -> str:
    """Convert datetime to p4 date.

    Args:
        date: datetime instance.

    Returns:
        p4 date string.

    """
    return date.strftime(PERFORCE_DATE_FORMAT)


def _is_datetime_annotation(annotation: Any) -> bool:  # ruff: ignore[any-type]
    if annotation is datetime.datetime:
        return True
    origin = get_origin(annotation)
    if origin is None:
        return False
    return any(
        arg is not type(None) and _is_datetime_annotation(arg)
        for arg in get_args(annotation)
    )


def _coerce_perforce_datetime(value: str) -> datetime.datetime:
    if value.lstrip("-").isdigit():
        return perforce_timestamp_to_datetime(value)
    return perforce_date_to_datetime(value)


def _enable_alias_kwargs(cls: type[R]) -> type[R]:
    """Allow dataclass constructor kwargs by field name and alias.

    Args:
        cls: dataclass with aliases.

    Returns:
        Modified class.

    """
    try:
        type_hints = get_type_hints(cls)
    except NameError:
        # This can happen if the dataclass has forward references.
        # In that case, we can't do any type checking,
        # so just return the class.
        return cls
    datetime_fields = {
        name
        for name, annotation in type_hints.items()
        if _is_datetime_annotation(annotation)
    }
    alias_to_name = {
        alias: data_field.name
        for data_field in fields(cls)
        if (alias := data_field.metadata.get("alias"))
        and alias != data_field.name
    }
    if not alias_to_name:
        return cls

    original_init = cls.__init__

    def __init__(self, *args: Any, **kwargs: Any) -> None:  # ruff: ignore[any-type, dunder-function-name, missing-type-function-argument]
        """Constructor to handle aliases.

        Raises:
            TypeError: when alias and property name is the same.
        """
        for alias, name in alias_to_name.items():
            if alias not in kwargs:
                continue
            if name in kwargs:
                msg = f"{cls.__name__} got both {name!r} and alias {alias!r}"
                raise TypeError(msg)
            kwargs[name] = kwargs.pop(alias)
        for name in datetime_fields:
            value = kwargs.get(name)
            if isinstance(value, str):
                kwargs[name] = _coerce_perforce_datetime(value)
        original_init(self, *args, **kwargs)

    cls.__init__ = __init__
    return cls


def alias_dataclass(_cls: type[R] | None = None, **kwargs: Any):  # ruff: ignore[any-type, missing-return-type-private-function]
    """Dataclass decorator with support for alias kwargs in constructor.

    This is poor-man substitution for pydantic aliases. It breaks mypy
    unfortunately, but it works for runtime. It also supports datetime fields
    to be passed as strings in either p4 date format or timestamp format.

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
    LDAP = "ldap"


@alias_dataclass
class User:
    """A Perforce user specification."""

    access: datetime.datetime = field(metadata={"alias": "Access"}, repr=False)
    """The date and time this user last ran a Helix Server command."""

    auth_method: AuthMethod = field(
        metadata={"alias": "AuthMethod"}, repr=False)
    email: str = field(metadata={"alias": "Email"})
    full_name: str = field(metadata={"alias": "FullName"})
    type: UserType = field(metadata={"alias": "Type"})

    update: datetime.datetime = field(metadata={"alias": "Update"}, repr=False)
    """The date and time this user was last updated."""

    name: str = field(metadata={"alias": "User"})


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
            "locked" if self.locked else "nolocked",
            "modtime" if self.modtime else "nomodtime",
            "rmdir" if self.rmdir else "normdir",
        ]
        return " ".join(options)


@alias_dataclass
class Client:
    """A Perforce client workspace specification."""

    access: datetime.datetime = field(metadata={"alias": "Access"}, repr=False)
    """The date and time that the workspace was last used in any way."""

    name: str = field(metadata={"alias": "Client"})
    description: str = field(metadata={"alias": "Description"}, repr=False)

    host: str = field(metadata={"alias": "Host"})
    """The name of the workstation on which this workspace resides."""

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

    update: datetime.datetime = field(
        metadata={"alias": "Update"}, repr=False)
    """The date the workspace specification was last modified."""

    views: list[View]
    """Specifies the mappings between files in the depot
    and files in the workspace."""

    stream: str | None = field(metadata={"alias": "Stream"}, default=None)


class ChangeStatus(Enum):
    """Types of changelist status."""

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

    change: int = field(metadata={"alias": "Change"})
    client: str = field(metadata={"alias": "Client"})

    date: datetime.datetime = field(metadata={"alias": "Date"})
    """Date the changelist was last modified."""

    description: str = field(metadata={"alias": "Description"}, repr=False)
    status: ChangeStatus = field(metadata={"alias": "Status"})
    type: ChangeType = field(metadata={"alias": "Type"})

    user: str = field(metadata={"alias": "User"})
    """Name of the change owner."""

    files: list[str] = field(repr=False)
    """The list of files being submitted in this changelist."""

    shelve_access: datetime.datetime | None = field(
        metadata={"alias": "shelveAccess"},
        default=None,
    )
    shelve_update: datetime.datetime | None = field(
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

    date: datetime.datetime = field(metadata={"alias": "time"})
    """Date the changelist was last modified."""

    description: str = field(metadata={"alias": "desc"}, repr=False)
    status: ChangeStatus = field(metadata={"alias": "status"})
    type: ChangeType = field(metadata={"alias": "changeType"})

    user: str = field(metadata={"alias": "user"})
    """Name of the change owner."""

    path: str | None = field(default=None)
    stream: str | None = field(default=None)
    streamStatus: str | None = field(default=None)  # ruff: ignore[mixed-case-variable-in-class-scope]
    oldChange: str | None = field(default=None)  # ruff: ignore[mixed-case-variable-in-class-scope]


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


@alias_dataclass
class Revision:
    """A file revision information."""

    action: Action
    """The operation the file was open for."""

    change: int
    """The number of the submitting changelist."""

    client: str
    depot_file: str = field(metadata={"alias": "depotFile"})
    description: str = field(metadata={"alias": "desc"}, repr=False)
    revision: int = field(metadata={"alias": "rev"})
    time: datetime.datetime

    file_type: str = field(metadata={"alias": "type"})

    user: str
    """The name of the user who submitted the revision."""

    digest: str | None = field(default=None, repr=False)
    """MD5 digest of the file. ``None`` if ``action`` is `Action.DELETE`."""

    file_size: int | None = field(default=None)
    """File length in bytes. ``None`` if ``action`` is `Action.DELETE`."""


@alias_dataclass
class Sync:
    """The result of a file sync operation."""

    action: str
    client_file: str = field(metadata={"alias": "clientFile"})
    depot_file: str = field(metadata={"alias": "depotFile"})
    revision: int = field(metadata={"alias": "rev"})

    file_size: int = field(metadata={"alias": "fileSize"})


@alias_dataclass(frozen=True)
class OtherOpen:
    """Other Open information."""

    action: Action
    change: int | Literal["default"]
    user: str
    client: str


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
    time: datetime.datetime = field(metadata={"alias": "headTime"})
    """Revision **changelist** time."""

    mod_time: datetime.datetime = field(metadata={"alias": "headModTime"})
    """Revision modification time.

    The time that the file was last modified on the client before submit.
    """


@alias_dataclass
class FStat:
    """A file information."""

    client_file: str = field(metadata={"alias": "clientFile"})
    depot_file: str = field(metadata={"alias": "depotFile"})
    head: HeadInfo | None = field(metadata={"alias": "head"}, default=None)

    have_rev: int | None = field(metadata={"alias": "haveRev"}, default=None)
    """Revision last synced to workspace, if on workspace."""

    is_mapped: bool = field(metadata={"alias": "isMapped"}, default=False)
    """Is the file is mapped to client workspace."""

    others_open: list[OtherOpen] | None = field(default=None)

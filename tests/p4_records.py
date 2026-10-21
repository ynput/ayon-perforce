"""Sample ``p4 -G`` records used across tests.

Two kinds of samples live here:

``CAPTURED_*``
    Key sets captured from a real Helix Core server (2026-10-20) via
    ``p4 -G``. Values were anonymized, keys and value formats are unchanged.

``DOCUMENTED_*``
    Key sets built from the tagged-output fields described in the
    `P4 Command Reference`_ for cases that could not be captured with
    read-only commands (opened files, integrations, deletes, ...).

Every value is a ``str`` except ``severity``/``generic``/``level`` style
fields, which ``p4 -G`` emits as integers.

.. _P4 Command Reference:
    https://www.perforce.com/manuals/cmdref/Content/CmdRef/Home-cmdref.html
"""

from __future__ import annotations

WS = "ws_test"
ROOT = "C:/p4/ws_test"
STREAM = "//stream_depot/main"

# ---------------------------------------------------------------------------
# p4 user -o
# ---------------------------------------------------------------------------
CAPTURED_USER_SPEC = {
    "code": "stat",
    "User": "artist",
    "Email": "artist@studio",
    "Update": "2023/02/10 16:17:48",
    "Access": "2026/10/20 08:35:33",
    "FullName": "Artist Name",
    "Type": "standard",
    "AuthMethod": "perforce",
}

DOCUMENTED_USER_SPEC_WITH_REVIEWS = {
    **CAPTURED_USER_SPEC,
    "JobView": "status=open",
    "Reviews0": "//depot/project/...",
    "Reviews1": "//depot/other/...",
}

# ``p4 user -o <name>`` for a user that doesn't exist returns a template
# spec without ``Update``/``Access``.
DOCUMENTED_USER_SPEC_MISSING = {
    "code": "stat",
    "User": "nobody",
    "Email": "nobody@host",
    "FullName": "nobody",
    "Type": "standard",
    "AuthMethod": "perforce",
}

# ---------------------------------------------------------------------------
# p4 client -o
# ---------------------------------------------------------------------------
CAPTURED_CLIENT_SPEC_STREAM = {
    "code": "stat",
    "Client": WS,
    "Update": "2026/08/13 14:21:49",
    "Access": "2026/10/16 17:10:18",
    "Owner": "artist",
    "Host": "WORKSTATION",
    "Description": "Created by Ayon Launcher Hook\n",
    "Root": ROOT,
    "Options": "noallwrite noclobber nocompress unlocked nomodtime rmdir",
    "SubmitOptions": "submitunchanged",
    "LineEnd": "local",
    "Stream": STREAM,
    "View0": f"{STREAM}/... //{WS}/...",
    "Type": "writeable",
    "Backup": "enable",
}

DOCUMENTED_CLIENT_SPEC_CLASSIC = {
    "code": "stat",
    "Client": WS,
    "Update": "2026/08/13 14:21:49",
    "Access": "2026/10/16 17:10:18",
    "Owner": "artist",
    "Host": "WORKSTATION",
    "Description": "Classic workspace\n",
    "Root": ROOT,
    "AltRoots0": "/mnt/p4/ws_test",
    "Options": "allwrite clobber compress locked modtime normdir",
    "SubmitOptions": "revertunchanged",
    "LineEnd": "local",
    "View0": f"//depot/project/... //{WS}/project/...",
    "View1": f'"//depot/with space/..." "//{WS}/with space/..."',
    "ChangeView0": "//depot/project/...@120",
    "Type": "writeable",
    "Backup": "enable",
}

DOCUMENTED_CLIENT_SPEC_STREAM_AT_CHANGE = {
    **CAPTURED_CLIENT_SPEC_STREAM,
    "StreamAtChange": "120",
    "ServerID": "edge-1",
}

# ``p4 client -o`` for a not yet existing workspace has no Update/Access.
DOCUMENTED_CLIENT_SPEC_NEW = {
    key: value
    for key, value in CAPTURED_CLIENT_SPEC_STREAM.items()
    if key not in {"Update", "Access"}
}

# ---------------------------------------------------------------------------
# p4 change -o
# ---------------------------------------------------------------------------
DOCUMENTED_CHANGE_SPEC_PENDING = {
    "code": "stat",
    "Change": "130",
    "Date": "2026/10/20 08:40:00",
    "Client": WS,
    "User": "artist",
    "Status": "pending",
    "Type": "public",
    "Description": "Work in progress\n",
    "Files0": f"{STREAM}/a.txt",
    "Files1": f"{STREAM}/b.txt",
}

DOCUMENTED_CHANGE_SPEC_PENDING_WITH_JOBS = {
    **DOCUMENTED_CHANGE_SPEC_PENDING,
    "Jobs0": "job000001",
}

DOCUMENTED_CHANGE_SPEC_SUBMITTED = {
    "code": "stat",
    "Change": "128",
    "Date": "2026/09/21 12:49:04",
    "Client": WS,
    "User": "artist",
    "Status": "submitted",
    "Type": "public",
    "Description": "\n#changelist validated\r\n",
}

DOCUMENTED_CHANGE_SPEC_SHELVED = {
    **DOCUMENTED_CHANGE_SPEC_PENDING,
    "Status": "shelved",
    "shelveAccess": "2026/10/20 08:41:00",
    "shelveUpdate": "2026/10/20 08:41:00",
}

# ``p4 change -o`` (new changelist template). Files lists files opened in
# the default changelist.
DOCUMENTED_CHANGE_SPEC_NEW = {
    "code": "stat",
    "Change": "new",
    "Client": WS,
    "User": "artist",
    "Status": "new",
    "Description": "<enter description here>\n",
    "Type": "public",
    "Files0": f"{STREAM}/a.txt",
}

DOCUMENTED_CHANGE_UNKNOWN_ERROR = {
    "code": "error",
    "data": "Change 99999 unknown.\n",
    "severity": 3,
    "generic": 2,
}

# ---------------------------------------------------------------------------
# p4 changes
# ---------------------------------------------------------------------------
CAPTURED_CHANGES_SUBMITTED = {
    "code": "stat",
    "change": "128",
    "time": "1789994944",
    "user": "artist",
    "client": WS,
    "status": "submitted",
    "changeType": "public",
    "path": f"{STREAM}/Content/*",
    "desc": "\n#changelist validated\r\n",
}

CAPTURED_CHANGES_PENDING = {
    "code": "stat",
    "change": "101",
    "time": "1739292772",
    "user": "artist",
    "client": "unreal",
    "status": "pending",
    "changeType": "public",
    "desc": "ad_unreal_UE54_hero.json2025021",
}

DOCUMENTED_CHANGES_PENDING_SHELVED_FILES = {
    **CAPTURED_CHANGES_PENDING,
    "shelved": "",
}

DOCUMENTED_CHANGES_RESTRICTED = {
    **CAPTURED_CHANGES_SUBMITTED,
    "changeType": "restricted",
}

DOCUMENTED_CHANGES_RENUMBERED = {
    **CAPTURED_CHANGES_SUBMITTED,
    "oldChange": "125",
}

# ---------------------------------------------------------------------------
# p4 fstat
# ---------------------------------------------------------------------------
CAPTURED_FSTAT_SYNCED = {
    "code": "stat",
    "depotFile": f"{STREAM}/.p4ignore",
    "clientFile": f"{ROOT}\\.p4ignore",
    "isMapped": "",
    "headAction": "add",
    "headType": "text",
    "headTime": "1789137512",
    "headRev": "1",
    "headChange": "121",
    "headModTime": "1789137384",
    "haveRev": "1",
}

DOCUMENTED_FSTAT_OPENED_FOR_EDIT = {
    **CAPTURED_FSTAT_SYNCED,
    "action": "edit",
    "actionOwner": "artist",
    "change": "default",
    "type": "text",
    "workRev": "1",
}

DOCUMENTED_FSTAT_OPENED_FOR_ADD = {
    "code": "stat",
    "depotFile": f"{STREAM}/new.txt",
    "clientFile": f"{ROOT}\\new.txt",
    "isMapped": "",
    "action": "add",
    "actionOwner": "artist",
    "change": "130",
    "type": "text",
    "workRev": "1",
}

DOCUMENTED_FSTAT_OTHER_OPEN = {
    **CAPTURED_FSTAT_SYNCED,
    "otherOpen0": "bob@bob_ws",
    "otherAction0": "edit",
    "otherChange0": "default",
    "otherOpen": "1",
    "otherLock0": "bob@bob_ws",
    "otherLock": "",
}

DOCUMENTED_FSTAT_LOCKED_AND_RESOLVE = {
    **DOCUMENTED_FSTAT_OPENED_FOR_EDIT,
    "ourLock": "",
    "unresolved": "1",
    "resolveAction0": "content",
    "resolveBaseFile0": f"{STREAM}/.p4ignore",
    "resolveBaseRev0": "1",
    "resolveFromFile0": "//stream_depot/dev/.p4ignore",
    "resolveStartFromRev0": "1",
    "resolveEndFromRev0": "2",
}

DOCUMENTED_FSTAT_FILE_SIZE_DIGEST = {
    **CAPTURED_FSTAT_SYNCED,
    "fileSize": "6468",
    "digest": "77C5D456C17E241F1A2446359389CB8D",
}

DOCUMENTED_FSTAT_MOVED = {
    **CAPTURED_FSTAT_SYNCED,
    "action": "move/add",
    "actionOwner": "artist",
    "change": "130",
    "type": "text",
    "workRev": "1",
    "movedFile": f"{STREAM}/old_name.txt",
    "movedRev": "1",
    "headCharset": "utf8",
}

DOCUMENTED_FSTAT_SHELVED = {
    **DOCUMENTED_FSTAT_OPENED_FOR_ADD,
    "shelved": "",
}

DOCUMENTED_FSTAT_NO_SUCH_FILE_WARNING = {
    "code": "error",
    "data": f"{STREAM}/missing.txt - no such file(s).\n",
    "severity": 2,
    "generic": 17,
}

# ---------------------------------------------------------------------------
# p4 opened
# ---------------------------------------------------------------------------
DOCUMENTED_OPENED_DEFAULT = {
    "code": "stat",
    "depotFile": f"{STREAM}/a.txt",
    "clientFile": f"//{WS}/a.txt",
    "rev": "3",
    "haveRev": "3",
    "action": "edit",
    "change": "default",
    "type": "text",
    "user": "artist",
    "client": WS,
}

DOCUMENTED_OPENED_LOCKED_MOVED = {
    **DOCUMENTED_OPENED_DEFAULT,
    "action": "move/add",
    "ourLock": "",
    "movedFile": f"{STREAM}/old_a.txt",
}

# ---------------------------------------------------------------------------
# p4 filelog
# ---------------------------------------------------------------------------
CAPTURED_FILELOG_TWO_REVISIONS = {
    "code": "stat",
    "depotFile": f"{STREAM}/Content/instance.uasset",
    "rev0": "5",
    "change0": "128",
    "action0": "edit",
    "type0": "binary+l",
    "time0": "1789994944",
    "user0": "artist",
    "client0": WS,
    "fileSize0": "3086",
    "digest0": "A655DFAEC4A204456FD100647B49EC31",
    "desc0": " #changelist validated  ",
    "rev1": "4",
    "change1": "127",
    "action1": "edit",
    "type1": "binary+l",
    "time1": "1789994023",
    "user1": "artist",
    "client1": WS,
    "fileSize1": "3086",
    "digest1": "A655DFAEC4A204456FD100647B49EC31",
    "desc1": " #changelist validated  ",
}

DOCUMENTED_FILELOG_INTEGRATED = {
    "code": "stat",
    "depotFile": f"{STREAM}/a.txt",
    "rev0": "1",
    "change0": "90",
    "action0": "branch",
    "type0": "text",
    "time0": "1739292772",
    "user0": "artist",
    "client0": WS,
    "fileSize0": "12",
    "digest0": "0CC175B9C0F1B6A831C399E269772661",
    "desc0": "Populate main",
    "how0,0": "branch from",
    "file0,0": "//stream_depot/dev/a.txt",
    "srev0,0": "#none",
    "erev0,0": "#1",
}

DOCUMENTED_FILELOG_DELETED = {
    "code": "stat",
    "depotFile": f"{STREAM}/gone.txt",
    "rev0": "2",
    "change0": "95",
    "action0": "delete",
    "type0": "text",
    "time0": "1739292999",
    "user0": "artist",
    "client0": WS,
    "desc0": "Remove file",
    "rev1": "1",
    "change1": "94",
    "action1": "add",
    "type1": "text",
    "time1": "1739292888",
    "user1": "artist",
    "client1": WS,
    "fileSize1": "5",
    "digest1": "5D41402ABC4B2A76B9719D911017C592",
    "desc1": "Add file",
}

# ---------------------------------------------------------------------------
# p4 sync
# ---------------------------------------------------------------------------
CAPTURED_SYNC_UP_TO_DATE_WARNING = {
    "code": "error",
    "data": f"{STREAM}/... - file(s) up-to-date.\n",
    "severity": 2,
    "generic": 17,
    "severityformat": "warning",
    "code0": 554768772,
    "fmt0": "[%argc% - file(s)|File(s)] up-to-date.",
}

DOCUMENTED_SYNC_FIRST = {
    "code": "stat",
    "depotFile": f"{STREAM}/a.txt",
    "clientFile": f"{ROOT}\\a.txt",
    "rev": "3",
    "action": "updated",
    "fileSize": "120",
    "totalFileSize": "240",
    "totalFileCount": "2",
    "change": "128",
}

DOCUMENTED_SYNC_NEXT = {
    "code": "stat",
    "depotFile": f"{STREAM}/b.txt",
    "clientFile": f"{ROOT}\\b.txt",
    "rev": "1",
    "action": "added",
    "fileSize": "120",
    "change": "128",
}

DOCUMENTED_SYNC_DELETED = {
    "code": "stat",
    "depotFile": f"{STREAM}/gone.txt",
    "clientFile": f"{ROOT}\\gone.txt",
    "rev": "2",
    "action": "deleted",
    "change": "128",
}

# ---------------------------------------------------------------------------
# p4 add / edit / delete
# ---------------------------------------------------------------------------
DOCUMENTED_EDIT_RESULT = {
    "code": "stat",
    "depotFile": f"{STREAM}/a.txt",
    "clientFile": f"{ROOT}\\a.txt",
    "workRev": "4",
    "action": "edit",
    "type": "text",
}

DOCUMENTED_ADD_RESULT = {
    **DOCUMENTED_EDIT_RESULT,
    "depotFile": f"{STREAM}/new.txt",
    "clientFile": f"{ROOT}\\new.txt",
    "workRev": "1",
    "action": "add",
}

DOCUMENTED_DELETE_RESULT = {
    **DOCUMENTED_EDIT_RESULT,
    "action": "delete",
}

DOCUMENTED_INFO_ALSO_OPENED = {
    "code": "info",
    "data": f"{STREAM}/a.txt - also opened by bob@bob_ws",
    "level": 0,
}

CAPTURED_EDIT_NOT_ON_CLIENT_WARNING = {
    "code": "error",
    "data": f"{STREAM}/nonexist.txt - file(s) not on client.\n",
    "severity": 2,
    "generic": 17,
    "severityformat": "warning",
    "code0": 554768760,
    "fmt0": "[%argc% - file(s)|File(s)] not on client.",
}

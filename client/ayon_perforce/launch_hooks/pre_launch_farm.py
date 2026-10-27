"""Pre-launch hook resetting Perforce workspace on farm render jobs."""
from __future__ import annotations

import subprocess
from copy import deepcopy
from typing import ClassVar, List, Optional, Tuple, Union

from ayon_applications import (
    LaunchTypes,
    PreLaunchHook,
)
from ayon_core.lib import StringTemplate
from ayon_core.lib.ayon_info import get_workstation_info
from ayon_core.pipeline.template_data import get_template_data
from ayon_perforce.backend.communication_server import WebServer


def call_command(
    command: List[str],
    from_stdin: Optional[str] = None,
    *,
    start_new_session: bool = False,
    as_raw: bool = False,
) -> Union[List[str], subprocess.CompletedProcess, None]:
    """Run a command and return its output.

    Args:
        command (List[str]): Command and its arguments.
        from_stdin (Optional[str]): Text passed to the command's stdin.
        start_new_session (bool): Start the process in a new session.
        as_raw (bool): Return the ``CompletedProcess`` instead of the
            output lines.

    Returns:
        Union[List[str], subprocess.CompletedProcess, None]: Lines of
            stdout followed by stderr, the completed process if ``as_raw``
            is set, or None if the command failed.

    """
    try:
        if from_stdin:
            result = subprocess.run(
                command,
                input=from_stdin,
                capture_output=True,
                text=True,
                check=True,
                start_new_session=start_new_session,
            )
        else:
            result = subprocess.run(
                command,
                capture_output=True,
                text=True,
                check=True,
                start_new_session=start_new_session,
            )
        if not as_raw:
            stdout = result.stdout.splitlines() or []
            stderr = result.stderr.splitlines() or []
            result = stdout + stderr
    except subprocess.CalledProcessError as e:
        print(f"Error: {e}")  # noqa: T201 - no logger in module function
        return None
    else:
        return result


def get_from_workspaceinfo(
    ws_info: List[str],
) -> Tuple[Optional[str], Optional[str]]:
    """Get workspace and stream name from ``p4 info`` output.

    Args:
        ws_info (List[str]): Output lines of ``p4 info``.

    Returns:
        Tuple[Optional[str], Optional[str]]: Workspace (client) name and
            stream name, None for those not found.

    """
    ws = None
    stream = None
    for line in ws_info:
        if "Client name:" in line:
            ws = line.split(":")[-1].strip()
        if "Client stream:" in line:
            stream = line.split(":")[-1].strip()
    return (ws, stream)


def get_p4_data_from_env(env: dict[str, str]) -> dict[str, str]:
    """Collect Perforce data of the render job from the environment.

    Args:
        env (dict[str, str]): Launch environment.

    Returns:
        dict[str, str]: Workspace name (if set), stream and changelist.

    Raises:
        ValueError: If no Perforce data, or not the stream and changelist,
            are in the environment.

    """
    env_keys = {
        "AYON_P4_WORKSPACE": "workspace_name",
        "AYON_P4_STREAM": "stream",
        "AYON_P4_CHANGELIST": "changelist",
    }
    p4_data = {
        data_key: env[env_key]
        for env_key, data_key in env_keys.items()
        if env_key in env
    }
    if not p4_data:
        msg = "No Perforce data found in environment"
        raise ValueError(msg)

    missing = [
        env_key
        for env_key, data_key in env_keys.items()
        if data_key in {"stream", "changelist"} and data_key not in p4_data
    ]
    if missing:
        msg = f"Missing Perforce data in environment: {', '.join(missing)}"
        raise ValueError(msg)
    return p4_data


def set_workspace(ws_name: str) -> None:
    """Set the current Perforce workspace (P4CLIENT).

    Args:
        ws_name (str): Workspace name.

    Raises:
        RuntimeError: If the workspace could not be set.

    """
    msg = "Failed to set workspace"
    try:
        # call_command returns None when p4 fails and raises when it can't
        # run at all (e.g. p4 is missing)
        result = call_command(["p4", "set", f"P4CLIENT={ws_name}"])
    except Exception as err:
        raise RuntimeError(msg) from err
    if result is None:
        raise RuntimeError(msg)


class PerforcePreLaunchFarmHook(PreLaunchHook):
    """Handle workspace reset to commit on remote render jobs."""

    hosts: ClassVar[set[str]] = {"unreal"}
    launch_types: ClassVar[set[str]] = {LaunchTypes.farm_publish}

    def execute(self) -> None:
        """Switch to the render node workspace and sync to the changelist.

        Raises:
            ValueError: If Perforce data are missing in the environment,
                the workspace template can't be solved or the stream
                differs.
            RuntimeError: If the workspace could not be set.

        """
        env = deepcopy(self.data["env"])
        publish_job = env.get("AYON_PUBLISH_JOB")
        if publish_job and int(publish_job) > 0:
            return

        if not env.get("PERFORCE_WEBSERVER_URL"):
            p4_webserver = WebServer()
            p4_webserver.start()

        template_data = get_template_data(
            self.data["project_entity"],
            folder_entity=self.data["folder_entity"],
            task_entity=self.data["task_entity"],
            host_name=self.host_name,
            settings=self.data["project_settings"],
        )
        template_data["ext"] = "uproject"
        template_data["workstation_info"] = get_workstation_info()
        self.log.info("template_data = %r", template_data)

        p4_data = get_p4_data_from_env(env)
        self.log.info("p4_data = %s", p4_data)

        # find render node's workspace
        p4_settings = self.data["project_settings"]["perforce"]
        ws_settings = p4_settings["workspace"]
        ws_template = StringTemplate(ws_settings["template"])
        ws_name = ws_template.format(template_data)
        if not ws_name.solved:
            msg = "Workspace template could not be solved"
            raise ValueError(msg)

        # get current workspace? -> nah just checkout the correct one already
        set_workspace(ws_name)

        # get current clientinfo for newly checked out workspace
        curr_ws_info = call_command(["p4", "info"])
        if curr_ws_info is None:
            msg = "Failed to get workspace info"
            raise RuntimeError(msg)
        self.log.info("curr_ws_info = %r", curr_ws_info)
        curr_ws, curr_stream = get_from_workspaceinfo(curr_ws_info)
        self.log.info("curr_ws = %r", curr_ws)
        self.log.info("curr_stream = %r", curr_stream)

        # check if we're on the correct stream
        if curr_stream != p4_data["stream"]:
            # assumes workspaces are checked out to the correct stream
            # per default
            msg = "Switching stream is not yet supported"
            raise ValueError(msg)

        # revert any changes
        revert_result = call_command(["p4", "revert", "//..."])
        self.log.info("revert_result = %r", revert_result)

        # sync to changelist
        sync_result = call_command(["p4", "sync", f"@{p4_data['changelist']}"])
        self.log.info("sync_result = %r", sync_result)

"""Set the workspace for farm."""
from __future__ import annotations

from copy import deepcopy
from typing import TYPE_CHECKING, ClassVar

from ayon_applications import (
    LaunchTypes,
    PreLaunchHook,
)
from ayon_core.lib import StringTemplate
from ayon_core.lib.ayon_info import get_workstation_info
from ayon_core.pipeline.template_data import get_template_data
from ayon_perforce.api.commands import P4Commands


def get_from_workspaceinfo(
        ws_info: dict[str, str]) -> tuple[str | None, str | None]:
    """Get workspace and stream from workspace info.

    Args:
        ws_info (dict[str, str]): Dictionary containing workspace info.

    Returns:
        tuple[str | None, str | None]: Workspace and stream names.

    """
    ws = None
    stream = None
    for key, value in ws_info.items():
        if "Client:" in key:
            ws = value.strip()
        if "Stream:" in key:
            stream = value.strip()
    return ws, stream


class P4WorkspaceError(Exception):
    """Raised on P4 workspace failure."""


class P4StreamError(Exception):
    """Raised on P4 stream failure."""


class PerforcePreLaunchFarmHook(PreLaunchHook):
    """Handle workspace reset to commit on remote render jobs."""

    # TODO(antirotor): Fix type annotations in ayon-core with `ClassVar`
    hosts: ClassVar[set[str]] = {"unreal"}
    launch_types: ClassVar[set[str]] = {LaunchTypes.farm_publish}

    def execute(self) -> None:
        """Execute the hook.

        Raises:
            P4WorkspaceError: If the workspace template could not be solved
                or if setting the workspace fails.
            P4StreamError: If no Perforce data is found in the environment
                or if switching streams is not supported.
            ValueError: When missing data for template resolution.

        """
        env = deepcopy(self.data["env"])
        if int(env.get("AYON_PUBLISH_JOB", 0)) > 0:
            return

        if not self.host_name:
            msg = "Cannot determine host name."
            raise ValueError(msg)

        anatomy = self.data["anatomy"]
        ue_tmpl = anatomy.get_template_item("work", "unreal")
        template_data = get_template_data(
            self.data["project_entity"],
            folder_entity=self.data["folder_entity"],
            task_entity=self.data["task_entity"],
            host_name=self.host_name,
            settings=self.data["project_settings"],
        )
        template_data["ext"] = "uproject"
        template_data["workstation_info"] = get_workstation_info()
        # uproject_name = Path(ue_tmpl.format(template_data)["file"]).stem
        self.log.debug(f"Template data: {template_data = }")  # noqa: G004

        p4_data = {}
        for key in env:
            if key == "AYON_P4_WORKSPACE":
                p4_data["workspace_name"] = env[key]
            if key == "AYON_P4_STREAM":
                p4_data["stream"] = env[key]
            if key == "AYON_P4_CHANGELIST":
                p4_data["changelist"] = env[key]

        if not p4_data:
            msg = "No Perforce data found in environment"
            raise ValueError(msg)
        self.log.debug(f"Perforce data: {p4_data = }")  # noqa: G004

        # find render node's workspace
        p4_settings = self.data["project_settings"]["perforce"]
        ws_settings = p4_settings["workspace"]
        ws_template = StringTemplate(ws_settings["template"])
        ws_name = ws_template.format(template_data)
        if not ws_name.solved:
            msg = "Workspace template could not be solved"
            raise ValueError(msg)

        # get current workspace? -> nah just checkout the correct one already
        try:
            P4Commands.run_p4("set", f"P4CLIENT={ws_name}")
        except Exception as e:
            msg = "Failed to set workspace"
            raise P4WorkspaceError(msg) from e

        # get current clientinfo for newly checked out workspace
        curr_ws_info = P4Commands.run_p4("info")[0]
        self.log.debug(f"Workspace Info: {curr_ws_info = }")  # noqa: G004
        curr_ws, curr_stream = get_from_workspaceinfo(curr_ws_info)
        self.log.debug(f"Current Workspace: {curr_ws = }")  # noqa: G004
        self.log.debug(f"Current Stream: {curr_stream = }")  # noqa: G004

        # check if we're on the correct stream
        if curr_stream != p4_data["stream"]:
            # assumes workspaces are checked out
            # to the correct stream per default
            msg = "Switching stream is not yet supported"
            raise P4StreamError(msg)

        # revert any changes
        revert_result = P4Commands.run_p4("revert", "//...")
        self.log.debug(f"Revert result: {revert_result = }")  # noqa: G004

        # sync to changelist
        sync_result = P4Commands.run_p4("sync", f"@{p4_data['changelist']}")
        self.log.debug(f"Sync result: {sync_result = }")  # noqa: G004

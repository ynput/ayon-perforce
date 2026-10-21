"""Set the workspace for farm."""
from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import ClassVar

from ayon_applications import (
    LaunchTypes,
    PreLaunchHook,
)
from ayon_core.lib import StringTemplate
from ayon_core.lib.ayon_info import get_workstation_info
from ayon_core.pipeline.template_data import get_template_data
from ayon_perforce.api.commands import P4Commands
from ayon_perforce.api.exceptions import ClientNotFoundError
from ayon_perforce.api.models import MessageSeverity


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
            P4WorkspaceError: If the workspace doesn't exist or setting
                the workspace fails.
            P4StreamError: If the workspace is on a different stream than
                the one sent by the submitter (``AYON_P4_STREAM``).
            ValueError: When missing data for template resolution
                or the workspace template could not be solved.

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
        workstation_info = get_workstation_info()
        template_data["workstation"] = workstation_info
        template_data["workstation_info"] = workstation_info
        uproject_name = Path(ue_tmpl.format(template_data)["file"]).stem
        self.log.debug("Template data: %s", template_data)

        # optional overrides sent by the submitter
        env_stream = env.get("AYON_P4_STREAM")
        changelist = env.get("AYON_P4_CHANGELIST")
        self.log.debug(
            "Perforce env data: stream=%s, changelist=%s",
            env_stream, changelist)

        # find render node's workspace
        p4_settings = self.data["project_settings"]["perforce"]
        ws_settings = p4_settings["workspace"]
        ws_template = StringTemplate(ws_settings["template"])
        ws_name = ws_template.format(template_data)
        if not ws_name.solved:
            msg = "Workspace template could not be solved"
            raise ValueError(msg)

        # workspace must exist on the render node, its stream is used
        # unless the submitter sent one
        try:
            client = P4Commands().get_client(str(ws_name))
        except ClientNotFoundError as e:
            msg = f"Workspace `{ws_name}` does not exist on the server."
            raise P4WorkspaceError(msg) from e
        self.log.debug("Workspace: %s", client)

        if env_stream and client.stream and env_stream != client.stream:
            # assumes workspaces are checked out
            # to the correct stream per default
            msg = (
                f"Workspace `{ws_name}` is on stream `{client.stream}` "
                f"but `{env_stream}` is required. "
                "Switching stream is not yet supported"
            )
            raise P4StreamError(msg)
        if env_stream and not client.stream:
            self.log.warning(
                "Workspace `%s` is not a stream workspace, ignoring "
                "requested stream `%s`.", ws_name, env_stream)
        self.log.debug("Stream: %s", client.stream)

        # get current workspace? -> nah just checkout the correct one already
        try:
            P4Commands.run_p4("set", f"P4CLIENT={ws_name}")
        except Exception as e:
            msg = "Failed to set workspace"
            raise P4WorkspaceError(msg) from e

        # revert any changes, "file(s) not opened" is just a warning
        revert_result = P4Commands.run_p4(
            "revert", "//...", max_severity=MessageSeverity.WARNING)
        self.log.debug("Revert result: %s", revert_result)

        # sync to changelist, or to head if submitter didn't send one
        if changelist:
            sync_args = ["sync", f"@{changelist}"]
        else:
            self.log.warning(
                "AYON_P4_CHANGELIST is not set, syncing to head revision.")
            sync_args = ["sync"]
        sync_result = P4Commands.run_p4(
            *sync_args, max_severity=MessageSeverity.WARNING)
        self.log.debug("Sync result: %s", sync_result)

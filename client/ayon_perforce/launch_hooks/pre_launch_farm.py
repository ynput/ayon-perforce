"""Set the workspace for farm."""
from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import TYPE_CHECKING, ClassVar

from ayon_applications import (
    LaunchTypes,
    PreLaunchHook,
)
from ayon_core.lib import StringTemplate
from ayon_core.lib.ayon_info import get_workstation_info
from ayon_core.pipeline.template_data import get_template_data
from ayon_perforce.addon import is_perforce_enabled
from ayon_perforce.api.commands import P4Commands
from ayon_perforce.api.exceptions import ClientNotFoundError
from ayon_perforce.api.models import MessageSeverity

if TYPE_CHECKING:
    from ayon_perforce.api.models import Client


class P4WorkspaceError(Exception):
    """Raised on P4 workspace failure."""


class P4StreamError(Exception):
    """Raised on P4 stream failure."""


class PerforcePreLaunchFarmHook(PreLaunchHook):
    """Handle workspace reset to commit on remote render jobs."""

    # TODO(antirotor): Fix type annotations in ayon-core with `ClassVar`
    hosts: ClassVar[set[str]] = {"unreal"}
    launch_types: ClassVar[set[str]] = {LaunchTypes.farm_publish}

    def execute(self) -> None:  # ruff: ignore[too-many-locals]
        """Execute the hook.

        Raises:
            P4WorkspaceError: If the workspace doesn't exist or setting
                the workspace fails.
            ValueError: When missing data for template resolution
                or the workspace template could not be solved.

        """
        env = deepcopy(self.data["env"])
        if int(env.get("AYON_PUBLISH_JOB", 0)) > 0:
            return
        if not is_perforce_enabled(self.data["project_settings"]):
            self.log.info("Perforce is not enabled for project, skipping")
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

        self._check_stream(client, env_stream)

        # process-local, `p4 set` would switch the workspace of concurrent
        # jobs on this render node too
        P4Commands.set_client(str(ws_name))

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

        # used by Deadline GlobalJobPreLoad to sync the workspace for every
        # task, extracted environment (and this hook) can be cached per host
        self.launch_context.env["AYON_P4_WORKSPACE"] = str(ws_name)
        self.launch_context.env["P4CLIENT"] = str(ws_name)
        self._set_unreal_plugin_env(client.root, uproject_name)

    def _check_stream(self, client: Client, env_stream: str | None) -> None:
        """Check the workspace is on the stream requested by the submitter.

        Raises:
            P4StreamError: If the workspace is on a different stream.

        """
        if env_stream and client.stream and env_stream != client.stream:
            # assumes workspaces are checked out
            # to the correct stream per default
            msg = (
                f"Workspace `{client.name}` is on stream `{client.stream}` "
                f"but `{env_stream}` is required. "
                "Switching stream is not yet supported"
            )
            raise P4StreamError(msg)
        if env_stream and not client.stream:
            self.log.warning(
                "Workspace `%s` is not a stream workspace, ignoring "
                "requested stream `%s`.", client.name, env_stream)
        self.log.debug("Stream: %s", client.stream)

    def _set_unreal_plugin_env(
            self, workspace_root: Path, uproject_name: str) -> None:
        """Point the Deadline UnrealEngine5 plugin to this render node.

        The plugin prefers these environment variables over ``ProjectFile``
        and ``Executable`` plugin info, which are paths on the machine that
        submitted the job.

        Args:
            workspace_root: Root of the synced workspace, it is the Unreal
                project directory (see `PerforceWorkspaceCreationHook`).
            uproject_name: Unreal project name.

        """
        env = self.launch_context.env
        uproject = Path(workspace_root) / f"{uproject_name}.uproject"
        if uproject.is_file():
            env["ProjectRoot"] = Path(workspace_root).as_posix()
            env["UnrealUProject"] = uproject.as_posix()
            self.log.debug("Unreal project: %s", uproject)
        else:
            self.log.warning(
                "Unreal project `%s` not found in workspace, "
                "plugin info `ProjectFile` will be used.", uproject)

        # render with the command line editor of the configured application
        executable = Path(str(self.launch_context.executable))
        cmd_executable = executable.with_name(
            f"{executable.stem}-Cmd{executable.suffix}")
        if cmd_executable.is_file():
            executable = cmd_executable
        if executable.is_file():
            env["UnrealExecutable"] = executable.as_posix()
            self.log.debug("Unreal executable: %s", executable)

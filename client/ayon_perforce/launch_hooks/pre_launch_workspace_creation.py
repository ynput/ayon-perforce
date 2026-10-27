"""Handle Perforce workspace creation on any machine."""
from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import ClassVar

from ayon_applications import (
    LaunchTypes,
    PreLaunchHook,
)
from ayon_core.lib import StringTemplate, ayon_info
from ayon_core.pipeline import Anatomy
from ayon_core.pipeline.template_data import get_template_data_with_names
from ayon_perforce.addon import is_perforce_enabled
from ayon_perforce.api.commands import P4Commands
from ayon_perforce.lib import P4Workspace, get_local_login


class PerforceWorkspaceCreationHook(PreLaunchHook):
    """Handle workspace reset to commit on remote render jobs."""

    app_groups: ClassVar[set[str]] = {"unreal"}
    launch_types: ClassVar[set[str]] = {
        LaunchTypes.farm_publish,
        LaunchTypes.local,
    }
    order: ClassVar[float] = -5.2

    def execute(self) -> None:  # ruff: ignore[too-many-locals]
        """Handle Auto Workspace Creation on any machine.

        Raises:
            RuntimeError: If workspace creation or switching fails.

        """
        env = deepcopy(self.data["env"])
        if (publish_job := env.get("AYON_PUBLISH_JOB")) and int(publish_job) > 0:  # ruff: ignore[line-too-long]
            return

        # project data
        proj = self.data["project_entity"]
        proj_setts = self.data["project_settings"]
        if not is_perforce_enabled(proj_setts):
            self.log.info("Perforce is not enabled for project, skipping")
            return
        proj_name = self.data["project_entity"]["name"]
        anatomy = Anatomy(project_name=proj_name)
        task = self.data["task_entity"]
        workstation_info = ayon_info.get_workstation_info()

        # template data
        tmpl_data = get_template_data_with_names(
            proj_name,
            folder_path=self.data["folder_entity"]["path"],
            task_name=task["name"],
        )
        tmpl_data.update({
            "workstation": workstation_info,
            "workstation_info": workstation_info,
            "root": anatomy.roots,
        })
        tmpls = proj["config"]["templates"]

        # get p4username from registry
        owner, password = get_local_login()
        if not owner:
            msg = "Cannot determine p4 user."
            raise RuntimeError(msg)

        # workspace template
        ws_tmpl = proj_setts["perforce"]["workspace"]["template"]
        ws_tmpl = StringTemplate(ws_tmpl)
        ws_name = ws_tmpl.format_strict(tmpl_data)
        self.log.info("%s", ws_name)

        # use server from settings, not the ambient p4 configuration
        p4_setts = proj_setts["perforce"]
        self.log.info(
            "Perforce server: %s:%s", p4_setts["host_name"], p4_setts["port"])
        P4Commands.login(
            host=p4_setts["host_name"],
            port=p4_setts["port"],
            username=owner,
            password=password,
            workspace_name=str(ws_name),
        )

        # workspace root template
        # "file": "{project[code]}.{ext}",
        # "directory": "{root[local]}/perforce"
        ws_root_dir = Path(tmpls["work"]["unreal"]["directory"])
        ws_root_file = tmpls["work"]["unreal"]["file"].split(".")[0]
        ws_root_dir /= ws_root_file  # ue always creates a subdir for a project
        ws_root_tmpl = StringTemplate(ws_root_dir.as_posix())
        ws_root = Path(ws_root_tmpl.format_strict(tmpl_data)).as_posix()
        self.log.info("%s", ws_root)

        strm_name = self._get_stream_name(
            proj_setts["perforce"]["stream"], task["taskType"], tmpl_data)

        # depot template
        depot_tmpl = proj_setts["perforce"]["workspace"]["depot"]
        depot_tmpl = StringTemplate(depot_tmpl)
        depot_name = depot_tmpl.format_strict(tmpl_data)
        self.log.info("%s", depot_name)

        # Unreal needs writable files, p4 makes synced files read-only
        ws_options = proj_setts["perforce"]["workspace"].get(
            "options", ["allwrite", "clobber", "rmdir"])

        is_remote_run = self.launch_context.launch_type != LaunchTypes.local
        try:
            req_ws = P4Workspace(
                name=ws_name,
                root=ws_root,
                stream=strm_name,
                owner=owner,
                depot=depot_name,
                host=workstation_info["hostname"],
                options=list(ws_options),
            )
            self.log.info("%s", req_ws)
            # on farm `PerforcePreLaunchFarmHook` syncs to the job changelist
            # on farm, don't change the workspace of concurrent jobs
            req_ws.switch(
                force=is_remote_run,
                sync=not is_remote_run,
                persist=not is_remote_run,
            )
        except Exception as err:
            errmsg = f"Failed to checkout workspace. {err}"
            raise RuntimeError(errmsg) from err

    def _get_stream_name(
        self,
        stream_settings: dict,
        task_type: str,
        tmpl_data: dict,
    ) -> str | None:
        """Get stream name from stream profiles.

        Returns:
            Stream name or ``None`` if streams are disabled, a classic
            workspace is used then.

        Raises:
            RuntimeError: If no profile matches the task type.

        """
        if not stream_settings["enabled"]:
            return None
        strm_tmpl: StringTemplate | None = None
        for profile in stream_settings["profiles"]:
            if task_type in profile["task_types"]:
                strm_tmpl = StringTemplate(profile["template"])
        if not strm_tmpl:
            errmsg = f"Task type {task_type} not found in stream profiles."
            raise RuntimeError(errmsg)
        strm_name = strm_tmpl.format_strict(tmpl_data)
        self.log.info("%s", strm_name)
        return strm_name

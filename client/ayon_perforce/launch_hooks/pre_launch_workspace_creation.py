from copy import deepcopy
from typing import ClassVar, Optional
from pathlib import Path

from ayon_applications import (
    LaunchTypes,
    PreLaunchHook,
)
from ayon_core.lib import StringTemplate, ayon_info
from ayon_core.pipeline import Anatomy
from ayon_core.pipeline.template_data import get_template_data_with_names

from ayon_perforce.lib import P4Workspace, get_local_login


class PerforceWorkspaceCreationHook(PreLaunchHook):
    """Handle workspace reset to commit on remote render jobs."""

    app_groups: ClassVar = {"unreal"}
    launch_types: ClassVar = {
        LaunchTypes.farm_publish,
        LaunchTypes.local,
    }
    order = -5.2

    def execute(self) -> None:
        """Handle Auto Workspace Creation on any machine."""
        env = deepcopy(self.data["env"])
        if publish_job := env.get("AYON_PUBLISH_JOB"):
            if int(publish_job) > 0:
                return

        # project data
        proj = self.data["project_entity"]
        proj_setts = self.data["project_settings"]
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
            "workstation_info": workstation_info,
            "root": anatomy.roots,
        })
        tmpls = proj["config"]["templates"]

        # get p4username from registry
        owner = get_local_login()[0]

        # workspace template
        ws_tmpl = proj_setts["perforce"]["workspace"]["template"]
        ws_tmpl = StringTemplate(ws_tmpl)
        ws_name = ws_tmpl.format_strict(tmpl_data)
        self.log.info(f"{ws_name = }")

        # workspace root template
        # "file": "{project[code]}.{ext}",
        # "directory": "{root[local]}/perforce"
        ws_root_dir = Path(tmpls["work"]["unreal"]["directory"])
        ws_root_file = tmpls["work"]["unreal"]["file"].split(".")[0]
        ws_root_dir /= ws_root_file  # ue always creates a subdir for a project
        ws_root_tmpl = StringTemplate(ws_root_dir.as_posix())
        ws_root = Path(ws_root_tmpl.format_strict(tmpl_data)).as_posix()
        self.log.info(f"{ws_root = }")

        # stream template
        strm_tmpl: StringTemplate = None
        for profile in proj_setts["perforce"]["stream"]["profiles"]:
            if task["taskType"] in profile["task_types"]:
                strm_tmpl = StringTemplate(profile["template"])
        if not strm_tmpl:
            errmsg = (
                f"Task type {task['taskType']} not found in stream profiles."
            )
            raise RuntimeError(errmsg)
        strm_name = strm_tmpl.format_strict(tmpl_data)
        self.log.info(f"{strm_name = }")

        # depot template
        depot_tmpl = proj_setts["perforce"]["workspace"]["depot"]
        depot_tmpl = StringTemplate(depot_tmpl)
        depot_name = depot_tmpl.format_strict(tmpl_data)
        self.log.info(f"{depot_name = }")

        is_remote_run = self.launch_context.launch_type != LaunchTypes.local
        try:
            req_ws = P4Workspace(
                name=ws_name,
                root=ws_root,
                stream=strm_name,
                owner=owner,
                depot=depot_name,
                host=workstation_info["hostname"],
                options=["rmdir"]
            )
            self.log.info(f"{req_ws = }")
            req_ws.switch(force=is_remote_run)
        except Exception as err:
            errmsg = f"Failed to checkout workspace. {err}"
            raise RuntimeError(errmsg) from err

        # api: assuming username is used in template we can make username optional

        # api: call_command from ayon
        # create if necessary
        # set $P4CLIENT here?

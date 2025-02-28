import shutil
from copy import deepcopy
from pathlib import Path

from ayon_applications import (
    PreLaunchHook,
    LaunchTypes,
)

from ayon_core.pipeline.template_data import get_template_data

from ayon_perforce import P4_Workspace


class PerforcePreLaunchFarmHook(PreLaunchHook):
    """Handle workspace reset to commit on remote render jobs."""

    hosts = {"unreal"}
    launch_types = {LaunchTypes.farm_publish}

    def execute(self):
        env = deepcopy(self.data["env"])
        if publish_job := env.get("AYON_PUBLISH_JOB"):
            if int(publish_job) > 0:
                return

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
        uproject_name = Path(ue_tmpl.format(template_data)["file"]).stem

        p4_data = {}
        for key in env.keys():
            if key == "PERFORCE_WORKSPACE":
                p4_data["workspace_name"] = env[key]
            if key == "PERFORCE_STREAM":
                p4_data["stream"] = env[key]
            if key == "PERFORCE_CHANGELIST":
                p4_data["commit"] = env[key]

        return
        if not p4_data:
            raise ValueError("No diversion data found in environment")

        ws = P4_Workspace(name=p4_data["workspace_name"])
        # ws.checkout_stream(p4_data["stream"])
        ws.revert_to_changelist(p4_data["changelist"])

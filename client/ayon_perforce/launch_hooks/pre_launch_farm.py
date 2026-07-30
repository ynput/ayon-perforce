import shutil
import subprocess
from copy import deepcopy
from pathlib import Path
from typing import List

from ayon_applications import (
    LaunchTypes,
    PreLaunchHook,
)
from ayon_core.lib import StringTemplate
from ayon_core.lib.ayon_info import get_workstation_info
from ayon_core.pipeline.template_data import get_template_data

from client.ayon_perforce.api.commands import P4Commands



def get_from_workspaceinfo(ws_info: List[str]):
    ws = None
    stream = None
    for line in ws_info:
        if "Client name:" in line:
            ws = line.split(":")[-1].strip()
        if "Client stream:" in line:
            stream = line.split(":")[-1].strip()
    return (ws, stream)


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
        template_data["workstation_info"] = get_workstation_info()
        #uproject_name = Path(ue_tmpl.format(template_data)["file"]).stem
        print(f"{template_data = }")

        p4_data = {}
        for key in env.keys():
            if key == "AYON_P4_WORKSPACE":
                p4_data["workspace_name"] = env[key]
            if key == "AYON_P4_STREAM":
                p4_data["stream"] = env[key]
            if key == "AYON_P4_CHANGELIST":
                p4_data["changelist"] = env[key]

        if not p4_data:
            raise ValueError("No Perforce data found in environment")
        print(f"{p4_data = }")

        # find render node's workspace
        p4_settings = self.data["project_settings"]["perforce"]
        ws_settings = p4_settings["workspace"]
        ws_template = StringTemplate(ws_settings["template"])
        ws_name = ws_template.format(template_data)
        if not ws_name.solved:
            raise ValueError("Workspace template could not be solved")

        # get current workspace? -> nah just checkout the correct one already
        try:
            P4Commands.run_p4("set", f"P4CLIENT={ws_name}")  #! can fail -> wrap try/catch
        except Exception:
            raise Exception("Failed to set workspace")

        # get current clientinfo for newly checked out workspace
        curr_ws_info = P4Commands.run_p4("info")
        print(f"{curr_ws_info = }")
        curr_ws, curr_stream = get_from_workspaceinfo(curr_ws_info)
        print(f"{curr_ws = }")
        print(f"{curr_stream = }")

        # check if we're on the correct stream
        if not curr_stream == p4_data["stream"]:
            # assumes workspaces are checked out to the correct stream per default
            raise ValueError("Switching stream is not yet supported")

        # revert any changes
        revert_result = P4Commands.run_p4("revert", "//...")
        print(f"{revert_result = }")

        # sync to changelist
        sync_result = P4Commands.run_p4("sync", f"@{p4_data['changelist']}")
        print(f"{sync_result = }")

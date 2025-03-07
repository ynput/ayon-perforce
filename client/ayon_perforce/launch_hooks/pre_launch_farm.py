import shutil
from copy import deepcopy
from pathlib import Path
import subprocess
from typing import List

from ayon_applications import (
    PreLaunchHook,
    LaunchTypes,
)

from ayon_core.pipeline.template_data import get_template_data

from ayon_perforce.rest.communication_server import WebServer
from ayon_perforce.rest.perforce.rest_stub import PerforceRestStub  # throws `Unknown Perforce Serve` error


def call_command(
    command: List[str], from_stdin=None, start_new_session=False, as_raw=False
):
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

        return result
    except subprocess.CalledProcessError as e:
        print(f"Error: {e}")
        return None


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

        if not env.get("PERFORCE_WEBSERVER_URL"):
            p4_webserver = WebServer()
            p4_webserver.start()

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
        # TODO: build and resolve template
        ws_template = "{project_code}_{host_name}_{user_name}"
        # ws_name = "uemc_BEPIC-DEVNODE01_Tony.Dorfmeister" # example for resolved ws template
        ws_name = "bepic_BEPIC-DEVNODE01_mainline_8543"
    
        # get current workspace? -> nah just checkout the correct one already
        try:
            call_command(["p4", "set", f"P4CLIENT={ws_name}"])  #! can fail -> wrap try/catch
        except Exception:
            raise Exception("Failed to set workspace")

        # get current clientinfo for newly checked out workspace
        curr_ws_info = call_command(["p4", "info"])
        print(f"{curr_ws_info = }")
        curr_ws, curr_stream = get_from_workspaceinfo(curr_ws_info)
        print(f"{curr_ws = }")
        print(f"{curr_stream = }")

        # check if we're on the correct stream
        if not curr_stream == p4_data["stream"]:
            # assumes workspaces are checked out to the correct stream per default
            raise ValueError("Switching stream is not yet supported")

        # revert any changes
        revert_result = call_command(["p4", "revert", "//..."])
        print(f"{revert_result = }")

        # sync to changelist
        sync_result = call_command(["p4", "sync", f"@{p4_data['changelist']}"])
        print(f"{sync_result = }")

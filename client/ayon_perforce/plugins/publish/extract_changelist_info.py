from os import environ

import pyblish.api

from ayon_perforce.backend.rest_stub import PerforceRestStub

class ExtractChangelistInfo(pyblish.api.InstancePlugin):
    """Extract changelist info into deadline job."""

    order = pyblish.api.ValidatorOrder + 0.15    # -> should be extractor
    label = "Extract P4 Changelist info"
    hosts = ["unreal"]
    families = ["render.farm"]
    # depends_on: validate_workspace

    def process(self, instance):
        ctx = instance.context.data
        p4_data = ctx.get("perforce")
        cl_info = PerforceRestStub.get_last_change_list()
        p4_data["changelist"] = cl_info["change"]
        jobinfo = instance.data["deadline"].get("job_info")
        p4_webserver = environ.get("PERFORCE_WEBSERVER_URL")
        if not p4_webserver:
            raise RuntimeError("Perforce WebServer isn't running. Something's wrong.")

        jobinfo.EnvironmentKeyValue.update(
            {
                "AYON_P4_STREAM": p4_data["stream"],
                "AYON_P4_CHANGELIST": p4_data["changelist"],
                "AYON_UNREAL_VERSION": "5.4",   # todo: get from hostaddon
                "PERFORCE_WEBSERVER_URL": p4_webserver
            }
        )

        self.log.info(f"Changelist info: {cl_info}")

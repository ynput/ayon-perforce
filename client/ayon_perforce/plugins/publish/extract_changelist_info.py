import pyblish.api

from ayon_perforce.rest.perforce.rest_stub import PerforceRestStub

class ExtractChangelistInfo(pyblish.api.InstancePlugin):
    """Extract changelist info into deadline job."""

    order = pyblish.api.ValidatorOrder + 0.15    # -> should be extractor
    label = "Extract P4 Changelist info"
    hosts = ["unreal"]
    families = ["render.farm"]
    # depends_on: validate_workspace

    def process(self, instance):
        p4_data = instance.context.data.get("perforce")
        cl_info = PerforceRestStub.get_last_change_list()
        p4_data["changelist"] = cl_info["change"]
        jobinfo = instance.data["deadline"].get("job_info")
        jobinfo.EnvironmentKeyValue.update(
            {
                "AYON_P4_STREAM": p4_data["stream"],
                "AYON_P4_CHANGELIST": p4_data["changelist"],
                "AYON_UNREAL_VERSION": "5.4",   # todo: get from hostaddon
            }
        )
        instance.context.data["perforce"]["changelist"] = cl_info["change"]

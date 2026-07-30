from ayon_core.pipeline import publish

from client.ayon_perforce.api.commands import P4Commands

class ExtractChangeListInfo(publish.Extractor):
    """Extract changelist info into deadline job."""

    label = "Extract P4 Changelist info"
    hosts = ["unreal"]
    families = ["render.farm"]

    def process(self, instance):
        ctx = instance.context.data
        p4_data = ctx.get("perforce")
        cl_info = P4Commands.get_last_change_list()

        p4_data["changelist"] = cl_info["change"]
        jobinfo = instance.data["deadline"].get("job_info")

        jobinfo.EnvironmentKeyValue.update(
            {
                "AYON_P4_STREAM": p4_data["stream"],
                "AYON_P4_CHANGELIST": p4_data["changelist"],
                "AYON_UNREAL_VERSION": "5.7",   # todo: get from hostaddon
            }
        )

        self.log.info(f"Changelist info: {cl_info}")

"""Extract Perforce changelist info into the Deadline job."""
from __future__ import annotations

from os import environ
from typing import TYPE_CHECKING, ClassVar

from ayon_core.pipeline import publish
from ayon_perforce.backend.rest_stub import PerforceRestStub

if TYPE_CHECKING:
    import pyblish.api


class ExtractChangeListInfo(publish.Extractor):
    """Extract changelist info into deadline job."""

    label = "Extract P4 Changelist info"
    hosts: ClassVar[list[str]] = ["unreal"]
    families: ClassVar[list[str]] = ["render.farm"]

    def process(self, instance: pyblish.api.Instance) -> None:
        """Store the last changelist and P4 environment on the job info.

        Args:
            instance (pyblish.api.Instance): Render farm instance.

        Raises:
            RuntimeError: If the Perforce WebServer is not running.

        """
        ctx = instance.context.data
        p4_data = ctx.get("perforce")
        cl_info = PerforceRestStub.get_last_change_list()

        p4_data["changelist"] = cl_info["change"]
        jobinfo = instance.data["deadline"].get("job_info")
        p4_webserver = environ.get("PERFORCE_WEBSERVER_URL")
        if not p4_webserver:
            msg = "Perforce WebServer isn't running. Something's wrong."
            raise RuntimeError(msg)

        jobinfo.EnvironmentKeyValue.update(
            {
                "AYON_P4_STREAM": p4_data["stream"],
                "AYON_P4_CHANGELIST": p4_data["changelist"],
                # TODO(Tony Dorfmeister): get from hostaddon
                "AYON_UNREAL_VERSION": "5.7",
                "PERFORCE_WEBSERVER_URL": p4_webserver
            }
        )

        self.log.info("Changelist info: %s", cl_info)

"""Extract Changelist info from P4."""
from __future__ import annotations

import os
from typing import TYPE_CHECKING, ClassVar

from ayon_core.pipeline import publish
from ayon_perforce.api.commands import P4Commands

if TYPE_CHECKING:
    from logging import Logger

    import pyblish.api


class ExtractChangeListInfo(publish.Extractor):
    """Extract changelist info into deadline job."""

    label = "Extract P4 Changelist info"
    hosts: ClassVar[set[str]] = {"unreal"}
    families: ClassVar[set[str]] = {"render.farm"}
    log: Logger

    def process(self, instance: pyblish.api.Instance) -> None:
        """Process instance.

        Args:
            instance (pyblish.api.Instance): The instance to process.

        """
        ctx = instance.context.data
        p4_data = ctx.get("perforce")
        cl_info = P4Commands.get_last_change_list()

        p4_data["changelist"] = cl_info["change"]
        jobinfo = instance.data["deadline"].get("job_info")

        jobinfo.EnvironmentKeyValue.update(
            {
                "AYON_P4_STREAM": p4_data["stream"],
                "AYON_P4_CHANGELIST": p4_data["changelist"],
                # fallback to 5.7 to retain backwards compatibility with the
                # previous behavior of the plugin.
                "AYON_UNREAL_VERSION": ctx.get(
                    "unrealVersion", os.getenv("AYON_UNREAL_VERSION", "5.7"))
            }
        )

        self.log.info("Changelist info: %s", cl_info)

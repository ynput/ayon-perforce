"""AYON Integration addon for Perforce."""
from typing import Any

from ayon_server.addons import BaseServerAddon

from .settings import (
    DEFAULT_VALUES,
    PerforceSettings,
    convert_settings_overrides,
)


class PerforceAddon(BaseServerAddon):
    """AYON Integration addon for Perforce."""
    settings_model = PerforceSettings

    async def get_default_settings(self):
        """Get default settings.

        Returns:
            PerforceSettings: Default settings.

        """
        settings_model_cls = self.get_settings_model()
        return settings_model_cls(**DEFAULT_VALUES)

    async def convert_settings_overrides(
        self,
        source_version: str,
        overrides: dict[str, Any],
    ) -> dict[str, Any]:
        await convert_settings_overrides(source_version, overrides)
        return await super().convert_settings_overrides(
            source_version, overrides
        )

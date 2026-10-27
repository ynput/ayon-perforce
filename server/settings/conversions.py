"""Settings conversions from older addon versions."""
from typing import Any


def _convert_product_base_type(overrides: dict[str, Any]) -> None:
    profiles = (
        overrides
        .get("publish", {})
        .get("CollectPerforceControl", {})
        .get("profiles")
    )
    if not profiles:
        return

    for profile in profiles:
        if "product_base_types" not in profile and "product_types" in profile:
            profile["product_base_types"] = profile.pop("product_types")


async def convert_settings_overrides(  # noqa: RUF029 - awaited by addon
    source_version: str,  # noqa: ARG001 - kept for future conversions
    overrides: dict[str, Any],
) -> dict[str, Any]:
    """Convert settings overrides in place.

    Args:
        source_version (str): Addon version the overrides come from.
        overrides (dict[str, Any]): Settings overrides to convert.

    Returns:
        dict[str, Any]: Converted settings overrides.

    """
    _convert_product_base_type(overrides)
    return overrides

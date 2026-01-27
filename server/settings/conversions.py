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


async def convert_settings_overrides(
    source_version: str,
    overrides: dict[str, Any],
) -> dict[str, Any]:
    _convert_product_base_type(overrides)
    return overrides

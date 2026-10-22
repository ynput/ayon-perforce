"""Main settings."""
from ayon_server.settings import (
    BaseSettingsModel,
    task_types_enum,
)
from pydantic import Field


class CollectPerforceProfileModel(BaseSettingsModel):
    """Collect Perforce profile settings."""
    _layout = "expanded"
    host_names: list[str] = Field(
        default_factory=list,
        title="Host names",
    )
    product_base_types: list[str] = Field(
        default_factory=list,
        title="Product base types",
    )
    task_types: list[str] = Field(
        default_factory=list,
        title="Task types",
    )
    task_names: list[str] = Field(
        default_factory=list,
        title="Task names",
    )
    add_perforce_control: bool = Field(
        True,
        title="Commit representations to Perforce",
    )
    template_name: str = Field(
        "",
        title="Template name",
        description="Name from Anatomy to provide path and name of "
                    "committed file")


class CollectPerforceControlModel(BaseSettingsModel):
    """Collect Perforce Control settings."""
    _isGroup = True  # ruff: ignore[mixed-case-variable-in-class-scope]
    enabled: bool = False
    profiles: list[CollectPerforceProfileModel] = Field(
        default_factory=list,
        title="Profiles to trigger Perforce commit",
        description="Provide profile in which context representation should be"
         " tracked outside of AYON with Perforce commit"
    )


class PublishPluginsModel(BaseSettingsModel):
    """Publish plugins settings."""
    CollectPerforceControl: CollectPerforceControlModel = Field(
        default_factory=CollectPerforceControlModel,
        title="Collect Perforce Control",
        description=(
            "Configure which published products should be committed to P4. "
            "Keep disabled if published files should be versioned only in AYON"
        )
    )


class StreamProfile(BaseSettingsModel):
    """Stream profile settings."""
    template: str = Field(
        default="",
        title="Stream Name Template"
    )
    task_types: list[str] = Field(
        enum_resolver=task_types_enum,
        title="Task Type"
    )


class StreamModel(BaseSettingsModel):
    """Stream settings."""
    enabled: bool = Field(default=False)
    profiles: list[StreamProfile] = Field(
        default_factory=list,
        title="Stream Profiles"
    )


class WorkspaceModel(BaseSettingsModel):
    """Workspace settings."""

    depot: str = Field(
        "",
        title="Depot Name Template"
    )
    template: str = Field(
        "",
        title="Workspace Name Template"
    )


class PerforceSettings(BaseSettingsModel):
    """Version Control Project Settings."""

    enabled: bool = Field(default=False)
    host_name: str = Field(
        "perforce",
        title="Host name"
    )
    port: int = Field(
        1666,
        title="Port"
    )

    workspace: WorkspaceModel = Field(
        default_factory=WorkspaceModel,
        title="Workspace Settings"
    )
    stream: StreamModel = Field(
        default_factory=StreamModel,
        title="Stream Settings",
        description="WIP: enable to use streams in p4"
    )
    auto_submit_data_files: bool = Field(
        default=True,
        title="Auto-submit pipeline data files",
        description=(
            "Submit files with pipeline data written by host integrations "
            "(e.g. publish context data) in a dedicated changelist, so they "
            "are not reported as uncommitted changes on publish."
        ),
    )
    publish: PublishPluginsModel = Field(
        default_factory=PublishPluginsModel,
        title="Publish Plugins",
    )


DEFAULT_VALUES = {
  "port": 1666,
  "workspace": {
    "depot": "{project[code]}",
    "template": "{workstation[hostname]}_{workstation[system_name]}_{project[code]}"  # ruff: ignore[line-too-long]
  },
  "auto_submit_data_files": True,
  "stream": {
    "enabled": False,
    "profiles": []
  },
  "publish": {
    "CollectPerforceControl": {
      "enabled": False,
      "profiles": []
    }
  }
}

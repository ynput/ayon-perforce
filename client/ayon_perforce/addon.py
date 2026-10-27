"""Perforce Addon for AYON."""
from __future__ import annotations

import os
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ayon_core.addon import AYONAddon, IPluginPaths, ITrayService
from ayon_core.lib import StringTemplate, ayon_info, is_headless_mode_enabled
from ayon_core.pipeline.template_data import get_template_data_with_names
from ayon_core.settings import get_project_settings

from .lib import get_local_login
from .version import __version__

if TYPE_CHECKING:
    from collections.abc import Generator

PERFORCE_ADDON_DIR = os.path.dirname(os.path.abspath(__file__))


@dataclass
class ConnectionInfo:
    """Connection information for Perforce."""
    host: str
    port: int
    username: str
    password: str
    workspace_name: str


@dataclass
class LaunchData:
    """Current context data."""
    project_name: str
    folder_entity: dict[str, Any]
    task_entity: dict[str, Any]
    project_settings: dict[str, Any]


class PerforceAddon(AYONAddon, ITrayService, IPluginPaths):
    """AYON Integration addon for Perforce."""

    label = "Perforce Version Control"
    name = "perforce"
    version = __version__
    webserver = None
    login_tray = None

    # Public Methods:
    def initialize(self, settings: dict[str, Any]) -> None:
        """Initialize the addon."""
        self.settings: dict[str, Any] = settings.get(self.name)
        self.enabled: bool = self.settings and self.settings["enabled"]
        self.set_service_running_icon() if self.enabled else self.set_service_failed_icon()  # ruff: ignore[line-too-long]

    @staticmethod
    def get_connection_info(
        project_name: str,
        folder_entity: dict[str, Any],
        task_entity: dict[str, Any],
        project_settings: dict[str, Any] | None = None,
    ) -> ConnectionInfo:
        """Get connection information for Perforce.

        Args:
            project_name (str): Name of the project.
            folder_entity (dict[str, Any]): Folder entity.
            task_entity (dict[str, Any]): Task entity.
            project_settings (Optional[dict[str, Any]]): Project settings.

        Returns:
            ConnectionInfo: Connection information for Perforce.

        Raises:
            RuntimeError: If Perforce is disabled in server settings
        """
        if not project_settings:
            project_settings = get_project_settings(project_name)
        settings = project_settings["perforce"]
        if not settings["enabled"]:
            msg = "Perforce is disabled in server settings."
            raise RuntimeError(msg)

        tmpl_data = get_template_data_with_names(project_name)
        workstation_info = ayon_info.get_workstation_info()
        tmpl_data.update({
            "workstation": workstation_info,
            "workstation_info": workstation_info,
            "task": task_entity,
            "folder": folder_entity
        })
        ws_tmpl = StringTemplate(settings["workspace"]["template"])
        ws_name = ws_tmpl.format_strict(tmpl_data)
        username, password = get_local_login()

        return ConnectionInfo(
            host=settings["host_name"],
            port=settings["port"],
            username=username,
            password=password,
            workspace_name=ws_name
        )

    def get_server_url(
            self, project_settings: dict[str, Any] | None = None) -> str:
        """Get Perforce server URL.

        Returns:
            str: Perforce server URL.

        """
        if project_settings:
            self.settings = project_settings["perforce"]

        return f"{self.settings['host_name']}:{self.settings['port']}"

    @staticmethod
    def sync_to_version(
            conn_info: ConnectionInfo, change_id: int) -> None:
        """Sync to a specific version in Perforce.

        Args:
            conn_info (ConnectionInfo): Connection information for Perforce.
            change_id (int): Change ID to sync to.

        """
        from ayon_perforce.api.commands import P4Commands

        P4Commands.login(**asdict(conn_info))

        workspace_dir = P4Commands.get_workspace_dir(
            conn_info.workspace_name)
        P4Commands.sync_to_version(
            f"{workspace_dir}/...", change_id)

    @contextmanager
    def track_file_changes(
        self,
        project_name: str,
        filepaths: list[str],
        description: str,
    ) -> Generator[None, None, None]:
        """Submit files written in the block to Perforce.

        Public API for host integrations which store pipeline data
        (e.g. publish context data) in files inside the Perforce workspace.
        Files are opened in a dedicated changelist before the block and
        submitted after it, so they do not fail publish validation as
        uncommitted changes.

        Does nothing if Perforce or the auto-submit is disabled for
        the project, or if logging in to Perforce fails. Perforce errors
        are logged and never raised.

        Args:
            project_name (str): Name of the project.
            filepaths (list[str]): Local paths of files written in the block.
            description (str): Description of the submitted changelist.

        Yields:
            None

        """
        try:
            should_track = self._prepare_file_tracking(project_name)
        except Exception:
            self.log.warning(
                "Failed to prepare Perforce file tracking.", exc_info=True)
            should_track = False

        if not should_track:
            yield
            return

        from ayon_perforce.api.file_tracking import (
            track_file_changes,
        )

        with track_file_changes(filepaths, description):
            yield

    def _prepare_file_tracking(self, project_name: str) -> bool:
        """Check settings and ensure Perforce connection for file tracking.

        Returns:
            bool: Whether file changes should be tracked.

        """
        from ayon_core.pipeline.context_tools import (
            get_current_folder_entity,
            get_current_task_entity,
        )

        from ayon_perforce.api.commands import P4Commands

        project_settings = get_project_settings(project_name)
        if not is_perforce_enabled(project_settings):
            return False
        if not project_settings["perforce"].get("auto_submit_data_files"):
            return False

        if P4Commands._connection is not None:  # ruff: ignore[private-member-access]
            return True

        conn_info = self.get_connection_info(
            project_name,
            get_current_folder_entity(),
            get_current_task_entity(),
            project_settings,
        )
        if not all([
            conn_info.host,
            conn_info.port,
            conn_info.username,
            conn_info.workspace_name,
        ]):
            self.log.warning(
                "Perforce credentials are missing, "
                "skipping submit of data files.")
            return False
        P4Commands.login(**asdict(conn_info))
        return True

    def tray_init(self) -> None:
        """Called when the tray is initializing."""
        if is_headless_mode_enabled():
            return
        from .tray.login import PerforceLoginTray
        self.login_tray = PerforceLoginTray(self)

    def get_plugin_paths(self) -> dict[str, list[str]]:  # ruff: ignore[no-self-use]
        """Called to get the plugin paths.

        Note: this implements the abstract method from IPluginPaths.
            But it does not return any plugin paths.

        Returns:
            dict[str, list[str]]: Plugin paths.

        """
        return {}

    def tray_exit(self) -> None:
        """Called when the tray is exiting."""
        if self.enabled and \
                self.webserver and self.webserver.server_is_running:
            self.webserver.stop()

    def tray_start(self) -> None:
        """Called when the tray is starting."""

    def tray_menu(self, tray_menu: dict[str, Any]) -> None:
        """Add Perforce menu to the tray.

        Args:
            tray_menu (dict[str, Any]): Tray menu.

        """
        if self.enabled:
            self.login_tray.tray_menu(tray_menu)

    # PLR6301: This method is defined by the interface
    def get_create_plugin_paths(  # ruff: ignore[no-self-use]
            self, host_name: str) -> list[str]:
        """Get paths to create plugins.

        This adds host-specific paths based on runtime context.

        Args:
            host_name (str): Name of the host.

        Returns:
            list[str]: List of paths to create plugins.

        """
        if host_name != "unreal":
            return []
        return [f"{PERFORCE_ADDON_DIR}/plugins/create/unreal"]

    def get_publish_plugin_paths(  # ruff: ignore[no-self-use]
            self, host_name: str) -> list[str]:
        """Get paths to publish plugins.

        Args:
            host_name (str): Name of the host.

        Returns:
            list[str]: List of paths to publish plugins.

        """
        return [
            (Path(PERFORCE_ADDON_DIR) / "plugins" / "publish").as_posix()
        ]

    def get_launch_hook_paths(self, _app: str) -> str:  # ruff: ignore[no-self-use]
        """Implementation for applications launch hooks.

        Returns:
            str: full absolute path to directory with hooks for the module

        """
        return (Path(PERFORCE_ADDON_DIR) / "launch_hooks").as_posix()


def is_perforce_enabled(
        project_settings: dict[str, Any]) -> bool:
    """Check if Perforce is enabled for the project.

    Args:
        project_settings: Project settings.

    Returns:
        bool: True if Perforce is enabled, False otherwise.

    """
    return project_settings.get("perforce", {}).get("enabled", False)

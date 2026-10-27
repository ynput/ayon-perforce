"""REST API routes."""
from __future__ import annotations

from typing import TYPE_CHECKING

from ayon_core.lib import Logger

from ayon_perforce.backend import rest_routes

if TYPE_CHECKING:
    from aiohttp.web import UrlDispatcher


class PerforceModuleRestAPI:
    """REST API endpoint used for Perforce operations."""

    def __init__(self, server_manager: UrlDispatcher):
        """Initialize PerforceModuleRestAPI."""
        self._log = None
        self.server_manager = server_manager
        self.prefix = "/perforce"

    @property
    def log(self) -> Logger:
        """Get logger instance."""
        if self._log is None:
            self._log = Logger.get_logger(self.__class__.__name__)
        return self._log

    def register(self) -> None:
        """Register all REST API routes."""
        endpoints = (
            ("login", rest_routes.LoginEndpoint),
            ("is_in_any_workspace", rest_routes.IsPathInAnyWorkspace),
            ("add", rest_routes.AddEndpoint),
            ("sync_latest_version", rest_routes.SyncLatestEndpoint),
            ("sync_to_version", rest_routes.SyncVersionEndpoint),
            ("checkout", rest_routes.CheckoutEndpoint),
            ("is_checkouted", rest_routes.IsCheckoutedEndpoint),
            ("revert", rest_routes.Revert),
            ("get_changes", rest_routes.GetChanges),
            ("get_uncommitted_changes", rest_routes.GetUncommittedChanges),
            ("get_last_change_list", rest_routes.GetLastChangelist),
            ("submit_change_list", rest_routes.SubmitChangelist),
            ("submit_default_changelist",
             rest_routes.SubmitDefaultChangelist),
            ("exists_on_server", rest_routes.ExistsOnServer),
            ("get_stream", rest_routes.GetStreamEndpoint),
            ("get_workspace_dir", rest_routes.GetWorkspaceDirEndpoint),
        )
        for route, endpoint_cls in endpoints:
            endpoint = endpoint_cls()
            self.server_manager.add_route(
                "POST", f"{self.prefix}/{route}", endpoint.dispatch
            )

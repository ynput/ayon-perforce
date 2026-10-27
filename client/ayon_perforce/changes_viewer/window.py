"""Changes Viewer Window."""

from __future__ import annotations

import contextlib
import sys
from typing import TYPE_CHECKING

from ayon_core import style
from ayon_core.lib import Logger
from ayon_core.tools.utils.lib import qt_app_context
from qtpy import QtCore, QtWidgets

from .control import ChangesViewerController
from .widgets import ChangesDetailWidget

if TYPE_CHECKING:
    from ayon_perforce.addon import LaunchData


log = Logger.get_logger(__name__)
module = sys.modules[__name__]
module.window = None


class ChangesWindows(QtWidgets.QDialog):
    """Changes Viewer Window."""
    def __init__(
            self,
            controller: ChangesViewerController | None = None,
            parent: QtWidgets.QtWidget | None = None,
            launch_data: LaunchData | None = None):
        """Initialize ChangesWindows."""
        super().__init__(parent=parent)
        self.setWindowTitle("Changes Viewer")
        self.setObjectName("ChangesViewer")
        if not parent:
            self.setWindowFlags(
                self.windowFlags() | QtCore.Qt.WindowStaysOnTopHint
            )

        self.resize(780, 430)

        if controller is None:
            controller = ChangesViewerController(launch_data=launch_data)

        self._first_show = True

        details_widget = ChangesDetailWidget(controller, self)

        layout = QtWidgets.QHBoxLayout(self)
        layout.addWidget(details_widget, stretch=1)

        self._controller = controller
        self._details_widget = details_widget

    def showEvent(self, *args, **kwargs) -> None:  # ruff: ignore[invalid-function-name, missing-type-args, missing-type-kwargs]
        """Show event."""
        super().showEvent(*args, **kwargs)
        if self._first_show:
            self._first_show = False
            self.setStyleSheet(style.load_stylesheet())
            # exception must not escape into Qt, it would only be printed
            try:
                self._details_widget.reset()
            except Exception as err:
                log.exception("Failed to load Perforce changes")
                QtWidgets.QMessageBox.critical(
                    self,
                    "Perforce error",
                    f"Failed to load Perforce changes:\n{err}",
                )


def show(parent: QtWidgets.QtWidget | None = None) -> None:
    """Display Change Viewer GUI.

    Args:
        parent (QtWidgets.QtWidget, optional): When provided parent
            the interface to this QObject.

    """
    with contextlib.suppress(RuntimeError, AttributeError):
        module.window.close()
        del module.window
    with qt_app_context():
        window = ChangesWindows(parent)
        window.show()

        module.window = window

        # Pull window to the front.
        module.window.raise_()
        module.window.activateWindow()

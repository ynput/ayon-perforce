"""Validate if workspace_dir was collected and is valid."""
from __future__ import annotations

import os
from pathlib import Path
from typing import TYPE_CHECKING, Any, ClassVar

import pyblish.api
from ayon_core.pipeline import (
    PublishXmlValidationError,
)
from ayon_core.pipeline.publish import (
    PublishValidationError,
    ValidateContentsOrder,
)
from ayon_core.tools.utils import ErrorMessageBox
from ayon_perforce.api.commands import P4Commands
from qtpy import QtCore, QtWidgets

if TYPE_CHECKING:
    from ayon_perforce.api.models import UncommittedChange


class WorkspaceRepairAction(pyblish.api.Action):
    """Repair workspace_dir if it was not collected."""
    label = "Repair"
    on = "failed"
    icon = "wrench"

    def process(  # ruff: ignore[no-self-use]
            self, context: pyblish.api.Context,
            plugin: pyblish.api.Plugin) -> None:
        """Process the action.

        Args:
            context (pyblish.api.Context): The context of the publish.
            plugin (pyblish.api.Plugin): The plugin that failed.

        """
        plugin.repair(context)


class ValidateWorkspace(pyblish.api.ContextPlugin):
    """Validates if workspace_dir was collected and is valid.

    Used for committing to P4 directly from AYON.
    """
    order = ValidateContentsOrder  # this runs after the "normal" validators
    label = "Validate P4 workspace"
    targets: ClassVar[list[str]] = ["local"]
    hosts: ClassVar[list[str]] = ["unreal"]
    actions: ClassVar[list[type[pyblish.api.Action]]] = [WorkspaceRepairAction]

    def process(self, context: pyblish.api.Context) -> None:
        """Process the plugin.

        Args:
            context (pyblish.api.Context): The context of the publish.

        Raises:
            PublishValidationError: If workspace_dir is not collected.
            PublishXmlValidationError: If workspace_dir is not valid.


        """
        # TODO(antirotor): implement multiple roots
        p4_data = context.data.get("perforce")
        if not p4_data:
            msg = "No Perforce connection info found in context."
            raise PublishValidationError(msg)

        ws_name: str = p4_data.get("workspace_name")
        if not ws_name:
            msg = "No workspace name found in Perforce connection info."
            raise PublishValidationError(msg)

        # validate workspace_dir
        workspace_dir = P4Commands.get_workspace_dir(
            workspace_name=ws_name)
        if not workspace_dir or not os.path.exists(workspace_dir):
            project_name = context.data.get("projectName")
            msg = (
                "Please provide your local folder for workspace in "
                "`ayon+settings://perforce/local_setting/"
                f"workspace_dir?project={project_name}`")
            raise PublishXmlValidationError(self, msg)
        context.data["workspace_dir"] = workspace_dir

        # validate stream
        stream = P4Commands.get_stream(
            workspace_name=ws_name)
        if not stream:
            msg = (
                "Deadline implementation require depot with `streams`. "
                "Please let your Perforce admin set up your workspace with "
                "stream connected."
            )
            raise PublishValidationError(msg)
        p4_data["stream"] = stream

        # validate uncomitted changes
        uncommitted_changes = P4Commands.get_uncommitted_changes(ws_name)
        if uncommitted_changes:
            for change in uncommitted_changes:
                self.log.error("Uncommitted change: %s", change)
            p4_data["uncommitted_changes"] = uncommitted_changes
            msg = (
                "Workspace has uncommitted changes! Please commit or revert "
                "before publish."
            )

            raise PublishValidationError(msg)
        # TODO(tweak-wtf): check for stream updates

    _repair_dialog: ClassVar[UncommittedChangesRepairer | None] = None

    @classmethod
    def repair(cls, context: pyblish.api.Context) -> None:
        """Repair the workspace_dir if it was not collected.

        Args:
            context (pyblish.api.Context): The context of the publish.

        """
        import unreal

        p4_data = context.data["perforce"]
        dialog = UncommittedChangesRepairer(
            uncommitted_changes=p4_data["uncommitted_changes"],
            workspace_dir=context.data["workspace_dir"],
            workspace_name=p4_data["workspace_name"]
        )
        # Non-blocking show parented to Slate. A nested modal exec_()
        # blocks Unreal's game thread which pumps the Qt event loop.
        # Keep a reference so the dialog is not garbage collected.
        cls._repair_dialog = dialog
        dialog.show()
        unreal.parent_external_window_to_slate(dialog.winId())
        dialog.raise_()
        dialog.activateWindow()


class ChangesSelectionListModel(QtCore.QAbstractListModel):
    """A list model for displaying uncommitted changes in a QListView."""
    def __init__(self, data, parent=None):
        super().__init__(parent)
        self._data = data

    def rowCount(self, parent=QtCore.QModelIndex()) -> int:
        return len(self._data)

    def get_index(self, value):
        for idx, val in enumerate(self._data):
            if val == value:
                return idx
        return None

    def data(self, index, role) -> Any:
        if isinstance(index, QtCore.QModelIndex):
            index = index.row()
        if role == QtCore.Qt.DisplayRole:
            return str(self._data[index])
        if role == QtCore.Qt.UserRole:
            return self._data[index]
        return None

    def removeRows(self, row, count, parent=QtCore.QModelIndex()):
        self.beginRemoveRows(parent, row, row + count - 1)
        del self._data[row:row + count]
        self.endRemoveRows()
        return True


class UncommittedChangesRepairer(ErrorMessageBox):
    mb_submit_message: QtWidgets.QMessageBox = None
    lv_uncommitted_changes: QtWidgets.QListView = None

    def __init__(
            self,
            uncommitted_changes: list[UncommittedChange],
            workspace_dir: str,
            workspace_name: str,
            parent: QtWidgets.QWidget | None = None):
        # Set before super().__init__() because base __init__ calls
        # _create_content() which needs them.
        self.uncommitted_changes = uncommitted_changes
        self.workspace_dir = workspace_dir
        self.workspace_name = workspace_name
        if parent is None:
            parent = QtWidgets.QApplication.activeWindow()
        super().__init__("Pending Files in Changelist", parent)
        self.resize(800, 600)

    def _create_content(self, content_layout) -> None:
        label = QtWidgets.QLabel(
            "You have pending files in your changelist.\n"
            "Please revert, shelve or submit them before"
            "launching Unreal Engine again."
        )
        content_layout.addWidget(label)

        self.lv_uncommitted_changes = QtWidgets.QListView()
        self.lv_uncommitted_changes.setAlternatingRowColors(True)

        self.lv_uncommitted_changes.setModel(
            ChangesSelectionListModel(self.uncommitted_changes)
        )
        self.lv_uncommitted_changes.setSelectionMode(
            QtWidgets.QAbstractItemView.MultiSelection
        )
        content_layout.addWidget(self.lv_uncommitted_changes)

        self.mb_submit_message = QtWidgets.QPlainTextEdit()
        self.mb_submit_message.setPlaceholderText(
            "Enter a message for the submit")

        content_layout.addWidget(self.mb_submit_message)
        btn_revert_selected = QtWidgets.QPushButton("Revert Selected")
        btn_submit = QtWidgets.QPushButton("Submit")
        btn_revert_selected.clicked.connect(self.on_revert_selected)
        btn_submit.clicked.connect(self.on_submit)
        content_layout.addWidget(btn_revert_selected)
        content_layout.addWidget(btn_submit)

    def on_revert_selected(self) -> None:
        """Revert the selected files in the list view."""
        model = self.lv_uncommitted_changes.model()
        # Remove from the bottom so remaining rows keep valid positions.
        rows = sorted(
            {index.row()
             for index in self.lv_uncommitted_changes.selectedIndexes()},
            reverse=True,
        )
        for row in rows:
            index = model.index(row, 0)
            change: UncommittedChange = index.data(QtCore.Qt.UserRole)
            if not change.client_file:
                # pending changelist without opened files
                continue
            # we need to build an absolute local path
            # it seems p4 revert doesn't like depot or client syntax?!
            client_file = change.client_file.replace("//", "")
            client_file = client_file.replace(
                str(self.workspace_name), self.workspace_dir)
            client_file = Path(client_file)
            file_to_revert = self.workspace_dir / client_file

            P4Commands.revert(path=file_to_revert.as_posix())
            model.removeRow(row)

        if model.rowCount(QtCore.QModelIndex()) == 0:
            self.accept()

    def on_submit(self) -> None:
        """Submit the changelist with the provided message."""
        if not self.mb_submit_message.toPlainText().strip():
            msg_box = QtWidgets.QMessageBox()
            msg_box.setIcon(QtWidgets.QMessageBox.Critical)
            msg_box.setWindowTitle("No commit message found")
            msg_box.setText("Please enter a message for the submit")
            msg_box.setStandardButtons(QtWidgets.QMessageBox.Ok)
            msg_box.setWindowModality(QtCore.Qt.ApplicationModal)
            msg_box.setWindowFlags(
                msg_box.windowFlags() | QtCore.Qt.WindowStaysOnTopHint)
            msg_box.exec_()
            return

        changelist_message = self.mb_submit_message.toPlainText()
        changelist_message = f"[PRE PUBLISH] {changelist_message}"
        P4Commands.submit_default_changelist(changelist_message)
        self.accept()

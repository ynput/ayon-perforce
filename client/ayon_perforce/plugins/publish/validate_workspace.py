"""Validate if workspace_dir was collected and is valid."""
from __future__ import annotations

import os
from copy import deepcopy
from pathlib import Path
from typing import Any, ClassVar, Optional, Union

import pyblish.api
from ayon_core.pipeline import PublishXmlValidationError
from ayon_core.pipeline.publish import (
    PublishValidationError,
    ValidateContentsOrder,
)
from ayon_core.tools.utils import ErrorMessageBox
from ayon_perforce.backend.rest_stub import PerforceRestStub
from qtpy import QtCore, QtWidgets


class WorkspaceRepairAction(pyblish.api.Action):
    """Open dialog to revert or submit uncommitted changes."""

    label = "Repair"
    on = "failed"
    icon = "wrench"

    def process(  # noqa: PLR6301 - pyblish action interface
        self,
        context: pyblish.api.Context,
        plugin: pyblish.api.Plugin,
    ) -> None:
        """Run repair of the failed plugin.

        Args:
            context (pyblish.api.Context): Publish context.
            plugin (pyblish.api.Plugin): Plugin which failed.

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
    actions: ClassVar[list[type[pyblish.api.Action]]] = [
        WorkspaceRepairAction
    ]

    def process(self, context: pyblish.api.Context) -> None:
        """Process the plugin.

        Args:
            context (pyblish.api.Context): Publish context.

        Raises:
            PublishValidationError: If connection info, workspace name or
                stream are missing or there are uncommitted changes.
            PublishXmlValidationError: If workspace_dir is not collected.

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
        workspace_dir = PerforceRestStub.get_workspace_dir(
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
        stream = PerforceRestStub.get_stream(
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
        uncommitted_changes = PerforceRestStub.get_uncommitted_changes()
        if uncommitted_changes:
            for change in uncommitted_changes:
                self.log.error("Uncommitted change: %s", change)
            p4_data["uncommitted_changes"] = uncommitted_changes
            msg = (
                "Workspace has uncommitted changes! Please commit or revert "
                "before publish."
            )
            raise PublishValidationError(msg)
        # TODO(Tony Dorfmeister): check for stream updates

    @classmethod
    def repair(cls, context: pyblish.api.Context) -> None:
        """Show dialog to revert or submit uncommitted changes.

        Args:
            context (pyblish.api.Context): Publish context.

        """
        p4_data = context.data["perforce"]
        UncommittedChangesRepairer(
            uncommitted_changes=p4_data["uncommitted_changes"],
            workspace_dir=p4_data["workspace_dir"],
            workspace_name=p4_data["workspace_name"]
        ).exec_()


class ChangesSelectionListModel(QtCore.QAbstractListModel):
    """List model of uncommitted changes."""

    def __init__(
        self,
        data: list[dict],
        parent: Optional[QtCore.QObject] = None,
    ):
        """Initialize the model.

        Args:
            data (list[dict]): Uncommitted changes.
            parent (Optional[QtCore.QObject]): Parent object.

        """
        super().__init__(parent)
        self._data = data

    def rowCount(self, parent: QtCore.QModelIndex) -> int:  # noqa: N802 - Qt override
        """Return number of changes.

        Returns:
            int: Number of rows.

        """
        return len(self._data)

    def get_index(self, value: dict) -> Optional[int]:
        """Get row of the change.

        Args:
            value (dict): Change to look for.

        Returns:
            Optional[int]: Row of the change or None if not found.

        """
        for idx, val in enumerate(self._data):
            if val == value:
                return idx
        return None

    def data(
        self,
        index: Union[QtCore.QModelIndex, int],
        role: int,
    ) -> Any:  # noqa: ANN401 - value depends on Qt role
        """Get data for the index and role.

        Args:
            index (Union[QtCore.QModelIndex, int]): Model index or row.
            role (int): Qt item data role.

        Returns:
            Any: Display string, the change dict or None.

        """
        if isinstance(index, QtCore.QModelIndex):
            index = index.row()
        if role == QtCore.Qt.DisplayRole:
            change = self._data[index]
            return f"[{change['action']}]\t{change['clientFile']}"
        if role == QtCore.Qt.UserRole:
            return self._data[index]
        return None

    def removeRows(  # noqa: N802 - Qt override
        self,
        row: int,
        count: int,
        parent: Optional[QtCore.QModelIndex] = None,
    ) -> bool:
        """Remove changes from the model.

        Args:
            row (int): First row to remove.
            count (int): Number of rows to remove.
            parent (Optional[QtCore.QModelIndex]): Parent index.

        Returns:
            bool: Always True.

        """
        if parent is None:
            parent = QtCore.QModelIndex()
        self.beginRemoveRows(parent, row, row + count - 1)
        del self._data[row:row + count]
        self.endRemoveRows()
        return True


class UncommittedChangesRepairer(ErrorMessageBox):
    """Dialog to revert or submit uncommitted changes."""

    mb_submit_message: QtWidgets.QPlainTextEdit = None
    lv_uncommitted_changes: QtWidgets.QListView = None

    def __init__(
        self,
        uncommitted_changes: list,
        workspace_dir: str,
        workspace_name: str,
    ):
        """Initialize the dialog.

        Args:
            uncommitted_changes (list): Uncommitted changes.
            workspace_dir (str): Local root of the workspace.
            workspace_name (str): Name of the workspace.

        """
        self.title = "Pending Files in Changelist"
        self.parent = QtWidgets.QApplication.activeWindow()
        self.uncommitted_changes = uncommitted_changes
        self.workspace_dir = workspace_dir
        self.workspace_name = workspace_name
        super().__init__(self.title, self.parent)
        self.resize(800, 600)

    def _create_content(self, content_layout: QtWidgets.QVBoxLayout) -> None:
        label = QtWidgets.QLabel(
            "You have pending files in your changelist.\n"
            "Please revert, shelve or submit them before launching "
            "Unreal Engine again."
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
        """Revert selected files and close the dialog if none is left."""
        selection = self.lv_uncommitted_changes.selectedIndexes()
        for index in selection:
            # we need to buil;d an absolute local path
            # it seems p4 revert doesn't like depot or client syntax?!
            client_file = deepcopy(
                index.data(QtCore.Qt.UserRole)["clientFile"])
            client_file = client_file.replace("//", "")
            client_file = client_file.replace(
                str(self.workspace_name), self.workspace_dir)
            client_file = Path(client_file)
            file_to_revert = self.workspace_dir / client_file

            PerforceRestStub.revert(path=file_to_revert.as_posix())
            self.lv_uncommitted_changes.model().removeRow(index.row())

        model = self.lv_uncommitted_changes.model()
        if model.rowCount(QtCore.QModelIndex()) == 0:
            self.accept()

    def on_submit(self) -> None:
        """Submit the default changelist with the entered message."""
        if not self.mb_submit_message.toPlainText():
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
        PerforceRestStub.submit_default_changelist(changelist_message)
        self.accept()

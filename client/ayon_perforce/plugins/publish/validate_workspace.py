"""Validate if workspace_dir was collected and is valid."""
from __future__ import annotations

import os
from copy import deepcopy
from typing import ClassVar

import pyblish.api
from qtpy import QtCore, QtWidgets

from ayon_core.pipeline.publish import (
    ValidateContentsOrder,
    RepairAction,
    PublishValidationError,
)
from ayon_core.tools.utils import ErrorMessageBox
from ayon_core.pipeline import PublishXmlValidationError
from ayon_core.pipeline.publish import ValidateContentsOrder
from ayon_core.pipeline import PublishValidationError, PublishXmlValidationError


from ayon_perforce.rest.perforce.rest_stub import PerforceRestStub


class ValidateWorkspace(pyblish.api.ContextPlugin):
    """Validates if workspace_dir was collected and is valid.

    Used for committing to P4 directly from AYON.
    """
    order = ValidateContentsOrder # this runs after the "normal" validators
    label = "Validate P4 workspace"
    targets: ClassVar[list[str]] = ["local"]
    hosts = ["unreal"]
    actions = [RepairAction]  #todo: how to get context in repar action if its not an instnace plugin

    def process(self, context):
        """Process the plugin.

        Raises:
            PublishXmlValidationError: If workspace_dir is not collected.

        """
        # TODO(antirotor): implement multiple roots
        p4_data = context.data.get("perforce")
        if not p4_data:
            raise PublishValidationError("No Perforce data found in context.")

        # validate workspace_dir
        workspace_dir = p4_data["workspace_dir"]
        if not workspace_dir or not os.path.exists(workspace_dir):
            project_name = instance.context.data.get("projectName")
            msg = (
                "Please provide your local folder for workspace in "
                "`ayon+settings://perforce/local_setting/"
                f"workspace_dir?project={project_name}`")
            raise PublishXmlValidationError(self, msg)
        
        # validate stream
        stream = p4_data["stream"]
        if not stream:
            msg = (
                "Deadline implementation require depot with `streams`. "
                "Please let your Perforce admin set up your workspace with "
                "stream connected."
            )
            raise PublishValidationError(self, msg)
        
        # validate uncomitted changes
        uncommitted_changes = PerforceRestStub.get_uncommitted_changes()
        if uncommitted_changes:
            for change in uncommitted_changes:
                self.log.error(f"Uncommitted change: {change}")
            context.data["perforce"]["uncommitted_changes"] = uncommitted_changes
            raise PublishValidationError(
                "Workspace has uncommitted changes! Please commit or revert before publish."
            )
        # TODO: check for stream updates

    @classmethod
    def repair(cls, context):
        p4_data = context.data["perforce"]
        UncommittedChangesRepairer(
            uncommitted_changes=p4_data["uncommitted_changes"],
            workspace_dir=p4_data["workspace_dir"],
            workspace_name=p4_data["workspace_name"]
        ).exec_()


class ChangesSelectionListModel(QtCore.QAbstractListModel):
    def __init__(self, data, parent=None):
        super().__init__(parent)
        self._data = data

    def rowCount(self, parent):
        return len(self._data)

    def get_index(self, value):
        for idx, val in enumerate(self._data):
            if val == value:
                return idx
        return None

    def data(self, index, role):
        if isinstance(index, QtCore.QModelIndex):
            index = index.row()
        if role == QtCore.Qt.DisplayRole:
            return f"[{self._data[index]['action']}]\t{self._data[index]['clientFile']}"
        if role == QtCore.Qt.UserRole:
            return self._data[index]

    def removeRows(self, row, count, parent=QtCore.QModelIndex()):
        self.beginRemoveRows(parent, row, row + count - 1)
        del self._data[row:row + count]
        self.endRemoveRows()
        return True

class UncommittedChangesRepairer(ErrorMessageBox):
    mb_submit_message: QtWidgets.QMessageBox = None
    lv_uncommitted_changes: QtWidgets.QListView = None

    def __init__(self, uncommitted_changes: list, workspace_dir: str, workspace_name: str):
        self.title = "Pending Files in Changelist"
        self.parent = QtWidgets.QApplication.activeWindow()
        self.uncommitted_changes = uncommitted_changes
        self.workspace_dir = workspace_dir
        self.workspace_name = workspace_name
        super().__init__(self.title, self.parent)
        self.resize(800, 600)

    def _create_content(self, content_layout) -> None:
        label = QtWidgets.QLabel(
            "You have pending files in your changelist.\nPlease revert, shelve or submit them before launching Unreal Engine again."
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
        self.mb_submit_message.setPlaceholderText("Enter a message for the submit")

        content_layout.addWidget(self.mb_submit_message)
        btn_revert_selected = QtWidgets.QPushButton("Revert Selected")
        btn_submit = QtWidgets.QPushButton("Submit")
        btn_revert_selected.clicked.connect(self.on_revert_selected)
        btn_submit.clicked.connect(self.on_submit)
        content_layout.addWidget(btn_revert_selected)
        content_layout.addWidget(btn_submit)

    def on_revert_selected(self):
        selection = self.lv_uncommitted_changes.selectedIndexes()
        for index in selection:
            # we need to buil;d an absolute local path
            # it seems p4 revert doesn't like depot or client syntax?!
            client_file = deepcopy(index.data(QtCore.Qt.UserRole)["clientFile"])
            client_file = client_file.replace("//", "")
            client_file = client_file.replace(str(self.workspace_name), self.workspace_dir)
            client_file = Path(client_file)
            file_to_revert = self.workspace_dir / client_file

            PerforceRestStub.revert(path=file_to_revert.as_posix())
            self.lv_uncommitted_changes.model().removeRow(index.row())

        if self.lv_uncommitted_changes.model().rowCount(QtCore.QModelIndex()) == 0:
            self.accept()


    def on_submit(self):
        if self.mb_submit_message.toPlainText() == "":
            msg_box = QtWidgets.QMessageBox()
            msg_box.setIcon(QtWidgets.QMessageBox.Critical)
            msg_box.setWindowTitle("No commit message found")
            msg_box.setText("Please enter a message for the submit")
            msg_box.setStandardButtons(QtWidgets.QMessageBox.Ok)
            msg_box.setWindowModality(QtCore.Qt.ApplicationModal)
            msg_box.setWindowFlags(msg_box.windowFlags() | QtCore.Qt.WindowStaysOnTopHint)
            msg_box.exec_()
            return

        changelist_message = self.mb_submit_message.toPlainText()
        changelist_message = f"[PRE PUBLISH] {changelist_message}"
        PerforceRestStub.submit_default_changelist(changelist_message)
        self.accept()

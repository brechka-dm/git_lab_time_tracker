"""Kanban column list widget with drag-and-drop."""

from __future__ import annotations

from PySide6.QtCore import Qt, QUrl, Signal
from PySide6.QtGui import QAction, QDesktopServices
from PySide6.QtWidgets import QListWidget, QListWidgetItem


class BoardListWidget(QListWidget):
    """List that accepts dropped issue cards from other columns."""

    issue_moved = Signal(dict, str)
    start_work_requested = Signal(dict)
    stop_work_requested = Signal(dict)
    show_total_time_requested = Signal(dict)
    show_work_sessions_requested = Signal(dict)
    toggle_highlight_requested = Signal(dict)
    issue_order_changed = Signal(str, list)
    add_local_task_requested = Signal(str)

    def __init__(self, column_label: str) -> None:
        super().__init__()
        self._column_label = column_label
        self.setSelectionMode(QListWidget.SelectionMode.SingleSelection)
        self.setDragEnabled(True)
        self.setAcceptDrops(True)
        self.setDropIndicatorShown(True)
        self.setDragDropMode(QListWidget.DragDropMode.DragDrop)
        self.setDefaultDropAction(Qt.DropAction.MoveAction)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.ActionsContextMenu)
        open_action = QAction("Open in GitLab", self)
        open_action.triggered.connect(self._open_selected_issue_link)
        self.addAction(open_action)

        start_action = QAction("Start work", self)
        start_action.triggered.connect(self._start_selected_issue_work)
        self.addAction(start_action)

        stop_action = QAction("Stop work", self)
        stop_action.triggered.connect(self._stop_selected_issue_work)
        self.addAction(stop_action)

        total_time_action = QAction("Show total time", self)
        total_time_action.triggered.connect(self._show_selected_issue_total_time)
        self.addAction(total_time_action)

        sessions_action = QAction("Show work sessions", self)
        sessions_action.triggered.connect(self._show_selected_issue_sessions)
        self.addAction(sessions_action)

        highlight_action = QAction("Toggle highlight", self)
        highlight_action.triggered.connect(self._toggle_selected_issue_highlight)
        self.addAction(highlight_action)

        add_local_action = QAction("Add local task here", self)
        add_local_action.triggered.connect(self._request_add_local_task)
        self.addAction(add_local_action)
        self.itemDoubleClicked.connect(self._open_issue_link)

    def dropEvent(self, event) -> None:  # type: ignore[override]
        source = event.source()
        dragged_payload = None
        if isinstance(source, QListWidget):
            dragged_item = source.currentItem()
            if dragged_item is not None:
                payload = dragged_item.data(Qt.ItemDataRole.UserRole)
                if isinstance(payload, dict):
                    dragged_payload = payload

        event.setDropAction(Qt.DropAction.MoveAction)
        super().dropEvent(event)
        self._emit_order_changed()
        if source is self:
            return

        if isinstance(dragged_payload, dict):
            self.issue_moved.emit(dragged_payload, self._column_label)

    def _emit_order_changed(self) -> None:
        order: list[str] = []
        for idx in range(self.count()):
            item = self.item(idx)
            payload = item.data(Qt.ItemDataRole.UserRole)
            if not isinstance(payload, dict):
                continue
            if bool(payload.get("is_local", False)):
                task_id = int(payload.get("task_id", 0))
                if task_id > 0:
                    order.append(f"local:{task_id}")
                continue
            project_id = int(payload.get("project_id", 0))
            iid = int(payload.get("iid", 0))
            if project_id > 0 and iid > 0:
                order.append(f"{project_id}:{iid}")
        self.issue_order_changed.emit(self._column_label, order)

    def _open_issue_link(self, item: QListWidgetItem) -> None:
        payload = item.data(Qt.ItemDataRole.UserRole)
        if isinstance(payload, dict):
            if bool(payload.get("is_local", False)):
                return
            web_url = payload.get("web_url", "")
            if web_url:
                QDesktopServices.openUrl(QUrl(str(web_url)))

    def _open_selected_issue_link(self) -> None:
        selected = self.currentItem()
        if selected is None:
            return
        self._open_issue_link(selected)

    def _start_selected_issue_work(self) -> None:
        selected = self.currentItem()
        if selected is None:
            return
        payload = selected.data(Qt.ItemDataRole.UserRole)
        if isinstance(payload, dict):
            self.start_work_requested.emit(payload)

    def _stop_selected_issue_work(self) -> None:
        selected = self.currentItem()
        if selected is None:
            return
        payload = selected.data(Qt.ItemDataRole.UserRole)
        if isinstance(payload, dict):
            self.stop_work_requested.emit(payload)

    def _show_selected_issue_total_time(self) -> None:
        selected = self.currentItem()
        if selected is None:
            return
        payload = selected.data(Qt.ItemDataRole.UserRole)
        if isinstance(payload, dict):
            self.show_total_time_requested.emit(payload)

    def _show_selected_issue_sessions(self) -> None:
        selected = self.currentItem()
        if selected is None:
            return
        payload = selected.data(Qt.ItemDataRole.UserRole)
        if isinstance(payload, dict):
            self.show_work_sessions_requested.emit(payload)

    def _request_add_local_task(self) -> None:
        self.add_local_task_requested.emit(self._column_label)

    def _toggle_selected_issue_highlight(self) -> None:
        selected = self.currentItem()
        if selected is None:
            return
        payload = selected.data(Qt.ItemDataRole.UserRole)
        if isinstance(payload, dict):
            self.toggle_highlight_requested.emit(payload)

    @property
    def column_label(self) -> str:
        return self._column_label

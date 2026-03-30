"""Kanban board UI."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
import time

import requests
from PySide6.QtCore import Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QAction, QDesktopServices
from PySide6.QtWidgets import (
    QApplication,
    QCalendarWidget,
    QComboBox,
    QDateEdit,
    QDialog,
    QDialogButtonBox,
    QFrame,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QProgressDialog,
    QScrollArea,
    QSizePolicy,
    QStatusBar,
    QTableWidget,
    QTableWidgetItem,
    QTimeEdit,
    QVBoxLayout,
    QWidget,
)

from gitlab_client import GitLabClient
from models import GitLabIssue
from storage import AppStorage


class BoardListWidget(QListWidget):
    """List that accepts dropped issue cards from other columns."""

    issue_moved = Signal(dict, str)
    start_work_requested = Signal(dict)
    stop_work_requested = Signal(dict)
    show_total_time_requested = Signal(dict)
    show_work_sessions_requested = Signal(dict)
    issue_order_changed = Signal(str, list)

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
            project_id = int(payload.get("project_id", 0))
            iid = int(payload.get("iid", 0))
            if project_id > 0 and iid > 0:
                order.append(f"{project_id}:{iid}")
        self.issue_order_changed.emit(self._column_label, order)

    def _open_issue_link(self, item: QListWidgetItem) -> None:
        payload = item.data(Qt.ItemDataRole.UserRole)
        if isinstance(payload, dict):
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


class BoardColumn(QFrame):
    """Single kanban column with title and task list."""

    issue_moved = Signal(dict, str)
    move_left_requested = Signal(str)
    move_right_requested = Signal(str)
    remove_requested = Signal(str)
    issue_order_changed = Signal(str, list)

    def __init__(self, label_id: str, title: str, tasks_count: int) -> None:
        super().__init__()
        self._label = label_id
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.setMinimumWidth(260)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Expanding)

        layout = QVBoxLayout(self)
        header = QLabel(f"{title} ({tasks_count})")
        header.setAlignment(Qt.AlignmentFlag.AlignCenter)
        header.setStyleSheet("font-weight: bold;")
        layout.addWidget(header)

        controls = QHBoxLayout()
        move_left = QPushButton("←")
        move_left.setFixedWidth(28)
        move_left.clicked.connect(lambda: self.move_left_requested.emit(self._label))
        controls.addWidget(move_left)

        move_right = QPushButton("→")
        move_right.setFixedWidth(28)
        move_right.clicked.connect(lambda: self.move_right_requested.emit(self._label))
        controls.addWidget(move_right)

        remove = QPushButton("×")
        remove.setFixedWidth(28)
        remove.setEnabled(label_id != "open")
        remove.clicked.connect(lambda: self.remove_requested.emit(self._label))
        controls.addWidget(remove)
        controls.addStretch()
        layout.addLayout(controls)

        self.list_widget = BoardListWidget(label_id)
        self.list_widget.issue_moved.connect(self.issue_moved.emit)
        self.list_widget.issue_order_changed.connect(self.issue_order_changed.emit)
        layout.addWidget(self.list_widget)


class WorkSessionsDialog(QDialog):
    """Dialog for manual work session correction."""

    def __init__(self, issue_title: str, sessions: list[dict[str, float]], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Work Sessions")
        self.resize(760, 420)
        self._original: list[tuple[int, int]] = [
            (int(item["started_at"]), int(item["finished_at"])) for item in sessions
        ]
        self._editors: list[tuple[QTimeEdit, QTimeEdit]] = []
        self._dates: list[datetime.date] = []

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(issue_title))

        table = QTableWidget(self)
        table.setColumnCount(3)
        table.setHorizontalHeaderLabels(["Date", "Start", "End"])
        table.setRowCount(len(sessions))
        layout.addWidget(table)
        self._table = table

        for row, item in enumerate(sessions):
            started = datetime.fromtimestamp(float(item["started_at"]))
            finished = datetime.fromtimestamp(float(item["finished_at"]))
            session_date = started.date()
            self._dates.append(session_date)
            table.setItem(row, 0, QTableWidgetItem(started.strftime("%Y-%m-%d")))

            start_editor = QTimeEdit(self)
            start_editor.setDisplayFormat("HH:mm:ss")
            start_editor.setTime(started.time())
            table.setCellWidget(row, 1, start_editor)

            end_editor = QTimeEdit(self)
            end_editor.setDisplayFormat("HH:mm:ss")
            end_editor.setTime(finished.time())
            table.setCellWidget(row, 2, end_editor)

            start_editor.timeChanged.connect(self._on_changed)
            end_editor.timeChanged.connect(self._on_changed)
            self._editors.append((start_editor, end_editor))

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        self._save_button = buttons.button(QDialogButtonBox.StandardButton.Save)
        self._save_button.setEnabled(False)
        layout.addWidget(buttons)
        self._on_changed()

    def _on_changed(self) -> None:
        self._save_button.setEnabled(self.has_changes())

    def has_changes(self) -> bool:
        return self.get_values() != self._original

    def get_values(self) -> list[tuple[int, int]]:
        values: list[tuple[int, int]] = []
        for idx, (start_editor, end_editor) in enumerate(self._editors):
            session_date = self._dates[idx]
            start_time = start_editor.time()
            end_time = end_editor.time()
            started_dt = datetime(
                session_date.year,
                session_date.month,
                session_date.day,
                start_time.hour(),
                start_time.minute(),
                start_time.second(),
            )
            finished_dt = datetime(
                session_date.year,
                session_date.month,
                session_date.day,
                end_time.hour(),
                end_time.minute(),
                end_time.second(),
            )
            started = int(started_dt.timestamp())
            finished = int(finished_dt.timestamp())
            values.append((started, finished))
        return values


class TrackerWindow(QMainWindow):
    """Main window with board and sync actions."""
    _PROJECT_TAGS = {
        1: "RRC",
        2: "TS",
        14: "GTE",
        49: "Metro",
        54: "Noesis",
    }
    _REVIEW_BOARD_NAME = "Review"

    def __init__(self, client: GitLabClient, storage: AppStorage) -> None:
        super().__init__()
        self._client = client
        self._storage = storage
        self._issues_by_iid: dict[tuple[int, int], GitLabIssue] = {}
        self._columns: dict[str, BoardColumn] = {}
        self._current_board = self._REVIEW_BOARD_NAME
        self._column_orders_by_board = self._load_board_state()
        self._last_issues: list[GitLabIssue] = []
        self._spent_events_cache: dict[tuple[int, int], list[dict]] = {}
        self._issue_orders: dict[str, list[str]] = self._storage.load_issue_orders()
        self._today_total_seconds = 0
        self._active_issue_iid: int | None = None
        self._active_issue_title = ""
        self._active_started_at: float | None = None
        self._tracking_timer = QTimer(self)
        self._tracking_timer.setInterval(1000)
        self._tracking_timer.timeout.connect(self._refresh_tracking_info)
        self._sync_timer = QTimer(self)
        self._sync_timer.setInterval(15000)
        self._sync_timer.timeout.connect(self._sync_pending_events)

        self.setWindowTitle("GitLab Time Tracker")
        self.resize(1300, 700)
        self.setStatusBar(QStatusBar())

        root = QWidget()
        root_layout = QVBoxLayout(root)

        tracking_row = QHBoxLayout()
        tracking_row.addWidget(QLabel("Current work:"))
        self._active_issue_label = QLabel("None")
        self._active_issue_label.setStyleSheet("font-weight: bold;")
        tracking_row.addWidget(self._active_issue_label, stretch=1)
        tracking_row.addWidget(QLabel("Time:"))
        self._active_time_label = QLabel("00:00:00")
        self._active_time_label.setStyleSheet("font-family: Consolas, monospace;")
        tracking_row.addWidget(self._active_time_label)
        tracking_row.addWidget(QLabel("Today total:"))
        self._today_total_label = QLabel("00:00:00")
        self._today_total_label.setStyleSheet("font-family: Consolas, monospace;")
        tracking_row.addWidget(self._today_total_label)
        stop_active_button = QPushButton("Stop current")
        stop_active_button.clicked.connect(self._stop_active_work)
        tracking_row.addWidget(stop_active_button)
        root_layout.addLayout(tracking_row)

        refresh_row = QHBoxLayout()
        actions_row = QHBoxLayout()
        refresh_button = QPushButton("Refresh")
        refresh_button.clicked.connect(self.load_issues)
        refresh_row.addWidget(refresh_button)
        refresh_row.addStretch()
        root_layout.addLayout(refresh_row)

        actions_row.addWidget(QLabel("Board:"))
        self._board_selector = QComboBox()
        self._board_selector.currentTextChanged.connect(self._on_board_changed)
        actions_row.addWidget(self._board_selector)

        add_column_button = QPushButton("Add Column")
        add_column_button.clicked.connect(self._on_add_column)
        actions_row.addWidget(add_column_button)
        actions_row.addStretch()
        root_layout.addLayout(actions_row)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        board_container = QWidget()
        self._board_layout = QHBoxLayout(board_container)
        self._board_layout.setAlignment(Qt.AlignmentFlag.AlignLeft)
        scroll.setWidget(board_container)

        root_layout.addWidget(scroll)
        self.setCentralWidget(root)
        self._init_menus()
        self._load_tracking_state()
        self._refresh_tracking_info()
        self._tracking_timer.start()
        self._sync_timer.start()

    def _init_menus(self) -> None:
        reports_menu = self.menuBar().addMenu("Reports")
        daily_action = QAction("Daily Report", self)
        daily_action.triggered.connect(self._show_daily_report)
        reports_menu.addAction(daily_action)

        period_action = QAction("Period Report", self)
        period_action.triggered.connect(self._show_period_report)
        reports_menu.addAction(period_action)

    def load_issues(self) -> None:
        """Load board data from GitLab and redraw columns."""
        self._sync_pending_events()
        try:
            issues = self._client.fetch_open_issues()
        except Exception as error:  # noqa: BLE001
            QMessageBox.critical(self, "GitLab error", f"Failed to load issues:\n{error}")
            return

        self._last_issues = issues
        self._issues_by_iid = {(issue.project_id, issue.iid): issue for issue in issues}
        self._spent_events_cache.clear()
        self._sync_boards_from_issues(issues)
        grouped = self._group_issues(issues)
        self._rebuild_columns(grouped)
        self._update_today_total()
        self.statusBar().showMessage(f"Loaded {len(issues)} issues", 5000)

    def _group_issues(self, issues: list[GitLabIssue]) -> dict[str, list[GitLabIssue]]:
        grouped: dict[str, list[GitLabIssue]] = defaultdict(list)
        if self._current_board == self._REVIEW_BOARD_NAME:
            grouped["open"] = [issue for issue in issues if self._is_review_issue(issue)]
            return grouped

        self._ensure_column_exists(self._current_board, "open")
        for issue in issues:
            column_label = self._resolve_column(issue)
            grouped[column_label].append(issue)

        # Keep empty columns visible as long as label exists on board.
        for label in self._column_order():
            grouped.setdefault(label, [])
        return grouped

    def _rebuild_columns(self, grouped: dict[str, list[GitLabIssue]]) -> None:
        while self._board_layout.count():
            item = self._board_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

        self._columns.clear()
        for label in self._column_order():
            if label not in grouped:
                continue
            column = BoardColumn(label, self._display_label(label), len(grouped[label]))
            column.issue_moved.connect(self._on_issue_moved)
            column.move_left_requested.connect(self._move_column_left)
            column.move_right_requested.connect(self._move_column_right)
            column.remove_requested.connect(self._remove_column)
            column.issue_order_changed.connect(self._on_issue_order_changed)
            column.list_widget.start_work_requested.connect(self._start_work_from_payload)
            column.list_widget.stop_work_requested.connect(self._stop_work_from_payload)
            column.list_widget.show_total_time_requested.connect(self._show_issue_total_time)
            column.list_widget.show_work_sessions_requested.connect(self._show_issue_work_sessions)
            self._columns[label] = column
            self._board_layout.addWidget(column)

            for issue in self._ordered_issues_for_column(label, grouped[label]):
                card = QListWidgetItem(f"#{issue.iid} {issue.title}")
                card.setData(Qt.ItemDataRole.UserRole, asdict(issue))
                column.list_widget.addItem(card)

    def _on_issue_moved(self, payload: dict, target_label: str) -> None:
        if self._current_board == self._REVIEW_BOARD_NAME:
            return

        issue = GitLabIssue(
            project_id=int(payload["project_id"]),
            iid=int(payload["iid"]),
            title=str(payload["title"]),
            web_url=str(payload["web_url"]),
            labels=[str(label) for label in payload.get("labels", [])],
            assignee_ids=[int(value) for value in payload.get("assignee_ids", [])],
            reviewer_ids=[int(value) for value in payload.get("reviewer_ids", [])],
        )

        current = self._issues_by_iid.get((issue.project_id, issue.iid), issue)
        current_column = self._resolve_column(current)
        if current_column == target_label:
            return

        try:
            updated = self._client.move_issue_to_label(
                issue=current,
                target_label=target_label,
                current_column=current_column,
            )
        except Exception as error:  # noqa: BLE001
            if self._is_retryable_error(error):
                self._storage.enqueue_event(
                    "move_issue",
                    {
                        "project_id": current.project_id,
                        "iid": current.iid,
                        "title": current.title,
                        "web_url": current.web_url,
                        "labels": current.labels,
                        "assignee_ids": current.assignee_ids,
                        "reviewer_ids": current.reviewer_ids,
                        "target_label": target_label,
                        "current_column": current_column,
                    },
                )
                self._issues_by_iid[(current.project_id, current.iid)] = self._apply_local_move(
                    current,
                    target_label,
                    current_column,
                )
                self.statusBar().showMessage(
                    f"Connection lost. Move queued for issue #{current.iid}.",
                    7000,
                )
                self._rebuild_columns(self._group_issues(list(self._issues_by_iid.values())))
                return
            QMessageBox.warning(self, "Move failed", f"Failed to update issue labels:\n{error}")
            return

        self._issues_by_iid[(updated.project_id, updated.iid)] = updated
        self.statusBar().showMessage(f"Issue #{updated.iid} moved to '{target_label}'", 5000)
        self.load_issues()

    def _resolve_column(self, issue: GitLabIssue) -> str:
        if self._current_board == self._REVIEW_BOARD_NAME:
            return "open"
        if not issue.labels:
            return "open"
        board_order = self._column_order()
        for label in issue.labels:
            category, _column_name = self._parse_label(label)
            if category != self._current_board:
                continue
            self._ensure_column_exists(self._current_board, label)
            if label in board_order:
                return label
        return "open"

    def _load_board_state(self) -> dict[str, list[str]]:
        default_state = {self._REVIEW_BOARD_NAME: ["open"], "TT": ["open"]}
        state, selected = self._storage.load_boards()
        state.setdefault(self._REVIEW_BOARD_NAME, ["open"])
        if selected and selected in state:
            self._current_board = selected
        return state or default_state

    def _save_board_state(self) -> None:
        self._storage.save_boards(self._column_orders_by_board, self._current_board)
        self._storage.save_tracking(self._active_issue_iid, self._active_issue_title, self._active_started_at)

    def _column_order(self) -> list[str]:
        if self._current_board == self._REVIEW_BOARD_NAME:
            self._column_orders_by_board[self._REVIEW_BOARD_NAME] = ["open"]
            return ["open"]
        if self._current_board not in self._column_orders_by_board:
            self._column_orders_by_board[self._current_board] = ["open"]
        return self._column_orders_by_board[self._current_board]

    def _ensure_column_exists(self, board_name: str, label: str) -> None:
        if board_name not in self._column_orders_by_board:
            self._column_orders_by_board[board_name] = ["open"]
        board_order = self._column_orders_by_board[board_name]
        if label not in board_order:
            board_order.append(label)
            self._save_board_state()

    def _sync_boards_from_issues(self, issues: list[GitLabIssue]) -> None:
        discovered_categories: set[str] = set()
        for issue in issues:
            for label in issue.labels:
                category, _column_name = self._parse_label(label)
                discovered_categories.add(category)
                self._ensure_column_exists(category, label)
        self._ensure_column_exists(self._REVIEW_BOARD_NAME, "open")
        if discovered_categories and self._current_board not in discovered_categories:
            if self._current_board != self._REVIEW_BOARD_NAME:
                self._current_board = sorted(discovered_categories)[0]
        self._refresh_board_selector()
        self._save_board_state()

    def _refresh_board_selector(self) -> None:
        boards = sorted(self._column_orders_by_board.keys())
        self._board_selector.blockSignals(True)
        self._board_selector.clear()
        self._board_selector.addItems(boards)
        current_index = self._board_selector.findText(self._current_board)
        if current_index >= 0:
            self._board_selector.setCurrentIndex(current_index)
        self._board_selector.blockSignals(False)

    def _on_add_column(self) -> None:
        if self._current_board == self._REVIEW_BOARD_NAME:
            QMessageBox.information(self, "Review Board", "Columns are not editable in Review board.")
            return
        label, accepted = QInputDialog.getText(
            self,
            "Add column",
            f"Column label for board '{self._current_board}':",
        )
        if not accepted:
            return
        column_name = label.strip()
        if not column_name:
            QMessageBox.information(self, "Empty Value", "Column label cannot be empty.")
            return
        if column_name == "open":
            QMessageBox.information(self, "Reserved Label", "'open' already exists.")
            return
        new_label = f"{self._current_board}:{column_name}"

        if new_label in self._column_order():
            QMessageBox.information(self, "Duplicate", "Column already exists.")
            return

        self._column_order().append(new_label)
        self._save_board_state()
        self.load_issues()

    def _remove_column(self, label: str) -> None:
        if self._current_board == self._REVIEW_BOARD_NAME:
            QMessageBox.information(self, "Review Board", "Columns are not editable in Review board.")
            return
        if label == "open":
            QMessageBox.information(self, "Blocked", "Column 'open' cannot be removed.")
            return
        confirmation = QMessageBox.question(
            self,
            "Delete Column",
            f"Delete '{label}' column and move its tasks to 'open'?",
        )
        if confirmation != QMessageBox.StandardButton.Yes:
            return

        issues_to_move = [
            issue
            for issue in self._issues_by_iid.values()
            if self._resolve_column(issue) == label
        ]
        for issue in issues_to_move:
            try:
                self._client.move_issue_to_label(
                    issue=issue,
                    target_label="open",
                    current_column=label,
                )
            except Exception as error:  # noqa: BLE001
                if self._is_retryable_error(error):
                    self._storage.enqueue_event(
                        "move_issue",
                        {
                            "project_id": issue.project_id,
                            "iid": issue.iid,
                            "title": issue.title,
                            "web_url": issue.web_url,
                            "labels": issue.labels,
                            "assignee_ids": issue.assignee_ids,
                            "reviewer_ids": issue.reviewer_ids,
                            "target_label": "open",
                            "current_column": label,
                        },
                    )
                    continue
                QMessageBox.warning(self, "Move Failed", f"Failed to move issue #{issue.iid}:\n{error}")
                self.load_issues()
                return

        self._column_orders_by_board[self._current_board] = [
            item for item in self._column_order() if item != label
        ]
        self._save_board_state()
        self.load_issues()

    def _move_column_left(self, label: str) -> None:
        board_order = self._column_order()
        index = board_order.index(label)
        if index <= 0:
            return
        board_order[index - 1], board_order[index] = (
            board_order[index],
            board_order[index - 1],
        )
        self._save_board_state()
        self.load_issues()

    def _move_column_right(self, label: str) -> None:
        board_order = self._column_order()
        index = board_order.index(label)
        if index >= len(board_order) - 1:
            return
        board_order[index + 1], board_order[index] = (
            board_order[index],
            board_order[index + 1],
        )
        self._save_board_state()
        self.load_issues()

    def _on_board_changed(self, board_name: str) -> None:
        selected = board_name.strip()
        if not selected:
            return
        if selected == self._current_board:
            return
        self._current_board = selected
        self._save_board_state()
        self._rebuild_columns(self._group_issues(self._last_issues))

    def _on_issue_order_changed(self, column_label: str, order: list[str]) -> None:
        key = f"{self._current_board}|{column_label}"
        self._issue_orders[key] = list(order)
        self._storage.save_issue_orders(self._issue_orders)

    def _ordered_issues_for_column(self, column_label: str, issues: list[GitLabIssue]) -> list[GitLabIssue]:
        key = f"{self._current_board}|{column_label}"
        preferred_order = self._issue_orders.get(key, [])
        if not preferred_order:
            return issues
        by_key = {self._issue_key(issue): issue for issue in issues}
        ordered: list[GitLabIssue] = []
        seen: set[str] = set()
        for issue_key in preferred_order:
            issue = by_key.get(issue_key)
            if issue is not None:
                ordered.append(issue)
                seen.add(issue_key)
        for issue in issues:
            issue_key = self._issue_key(issue)
            if issue_key not in seen:
                ordered.append(issue)
        return ordered

    @staticmethod
    def _parse_label(label: str) -> tuple[str, str]:
        text = label.strip()
        if ":" in text:
            category, column_name = text.split(":", 1)
            category = category.strip() or "Other"
            return category, (column_name.strip() or text)
        return "Other", text

    def _display_label(self, raw_label: str) -> str:
        if raw_label == "open":
            return "open"
        category, column_name = self._parse_label(raw_label)
        if category == self._current_board and column_name:
            return column_name
        return raw_label

    def _start_work_from_payload(self, payload: dict) -> None:
        issue_iid = int(payload.get("iid", 0))
        title = str(payload.get("title", "")).strip()
        if issue_iid <= 0 or not title:
            return

        if self._active_issue_iid is not None and self._active_issue_iid != issue_iid:
            self._stop_active_work()

        self._active_issue_iid = issue_iid
        self._active_issue_title = title
        self._active_started_at = time.time()
        self._save_board_state()
        self._refresh_tracking_info()

    def _stop_work_from_payload(self, payload: dict) -> None:
        issue_iid = int(payload.get("iid", 0))
        if self._active_issue_iid is None:
            return
        if issue_iid != self._active_issue_iid:
            return
        self._stop_active_work()

    def _stop_active_work(self) -> None:
        if self._active_issue_iid is not None and self._active_started_at is not None:
            finished_at = time.time()
            elapsed_seconds = max(1, int(finished_at - self._active_started_at))
            try:
                active_issue = self._find_issue_by_iid(self._active_issue_iid)
                if active_issue is None:
                    raise ValueError(f"Issue #{self._active_issue_iid} not found in loaded list")
                self._client.add_spent_time(active_issue.project_id, self._active_issue_iid, elapsed_seconds)
                self.statusBar().showMessage(
                    f"Spent time added to issue #{self._active_issue_iid}: {self._format_duration(elapsed_seconds)}",
                    6000,
                )
            except Exception as error:  # noqa: BLE001
                if self._is_retryable_error(error):
                    self._storage.enqueue_event(
                        "add_spent_time",
                        {
                            "project_id": active_issue.project_id,
                            "iid": self._active_issue_iid,
                            "spent_seconds": elapsed_seconds,
                        },
                    )
                    self.statusBar().showMessage(
                        f"Connection lost. Time event queued for issue #{self._active_issue_iid}.",
                        7000,
                    )
                else:
                    QMessageBox.warning(
                        self,
                        "GitLab time tracking",
                        f"Failed to add spent time for issue #{self._active_issue_iid}:\n{error}",
                    )
        self._active_issue_iid = None
        self._active_issue_title = ""
        self._active_started_at = None
        self._save_board_state()
        self._refresh_tracking_info()

    def _refresh_tracking_info(self) -> None:
        active_elapsed = 0
        if self._active_issue_iid is None or self._active_started_at is None:
            self._active_issue_label.setText("None")
            self._active_time_label.setText("00:00:00")
        else:
            current_issue = self._find_issue_by_iid(self._active_issue_iid)
            if current_issue is not None:
                self._active_issue_title = current_issue.title

            self._active_issue_label.setText(f"#{self._active_issue_iid} {self._active_issue_title}")
            active_elapsed = max(0, int(time.time() - self._active_started_at))
            self._active_time_label.setText(self._format_duration(active_elapsed))

        self._today_total_label.setText(self._format_duration(self._today_total_seconds + active_elapsed))

    def _load_tracking_state(self) -> None:
        issue_iid, title, started_at = self._storage.load_tracking()
        self._active_issue_iid = issue_iid
        self._active_issue_title = title
        self._active_started_at = started_at

    def _show_issue_total_time(self, payload: dict) -> None:
        issue_iid = int(payload.get("iid", 0))
        title = str(payload.get("title", "")).strip()
        project_id = int(payload.get("project_id", 0))
        if issue_iid <= 0:
            return
        try:
            stats = self._client.get_issue_time_summary(project_id, issue_iid)
        except Exception as error:  # noqa: BLE001
            QMessageBox.warning(self, "GitLab time tracking", f"Failed to load time summary:\n{error}")
            return

        total_seconds = int(stats.get("total_time_spent", 0) or 0)
        estimate_seconds = int(stats.get("time_estimate", 0) or 0)
        QMessageBox.information(
            self,
            f"Time summary #{issue_iid}",
            (
                f"{title}\n\n"
                f"Total spent: {self._format_duration(total_seconds)}\n"
                f"Estimate: {self._format_duration(estimate_seconds)}"
            ),
        )

    def _show_issue_work_sessions(self, payload: dict) -> None:
        issue_iid = int(payload.get("iid", 0))
        project_id = int(payload.get("project_id", 0))
        issue_title = str(payload.get("title", "")).strip()
        if issue_iid <= 0:
            return
        try:
            spent_events = self._load_spent_events_from_gitlab(project_id, issue_iid)
        except Exception as error:  # noqa: BLE001
            QMessageBox.warning(self, "GitLab Time Tracking", f"Failed to load work sessions:\n{error}")
            return
        if not spent_events:
            QMessageBox.information(self, f"Work Sessions #{issue_iid}", "No sessions found.")
            return

        sessions: list[dict[str, float]] = []
        for event in spent_events:
            finished_at = self._parse_iso_datetime(event.get("finished_at"))
            duration = int(event.get("duration_seconds", 0) or 0)
            if finished_at is None or duration <= 0:
                continue
            started_at = finished_at - duration
            sessions.append(
                {
                    "started_at": float(started_at),
                    "finished_at": float(finished_at),
                    "duration_seconds": float(duration),
                }
            )
        sessions.sort(key=lambda item: item["started_at"])

        dialog = WorkSessionsDialog(f"#{issue_iid} {issue_title}", sessions, self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        if not dialog.has_changes():
            return

        updated_ranges = dialog.get_values()
        for started, finished in updated_ranges:
            if finished <= started:
                QMessageBox.warning(self, "Work Sessions", "End time must be greater than start time.")
                return

        try:
            self._client.reset_spent_time(project_id, issue_iid)
            for started, finished in sorted(updated_ranges):
                self._client.add_spent_time(project_id, issue_iid, finished - started)
            cache_key = (project_id, issue_iid)
            if cache_key in self._spent_events_cache:
                del self._spent_events_cache[cache_key]
            self._update_today_total()
            self.statusBar().showMessage(f"Work sessions saved for issue #{issue_iid}", 5000)
        except Exception as error:  # noqa: BLE001
            QMessageBox.warning(self, "Work Sessions", f"Failed to save sessions:\n{error}")

    def _show_daily_report(self) -> None:
        dialog = QDialog(self)
        dialog.setWindowTitle("Daily Report Date")
        dialog_layout = QVBoxLayout(dialog)
        calendar = QCalendarWidget(dialog)
        calendar.setGridVisible(True)
        dialog_layout.addWidget(calendar)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        dialog_layout.addWidget(buttons)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        target_qdate = calendar.selectedDate()
        target_date = datetime(
            target_qdate.year(),
            target_qdate.month(),
            target_qdate.day(),
        ).date()

        issue_totals: dict[tuple[int, int], int] = {}
        issue_titles: dict[tuple[int, int], str] = {}
        issues = list(self._issues_by_iid.values())
        progress = QProgressDialog("Building daily report...", "Cancel", 0, len(issues), self)
        progress.setWindowTitle("Daily Report")
        progress.setWindowModality(Qt.WindowModality.WindowModal)
        progress.setMinimumDuration(0)

        for index, issue in enumerate(issues, start=1):
            progress.setLabelText(f"Loading events for #{issue.iid} ({index}/{len(issues)})...")
            progress.setValue(index - 1)
            QApplication.processEvents()
            if progress.wasCanceled():
                return

            try:
                cache_key = (issue.project_id, issue.iid)
                if cache_key not in self._spent_events_cache:
                    self._spent_events_cache[cache_key] = self._load_spent_events_from_gitlab(issue.project_id, issue.iid)
                events = self._spent_events_cache[cache_key]
            except Exception as error:  # noqa: BLE001
                progress.close()
                QMessageBox.warning(
                    self,
                    "Daily Report",
                    f"Failed to load spent events for issue #{issue.iid}:\n{error}",
                )
                return

            key = (issue.project_id, issue.iid)
            issue_titles[key] = issue.title
            issue_totals.setdefault(key, 0)
            for event in events:
                finished_at = self._parse_iso_datetime(event.get("finished_at"))
                duration = int(event.get("duration_seconds", 0) or 0)
                if finished_at is None or duration <= 0:
                    continue
                event_date = datetime.fromtimestamp(finished_at).date()
                if event_date == target_date:
                    issue_totals[key] += duration
            progress.setValue(index)
            QApplication.processEvents()
        progress.close()

        report_items = [(key, seconds) for key, seconds in issue_totals.items() if seconds > 0]
        report_items.sort(key=lambda item: item[1], reverse=True)
        total_seconds = sum(seconds for _, seconds in report_items)

        lines = [f"{target_date.isoformat()} | Total: {self._format_duration(total_seconds)}", ""]
        if not report_items:
            lines.append("No tracked time for this date.")
        else:
            for (project_id, issue_iid), seconds in report_items:
                tag = self._PROJECT_TAGS.get(project_id, f"P{project_id}")
                lines.append(f"{tag} | #{issue_iid} | {self._format_duration(seconds)}")

        report_text = "\n".join(lines)
        reports_dir = Path(__file__).resolve().parent / "reports"
        reports_dir.mkdir(exist_ok=True)
        report_path = reports_dir / f"daily_report_{target_date.isoformat()}.txt"
        report_path.write_text(report_text, encoding="utf-8")

        QMessageBox.information(self, "Daily Report", f"Report saved to:\n{report_path}")

    def _show_period_report(self) -> None:
        dialog = QDialog(self)
        dialog.setWindowTitle("Period Report")
        layout = QVBoxLayout(dialog)

        from_row = QHBoxLayout()
        from_row.addWidget(QLabel("From:"))
        from_date = QDateEdit(dialog)
        from_date.setCalendarPopup(True)
        from_date.setDate(datetime.now().date())
        from_row.addWidget(from_date)
        layout.addLayout(from_row)

        to_row = QHBoxLayout()
        to_row.addWidget(QLabel("To:"))
        to_date = QDateEdit(dialog)
        to_date.setCalendarPopup(True)
        to_date.setDate(datetime.now().date())
        to_row.addWidget(to_date)
        layout.addLayout(to_row)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return

        start_qdate = from_date.date()
        end_qdate = to_date.date()
        start_date = datetime(start_qdate.year(), start_qdate.month(), start_qdate.day()).date()
        end_date = datetime(end_qdate.year(), end_qdate.month(), end_qdate.day()).date()
        if start_date > end_date:
            QMessageBox.warning(self, "Period Report", "'From' date must be before or equal to 'To' date.")
            return

        issues = list(self._issues_by_iid.values())
        progress = QProgressDialog("Building period report...", "Cancel", 0, len(issues), self)
        progress.setWindowTitle("Period Report")
        progress.setWindowModality(Qt.WindowModality.WindowModal)
        progress.setMinimumDuration(0)

        grouped: dict[str, dict[str, int]] = {}
        for index, issue in enumerate(issues, start=1):
            progress.setLabelText(f"Loading events for #{issue.iid} ({index}/{len(issues)})...")
            progress.setValue(index - 1)
            QApplication.processEvents()
            if progress.wasCanceled():
                return

            try:
                cache_key = (issue.project_id, issue.iid)
                if cache_key not in self._spent_events_cache:
                    self._spent_events_cache[cache_key] = self._load_spent_events_from_gitlab(issue.project_id, issue.iid)
                events = self._spent_events_cache[cache_key]
            except Exception as error:  # noqa: BLE001
                progress.close()
                QMessageBox.warning(
                    self,
                    "Period Report",
                    f"Failed to load spent events for issue #{issue.iid}:\n{error}",
                )
                return

            project_tag = self._PROJECT_TAGS.get(issue.project_id, f"P{issue.project_id}")
            per_project = grouped.setdefault(project_tag, {})
            per_project.setdefault(issue.title, 0)
            for event in events:
                finished_at = self._parse_iso_datetime(event.get("finished_at"))
                duration = int(event.get("duration_seconds", 0) or 0)
                if finished_at is None or duration <= 0:
                    continue
                event_date = datetime.fromtimestamp(finished_at).date()
                if start_date <= event_date <= end_date:
                    per_project[issue.title] += duration
            progress.setValue(index)
            QApplication.processEvents()
        progress.close()

        total_seconds = 0
        lines: list[str] = [f"{start_date.isoformat()} .. {end_date.isoformat()}", ""]
        for project_tag in sorted(grouped.keys()):
            tasks = grouped[project_tag]
            task_items = [(title, seconds) for title, seconds in tasks.items() if seconds > 0]
            if not task_items:
                continue
            lines.append(project_tag)
            task_items.sort(key=lambda item: item[1], reverse=True)
            for title, seconds in task_items:
                total_seconds += seconds
                lines.append(f"{title} | {self._format_duration(seconds)}")
            lines.append("")

        if total_seconds == 0:
            lines.append("No tracked time for selected period.")
        else:
            lines.insert(1, f"Total: {self._format_duration(total_seconds)}")
            lines.insert(2, "")

        report_text = "\n".join(lines)
        reports_dir = Path(__file__).resolve().parent / "reports"
        reports_dir.mkdir(exist_ok=True)
        report_path = reports_dir / f"period_report_{start_date.isoformat()}_{end_date.isoformat()}.txt"
        report_path.write_text(report_text, encoding="utf-8")
        QMessageBox.information(self, "Period Report", f"Report saved to:\n{report_path}")

    def _load_timelogs_from_gitlab(self, project_id: int, issue_iid: int) -> list[dict]:
        return self._client.get_issue_time_logs(project_id, issue_iid)

    def _load_spent_events_from_gitlab(self, project_id: int, issue_iid: int) -> list[dict]:
        return self._client.get_issue_spent_time_events(project_id, issue_iid)

    def _update_today_total(self) -> None:
        today = datetime.now().date()
        total = 0
        for issue in self._issues_by_iid.values():
            cache_key = (issue.project_id, issue.iid)
            if cache_key not in self._spent_events_cache:
                continue
            for event in self._spent_events_cache[cache_key]:
                finished_at = self._parse_iso_datetime(event.get("finished_at"))
                duration = int(event.get("duration_seconds", 0) or 0)
                if finished_at is None or duration <= 0:
                    continue
                if datetime.fromtimestamp(finished_at).date() == today:
                    total += duration
        self._today_total_seconds = total

    def _sync_pending_events(self) -> None:
        queued = self._storage.load_events(limit=100)
        if not queued:
            return
        for event in queued:
            event_id = int(event["id"])
            event_type = str(event["event_type"])
            payload = event["payload"]
            try:
                if event_type == "add_spent_time":
                    self._client.add_spent_time(
                        int(payload["project_id"]),
                        int(payload["iid"]),
                        int(payload["spent_seconds"]),
                    )
                elif event_type == "move_issue":
                    issue = GitLabIssue(
                        project_id=int(payload["project_id"]),
                        iid=int(payload["iid"]),
                        title=str(payload["title"]),
                        web_url=str(payload["web_url"]),
                        labels=[str(item) for item in payload.get("labels", [])],
                        assignee_ids=[int(value) for value in payload.get("assignee_ids", [])],
                        reviewer_ids=[int(value) for value in payload.get("reviewer_ids", [])],
                    )
                    self._client.move_issue_to_label(
                        issue=issue,
                        target_label=str(payload["target_label"]),
                        current_column=str(payload["current_column"]),
                    )
                self._storage.delete_event(event_id)
            except Exception as error:  # noqa: BLE001
                if self._is_retryable_error(error):
                    break
                self._storage.delete_event(event_id)
        self._update_today_total()

    @staticmethod
    def _is_retryable_error(error: Exception) -> bool:
        if isinstance(error, requests.RequestException):
            if error.response is None:
                return True
            status = error.response.status_code
            return status >= 500
        return False

    @staticmethod
    def _apply_local_move(issue: GitLabIssue, target_label: str, current_column: str) -> GitLabIssue:
        labels = list(issue.labels)
        if current_column != "open":
            labels = [label for label in labels if label != current_column]
        if target_label != "open":
            labels.append(target_label)
        labels = list(dict.fromkeys(labels))
        return GitLabIssue(
            project_id=issue.project_id,
            iid=issue.iid,
            title=issue.title,
            web_url=issue.web_url,
            labels=labels,
            assignee_ids=issue.assignee_ids,
            reviewer_ids=issue.reviewer_ids,
        )

    def _find_issue_by_iid(self, iid: int) -> GitLabIssue | None:
        for issue in self._issues_by_iid.values():
            if issue.iid == iid:
                return issue
        return None

    def _is_review_issue(self, issue: GitLabIssue) -> bool:
        current_user_id = self._client.user_id
        if current_user_id is None:
            return False
        return (current_user_id in issue.reviewer_ids) and (current_user_id not in issue.assignee_ids)

    @staticmethod
    def _parse_iso_datetime(value: object) -> float | None:
        if not isinstance(value, str):
            return None
        text = value.strip()
        if not text:
            return None
        normalized = text.replace("Z", "+00:00")
        try:
            return datetime.fromisoformat(normalized).timestamp()
        except ValueError:
            return None

    @staticmethod
    def _format_duration(seconds: int) -> str:
        value = max(0, int(seconds))
        hours = value // 3600
        minutes = (value % 3600) // 60
        secs = value % 60
        return f"{hours:02d}:{minutes:02d}:{secs:02d}"

    @staticmethod
    def _issue_key(issue: GitLabIssue) -> str:
        return f"{issue.project_id}:{issue.iid}"

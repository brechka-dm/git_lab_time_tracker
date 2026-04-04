"""Main tracker window and board orchestration."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import asdict
from datetime import date, datetime
import time

from PySide6.QtCore import Qt, QThread, QTimer, Signal
from PySide6.QtGui import QAction
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QStatusBar,
    QVBoxLayout,
    QWidget,
)

from gitlab_client import GitLabClient
from models import GitLabIssue, LocalTask, local_task_from_payload, local_task_to_payload
from storage import AppStorage

from .board_column import BoardColumn
from .board_labels import display_label, parse_label
from .board_list_widget import BoardListWidget
from .issue_board_ops import apply_local_move, issue_key
from .issue_time_dialogs import show_issue_total_time, show_issue_work_sessions
from .issue_payload import issue_from_dict, issue_to_dict
from .local_time_totals import local_totals_for_day, local_totals_for_period
from .network import is_retryable_network_error
from .refresh_thread import RefreshThread
from .report_dialogs import run_daily_report, run_period_report
from .report_source import merge_report_item_lists, report_source_items
from .sync_thread import SyncThread
from .time_formatting import format_duration
from .today_scan_thread import TodayScanThread


class TrackerWindow(QMainWindow):
    """Main window with board and sync actions."""
    _REVIEW_BOARD_NAME = "Review"

    def __init__(self, client: GitLabClient, storage: AppStorage) -> None:
        super().__init__()
        self._client = client
        self._storage = storage
        self._issues_by_iid: dict[tuple[int, int], GitLabIssue] = {}
        self._local_tasks_by_id: dict[int, LocalTask] = {}
        self._columns: dict[str, BoardColumn] = {}
        self._current_board = self._REVIEW_BOARD_NAME
        self._column_orders_by_board = self._load_board_state()
        self._last_issues: list[GitLabIssue] = []
        self._review_items: list[GitLabIssue] = []
        self._spent_events_cache: dict[tuple[str, int, int], list[dict]] = {}
        self._today_scan_total_seconds = 0
        self._refresh_thread: QThread | None = None
        self._refresh_in_progress = False
        self._sync_worker_thread: SyncThread | None = None
        self._scan_thread: QThread | None = None
        self._current_scan_worker: TodayScanThread | None = None
        self._scan_restart_requested = False
        self._today_scan_cache: dict[str, dict[str, float]] = {}
        self._today_scan_day = ""
        self._issue_orders: dict[str, list[str]] = self._storage.load_issue_orders()
        self._today_total_seconds = 0
        self._active_issue_iid: int | None = None
        self._active_is_local = False
        self._active_local_task_id: int | None = None
        self._active_issue_project_id: int | None = None
        self._active_item_type = "issue"
        self._active_target_project_id: int | None = None
        self._active_target_iid: int | None = None
        self._active_target_type = "issue"
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
        self._refresh_btn = QPushButton("Refresh")
        self._refresh_btn.clicked.connect(self.load_issues)
        refresh_row.addWidget(self._refresh_btn)
        refresh_row.addStretch()
        root_layout.addLayout(refresh_row)

        actions_row.addWidget(QLabel("Board:"))
        self._board_selector = QComboBox()
        self._board_selector.currentTextChanged.connect(self._on_board_changed)
        actions_row.addWidget(self._board_selector)

        add_column_button = QPushButton("Add Column")
        add_column_button.clicked.connect(self._on_add_column)
        actions_row.addWidget(add_column_button)
        add_local_task_button = QPushButton("Add Local Task")
        add_local_task_button.clicked.connect(self._on_add_local_task_button)
        actions_row.addWidget(add_local_task_button)
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
        self._load_local_tasks_state()
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
        if self._refresh_in_progress:
            return

        cached = self._storage.load_issues_cache()
        if cached is not None:
            cached_issues = [i for d in cached[0] if (i := issue_from_dict(d)) is not None]
            cached_mrs = [i for d in cached[1] if (i := issue_from_dict(d)) is not None]
            self._apply_cached(cached_issues, cached_mrs)

        self._refresh_in_progress = True
        self._refresh_btn.setEnabled(False)
        self.statusBar().showMessage("Refreshing from GitLab…")

        thread = RefreshThread(self._client, self)
        thread.result.connect(self._apply_refresh)
        thread.error.connect(self._on_refresh_error)
        thread.finished.connect(self._on_refresh_finished)
        thread.finished.connect(thread.deleteLater)
        self._refresh_thread = thread
        thread.start()

    def _apply_cached(self, issues: list[GitLabIssue], review_mrs: list[GitLabIssue]) -> None:
        """Show cached issues immediately on startup before the network refresh completes."""
        self._last_issues = issues
        self._review_items = review_mrs
        self._issues_by_iid = {(issue.project_id, issue.iid): issue for issue in issues}
        self._sync_boards_from_issues(issues)
        grouped = self._group_issues(issues)
        self._rebuild_columns(grouped)
        self._start_today_total_scan()
        self.statusBar().showMessage(f"Showing {len(issues)} cached issues — refreshing…")

    def _apply_refresh(
        self,
        issues: list[GitLabIssue],
        review_mrs: list[GitLabIssue],
    ) -> None:
        """Apply freshly loaded issues to the UI (called in the UI thread via queued connection)."""
        self._refresh_in_progress = False
        self._refresh_btn.setEnabled(True)
        self.statusBar().clearMessage()
        self._storage.save_issues_cache(
            [issue_to_dict(i) for i in issues],
            [issue_to_dict(i) for i in review_mrs],
        )
        self._last_issues = issues
        self._review_items = review_mrs
        self._issues_by_iid = {(issue.project_id, issue.iid): issue for issue in issues}
        self._spent_events_cache.clear()
        self._sync_boards_from_issues(issues)
        grouped = self._group_issues(issues)
        self._rebuild_columns(grouped)
        # Avoid zeroing today total before scan: if the cached-data scan is still
        # running, _start_today_total_scan() returns early and _on_scan_item needs
        # a correct baseline (it does old_total -> new_total deltas).
        self._start_today_total_scan()
        self.statusBar().showMessage(f"Loaded {len(issues)} issues", 5000)

    def _on_refresh_error(self, error: str) -> None:
        """Handle a refresh failure (called in the UI thread via queued connection)."""
        self._refresh_in_progress = False
        self._refresh_btn.setEnabled(True)
        self.statusBar().clearMessage()
        QMessageBox.critical(self, "GitLab error", f"Failed to load issues:\n{error}")

    def _on_refresh_finished(self) -> None:
        """Drop thread reference after refresh completion."""
        self._refresh_thread = None
        self._refresh_in_progress = False
        self._refresh_btn.setEnabled(True)

    def _group_issues(self, issues: list[GitLabIssue]) -> dict[str, list[GitLabIssue]]:
        grouped: dict[str, list[GitLabIssue]] = defaultdict(list)
        if self._current_board == self._REVIEW_BOARD_NAME:
            grouped["open"] = [issue for issue in self._review_items if self._is_review_issue(issue)]
            return grouped

        self._ensure_column_exists(self._current_board, "open")
        for issue in issues:
            if not self._is_assignee_issue(issue):
                continue
            column_label = self._resolve_column(issue)
            grouped[column_label].append(issue)

        # Keep empty columns visible as long as label exists on board.
        for label in self._column_order():
            grouped.setdefault(label, [])
        for task in self._local_tasks_by_id.values():
            if task.board_name != self._current_board:
                continue
            self._ensure_column_exists(task.board_name, task.column_label)
            grouped.setdefault(task.column_label, [])
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
            local_count = sum(
                1
                for t in self._local_tasks_by_id.values()
                if t.board_name == self._current_board and t.column_label == label
            )
            column = BoardColumn(label, display_label(label, self._current_board), len(grouped[label]) + local_count)
            column.issue_moved.connect(self._on_issue_moved)
            column.move_left_requested.connect(self._move_column_left)
            column.move_right_requested.connect(self._move_column_right)
            column.remove_requested.connect(self._remove_column)
            column.issue_order_changed.connect(self._on_issue_order_changed)
            column.list_widget.start_work_requested.connect(self._start_work_from_payload)
            column.list_widget.stop_work_requested.connect(self._stop_work_from_payload)
            column.list_widget.show_total_time_requested.connect(self._show_issue_total_time)
            column.list_widget.show_work_sessions_requested.connect(self._show_issue_work_sessions)
            column.list_widget.add_local_task_requested.connect(self._on_add_local_task_in_column)
            self._columns[label] = column
            self._board_layout.addWidget(column)

            local_for_column = [
                task
                for task in self._local_tasks_by_id.values()
                if task.board_name == self._current_board and task.column_label == label
            ]
            for kind, entry in self._merge_column_card_order(label, grouped[label], local_for_column):
                if kind == "gitlab":
                    issue = entry
                    card = QListWidgetItem(f"#{issue.iid} {issue.title}")
                    issue_payload = asdict(issue)
                    issue_payload["is_local"] = False
                    card.setData(Qt.ItemDataRole.UserRole, issue_payload)
                    column.list_widget.addItem(card)
                else:
                    task = entry
                    local_card = QListWidgetItem(f"LOCAL {task.title}")
                    local_card.setData(Qt.ItemDataRole.UserRole, local_task_to_payload(task))
                    column.list_widget.addItem(local_card)

    def _on_issue_moved(self, payload: dict, target_label: str) -> None:
        if bool(payload.get("is_local", False)):
            if self._current_board == self._REVIEW_BOARD_NAME:
                self.statusBar().showMessage("Local tasks cannot be placed on the Review board.", 5000)
                self._rebuild_columns(self._group_issues(self._last_issues))
                return
            task = local_task_from_payload(payload)
            if task is None:
                return
            if task.board_name != self._current_board:
                return
            if task.column_label == target_label:
                return
            self._storage.move_local_task(task.task_id, self._current_board, target_label)
            self._local_tasks_by_id[task.task_id] = LocalTask(
                task_id=task.task_id,
                board_name=self._current_board,
                column_label=target_label,
                title=task.title,
                created_at=task.created_at,
            )
            self.statusBar().showMessage("Local task moved", 3000)
            self._rebuild_columns(self._group_issues(list(self._issues_by_iid.values())))
            return
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
            item_type=str(payload.get("item_type", "issue")),
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
            if is_retryable_network_error(error):
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
                        "item_type": current.item_type,
                        "target_label": target_label,
                        "current_column": current_column,
                    },
                )
                self._issues_by_iid[(current.project_id, current.iid)] = apply_local_move(
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
            category, _column_name = parse_label(label)
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
        self._storage.save_tracking(
            self._active_issue_iid,
            self._active_issue_title,
            self._active_started_at,
            self._active_issue_project_id,
            self._active_item_type,
            self._active_target_project_id,
            self._active_target_iid,
            self._active_target_type,
            self._active_is_local,
            self._active_local_task_id,
        )

    def _load_local_tasks_state(self) -> None:
        self._local_tasks_by_id = {}
        fallback_board = next(
            (name for name in sorted(self._column_orders_by_board.keys()) if name != self._REVIEW_BOARD_NAME),
            "TT",
        )
        if fallback_board not in self._column_orders_by_board:
            self._column_orders_by_board[fallback_board] = ["open"]
        for payload in self._storage.load_local_tasks():
            task = local_task_from_payload({"is_local": True, **payload})
            if task is None:
                continue
            if task.board_name == self._REVIEW_BOARD_NAME:
                self._storage.move_local_task(task.task_id, fallback_board, "open")
                task = LocalTask(
                    task_id=task.task_id,
                    board_name=fallback_board,
                    column_label="open",
                    title=task.title,
                    created_at=task.created_at,
                )
            self._local_tasks_by_id[task.task_id] = task

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
                category, _column_name = parse_label(label)
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
                if is_retryable_network_error(error):
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
                            "item_type": issue.item_type,
                            "target_label": "open",
                            "current_column": label,
                        },
                    )
                    continue
                QMessageBox.warning(self, "Move Failed", f"Failed to move issue #{issue.iid}:\n{error}")
                self.load_issues()
                return

        local_to_move = [
            task
            for task in self._local_tasks_by_id.values()
            if task.board_name == self._current_board and task.column_label == label
        ]
        for task in local_to_move:
            self._storage.move_local_task(task.task_id, self._current_board, "open")
            self._local_tasks_by_id[task.task_id] = LocalTask(
                task_id=task.task_id,
                board_name=self._current_board,
                column_label="open",
                title=task.title,
                created_at=task.created_at,
            )

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

    def _merge_column_card_order(
        self,
        column_label: str,
        issues: list[GitLabIssue],
        local_tasks: list[LocalTask],
    ) -> list[tuple[str, GitLabIssue | LocalTask]]:
        """Build card sequence for a column using saved order keys (project:iid and local:id)."""
        key = f"{self._current_board}|{column_label}"
        preferred_order = self._issue_orders.get(key, [])
        by_issue_key = {issue_key(issue): issue for issue in issues}
        by_local_id = {task.task_id: task for task in local_tasks}
        merged: list[tuple[str, GitLabIssue | LocalTask]] = []
        seen_issues: set[str] = set()
        seen_local: set[int] = set()

        for token in preferred_order:
            if token.startswith("local:"):
                try:
                    local_id = int(token.split(":", 1)[1])
                except (IndexError, ValueError):
                    continue
                task = by_local_id.get(local_id)
                if task is not None and local_id not in seen_local:
                    merged.append(("local", task))
                    seen_local.add(local_id)
                continue
            issue = by_issue_key.get(token)
            if issue is not None and token not in seen_issues:
                merged.append(("gitlab", issue))
                seen_issues.add(token)

        for issue in issues:
            ik = issue_key(issue)
            if ik not in seen_issues:
                merged.append(("gitlab", issue))
                seen_issues.add(ik)
        for task in sorted(local_tasks, key=lambda item: item.task_id):
            if task.task_id not in seen_local:
                merged.append(("local", task))
                seen_local.add(task.task_id)
        return merged

    def _on_add_local_task_in_column(self, column_label: str) -> None:
        self._on_add_local_task_with_column(column_label)

    def _on_add_local_task_button(self) -> None:
        default_column = "open"
        focus = QApplication.focusWidget()
        if isinstance(focus, BoardListWidget):
            default_column = focus.column_label
        elif self._current_board != self._REVIEW_BOARD_NAME and self._column_order():
            default_column = self._column_order()[0]
        self._on_add_local_task_with_column(default_column)

    def _on_add_local_task_with_column(self, column_label: str) -> None:
        if self._current_board == self._REVIEW_BOARD_NAME:
            QMessageBox.information(self, "Review Board", "Local tasks are not supported in Review board.")
            return
        title, accepted = QInputDialog.getText(
            self,
            "Add Local Task",
            f"Task title for '{self._current_board}' / '{display_label(column_label, self._current_board)}':",
        )
        if not accepted:
            return
        clean_title = title.strip()
        if not clean_title:
            QMessageBox.information(self, "Empty Value", "Task title cannot be empty.")
            return
        created = self._storage.create_local_task(self._current_board, column_label, clean_title)
        task = local_task_from_payload({"is_local": True, **created})
        if task is None:
            return
        self._local_tasks_by_id[task.task_id] = task
        self._rebuild_columns(self._group_issues(self._last_issues))

    def _start_work_from_payload(self, payload: dict) -> None:
        if bool(payload.get("is_local", False)):
            task = local_task_from_payload(payload)
            if task is None:
                return
            if self._active_started_at is not None and (
                self._active_is_local or self._active_issue_iid is not None
            ):
                self._stop_active_work()
            self._active_is_local = True
            self._active_local_task_id = task.task_id
            self._active_issue_iid = None
            self._active_issue_project_id = None
            self._active_item_type = "local_task"
            self._active_target_project_id = None
            self._active_target_iid = None
            self._active_target_type = "local_task"
            self._active_issue_title = task.title
            self._active_started_at = time.time()
            self._save_board_state()
            self._refresh_tracking_info()
            return
        item_type = str(payload.get("item_type", "issue"))
        item_project_id = int(payload.get("project_id", 0))
        issue_iid = int(payload.get("iid", 0))
        title = str(payload.get("title", "")).strip()
        if issue_iid <= 0 or not title or item_project_id <= 0:
            return

        if self._active_is_local or (
            self._active_issue_iid is not None
            and (
                self._active_issue_iid != issue_iid
                or self._active_issue_project_id != item_project_id
                or self._active_item_type != item_type
            )
        ):
            self._stop_active_work()

        target_project_id, target_iid, target_type = self._resolve_tracking_target(item_type, item_project_id, issue_iid)
        self._active_issue_iid = issue_iid
        self._active_issue_project_id = item_project_id
        self._active_item_type = item_type
        self._active_target_project_id = target_project_id
        self._active_target_iid = target_iid
        self._active_target_type = target_type
        self._active_issue_title = title
        self._active_started_at = time.time()
        self._save_board_state()
        self._refresh_tracking_info()

    def _stop_work_from_payload(self, payload: dict) -> None:
        if bool(payload.get("is_local", False)):
            task = local_task_from_payload(payload)
            if task is None:
                return
            if not self._active_is_local or self._active_local_task_id != task.task_id:
                return
            self._stop_active_work()
            return
        item_type = str(payload.get("item_type", "issue"))
        item_project_id = int(payload.get("project_id", 0))
        issue_iid = int(payload.get("iid", 0))
        if self._active_issue_iid is None:
            return
        if (
            issue_iid != self._active_issue_iid
            or item_project_id != self._active_issue_project_id
            or item_type != self._active_item_type
        ):
            return
        self._stop_active_work()

    def _stop_active_work(self) -> None:
        bump_gitlab_today: tuple[str, int, int, int] | None = None
        if self._active_started_at is not None and (
            self._active_is_local or self._active_issue_iid is not None
        ):
            finished_at = time.time()
            elapsed_seconds = max(1, int(finished_at - self._active_started_at))
            if self._active_is_local and self._active_local_task_id is not None:
                self._storage.add_local_time_event(
                    self._active_local_task_id,
                    self._active_started_at,
                    finished_at,
                    elapsed_seconds,
                )
                self._today_total_seconds += elapsed_seconds
                self.statusBar().showMessage(
                    f"Local time saved: {format_duration(elapsed_seconds)}",
                    5000,
                )
                self._active_issue_iid = None
                self._active_is_local = False
                self._active_local_task_id = None
                self._active_issue_project_id = None
                self._active_item_type = "issue"
                self._active_target_project_id = None
                self._active_target_iid = None
                self._active_target_type = "issue"
                self._active_issue_title = ""
                self._active_started_at = None
                self._save_board_state()
                self._refresh_tracking_info()
                return
            try:
                if (
                    self._active_target_project_id is None
                    or self._active_target_iid is None
                    or not self._active_target_type
                ):
                    raise ValueError("Active tracking target is undefined")
                self._client.add_spent_time(
                    self._active_target_type,
                    self._active_target_project_id,
                    self._active_target_iid,
                    elapsed_seconds,
                )
                bump_gitlab_today = (
                    self._active_target_type,
                    self._active_target_project_id,
                    self._active_target_iid,
                    elapsed_seconds,
                )
                cache_key = (self._active_target_type, self._active_target_project_id, self._active_target_iid)
                self._spent_events_cache.setdefault(cache_key, []).append(
                    {
                        "finished_at": datetime.fromtimestamp(finished_at).isoformat(timespec="seconds"),
                        "duration_seconds": elapsed_seconds,
                    }
                )
                self.statusBar().showMessage(
                    f"Spent time added to issue #{self._active_issue_iid}: {format_duration(elapsed_seconds)}",
                    6000,
                )
            except Exception as error:  # noqa: BLE001
                if is_retryable_network_error(error):
                    self._storage.enqueue_event(
                        "add_spent_time",
                        {
                            "project_id": self._active_target_project_id,
                            "iid": self._active_target_iid,
                            "item_type": self._active_target_type,
                            "spent_seconds": elapsed_seconds,
                        },
                    )
                    bump_gitlab_today = (
                        self._active_target_type,
                        self._active_target_project_id,
                        self._active_target_iid,
                        elapsed_seconds,
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
        self._active_is_local = False
        self._active_local_task_id = None
        self._active_issue_project_id = None
        self._active_item_type = "issue"
        self._active_target_project_id = None
        self._active_target_iid = None
        self._active_target_type = "issue"
        self._active_issue_title = ""
        self._active_started_at = None
        self._save_board_state()
        if bump_gitlab_today is not None:
            self._apply_gitlab_elapsed_to_today_total(*bump_gitlab_today)
        self._refresh_tracking_info()

    def _refresh_tracking_info(self) -> None:
        active_elapsed = 0
        tracking_active = self._active_started_at is not None and (
            self._active_is_local and self._active_local_task_id is not None
            or (not self._active_is_local and self._active_issue_iid is not None)
        )
        if not tracking_active:
            self._active_issue_label.setText("None")
            self._active_time_label.setText("00:00:00")
        else:
            if self._active_is_local and self._active_local_task_id is not None:
                local_task = self._local_tasks_by_id.get(self._active_local_task_id)
                if local_task is not None:
                    self._active_issue_title = local_task.title
                self._active_issue_label.setText(f"LOCAL {self._active_issue_title}")
            else:
                current_issue = self._find_issue(
                    self._active_issue_project_id,
                    self._active_issue_iid,
                )
                if current_issue is not None:
                    self._active_issue_title = current_issue.title
                self._active_issue_label.setText(f"#{self._active_issue_iid} {self._active_issue_title}")
            active_elapsed = max(0, int(time.time() - self._active_started_at))
            self._active_time_label.setText(format_duration(active_elapsed))

        self._today_total_label.setText(format_duration(self._today_total_seconds + active_elapsed))

    def _load_tracking_state(self) -> None:
        (
            issue_iid,
            title,
            started_at,
            project_id,
            item_type,
            target_project_id,
            target_iid,
            target_type,
            active_is_local,
            active_local_task_id,
        ) = self._storage.load_tracking()
        self._active_issue_iid = issue_iid
        self._active_issue_title = title
        self._active_started_at = started_at
        self._active_is_local = active_is_local
        self._active_local_task_id = active_local_task_id
        self._active_issue_project_id = project_id
        self._active_item_type = item_type
        self._active_target_project_id = target_project_id
        self._active_target_type = target_type
        self._active_target_iid = target_iid
        if self._active_is_local:
            self._active_issue_iid = None

    def _show_issue_total_time(self, payload: dict) -> None:
        show_issue_total_time(self, payload, self._client, self._storage)

    def _show_issue_work_sessions(self, payload: dict) -> None:
        show_issue_work_sessions(
            self,
            payload,
            self._client,
            self._storage,
            self._spent_events_cache,
            self._start_today_total_scan,
            self.statusBar().showMessage,
        )

    def _report_items_with_gitlab_updates(self, since_date: date) -> list[GitLabIssue]:
        """Board items plus issues/MRs updated on or after since_date (incl. closed)."""
        base = report_source_items(self._issues_by_iid, self._review_items)
        try:
            extra = self._client.fetch_items_updated_since(since_date)
        except Exception:  # noqa: BLE001
            return base
        return merge_report_item_lists(base, extra)

    def _show_daily_report(self) -> None:
        run_daily_report(
            self,
            self._report_items_with_gitlab_updates,
            self._spent_events_cache,
            lambda item_type, project_id, issue_iid: self._load_spent_events_from_gitlab(item_type, project_id, issue_iid),
            lambda target_date: local_totals_for_day(self._storage, self._local_tasks_by_id, target_date),
        )

    def _show_period_report(self) -> None:
        run_period_report(
            self,
            lambda start_date, end_date: self._report_items_with_gitlab_updates(start_date),
            self._spent_events_cache,
            lambda item_type, project_id, issue_iid: self._load_spent_events_from_gitlab(item_type, project_id, issue_iid),
            lambda start_date, end_date: local_totals_for_period(self._storage, self._local_tasks_by_id, start_date, end_date),
        )

    def _load_timelogs_from_gitlab(self, project_id: int, issue_iid: int) -> list[dict]:
        return self._client.get_issue_time_logs(project_id, issue_iid)

    def _load_spent_events_from_gitlab(
        self,
        item_type: str,
        project_id: int,
        issue_iid: int,
        since_date: date | None = None,
    ) -> list[dict]:
        return self._client.get_spent_time_events(item_type, project_id, issue_iid, since_date=since_date)

    def _resolve_tracking_target(self, item_type: str, project_id: int, item_iid: int) -> tuple[int, int, str]:
        if item_type == "merge_request":
            try:
                issue_target = self._client.get_merge_request_closing_issue(project_id, item_iid)
                if issue_target is not None:
                    return issue_target[0], issue_target[1], "issue"
            except Exception:  # noqa: BLE001
                pass
            return project_id, item_iid, "merge_request"
        return project_id, item_iid, "issue"

    def _apply_gitlab_elapsed_to_today_total(
        self,
        target_type: str,
        target_project_id: int,
        target_iid: int,
        elapsed_seconds: int,
    ) -> None:
        """Add completed GitLab session seconds to today total and scan cache (same as local stop)."""
        today = datetime.now().date()
        day_iso = today.isoformat()
        if self._today_scan_day != day_iso:
            self._today_scan_day = day_iso
            self._today_scan_cache = self._storage.load_today_scan_cache(day_iso)
        item_key = f"{target_type}:{target_project_id}:{target_iid}"
        prev = int(self._today_scan_cache.get(item_key, {}).get("total", 0) or 0)
        add_seconds = max(1, int(elapsed_seconds))
        self._today_scan_cache[item_key] = {
            "total": float(max(0, prev + add_seconds)),
            "ts": time.time(),
        }
        self._storage.save_today_scan_cache(day_iso, self._today_scan_cache)
        self._today_total_seconds = sum(
            int(entry.get("total", 0) or 0) for entry in self._today_scan_cache.values()
        )
        self._today_total_seconds += sum(
            seconds
            for _, _, seconds in local_totals_for_day(
                self._storage,
                self._local_tasks_by_id,
                today,
            )
        )

    def _start_today_total_scan(self) -> None:
        """Start a background scan of today's spent time across all recently-updated items."""
        if self._scan_thread is not None and self._scan_thread.isRunning():
            self._scan_restart_requested = True
            return

        self._today_scan_total_seconds = 0
        today_day = datetime.now().date().isoformat()
        self._today_scan_day = today_day
        self._today_scan_cache = self._storage.load_today_scan_cache(today_day)
        self._today_total_seconds = sum(int(entry.get("total", 0) or 0) for entry in self._today_scan_cache.values())
        self._today_total_seconds += sum(
            seconds
            for _, _, seconds in local_totals_for_day(
                self._storage,
                self._local_tasks_by_id,
                datetime.now().date(),
            )
        )
        self._refresh_tracking_info()
        today = datetime.now().date()
        thread = TodayScanThread(self._client, today, dict(self._today_scan_cache), self)
        thread.item_scanned.connect(self._on_scan_item)
        thread.finished.connect(self._on_scan_finished)
        thread.finished.connect(thread.deleteLater)
        self._current_scan_worker = thread
        self._scan_thread = thread
        thread.start()

    def _on_scan_item(
        self,
        item_type: str,
        project_id: int,
        iid: int,
        events: list,
        item_total: int,
        from_cache: bool,
    ) -> None:
        """Receive one scanned item from TodayScanWorker and update the today total."""
        if self._current_scan_worker is None or self.sender() is not self._current_scan_worker:
            return
        cache_key = (item_type, project_id, iid)
        item_key = f"{item_type}:{project_id}:{iid}"
        old_total = int(self._today_scan_cache.get(item_key, {}).get("total", 0) or 0)
        self._today_scan_cache[item_key] = {"total": float(max(0, item_total)), "ts": time.time()}
        if not from_cache:
            self._spent_events_cache[cache_key] = events
        self._today_total_seconds = max(0, self._today_total_seconds - old_total + max(0, item_total))
        self._refresh_tracking_info()

    def _on_scan_finished(self) -> None:
        """Persist scan cache and optionally restart scan if requested while running."""
        if self._today_scan_day:
            self._storage.save_today_scan_cache(self._today_scan_day, self._today_scan_cache)
        self._current_scan_worker = None
        self._scan_thread = None
        if self._scan_restart_requested:
            self._scan_restart_requested = False
            self._start_today_total_scan()

    def _sync_pending_events(self) -> None:
        """Launch SyncWorker to flush the offline queue in a background thread."""
        if self._refresh_in_progress:
            return
        if self._sync_worker_thread is not None and self._sync_worker_thread.isRunning():
            return
        queued = self._storage.load_events(limit=20)
        if not queued:
            return
        thread = SyncThread(self._client, self._storage, queued, self)
        thread.scan_needed.connect(self._start_today_total_scan)
        thread.finished.connect(self._on_sync_finished)
        thread.finished.connect(thread.deleteLater)
        self._sync_worker_thread = thread
        thread.start()

    def _on_sync_finished(self) -> None:
        """Clear sync thread reference after completion."""
        self._sync_worker_thread = None

    def _find_issue(self, project_id: int | None, iid: int | None) -> GitLabIssue | None:
        if project_id is None or iid is None:
            return None
        return self._issues_by_iid.get((project_id, iid))

    def _is_review_issue(self, issue: GitLabIssue) -> bool:
        current_user_id = self._client.user_id
        if current_user_id is None:
            return False
        return (current_user_id in issue.reviewer_ids) and (current_user_id not in issue.assignee_ids)

    def _is_assignee_issue(self, issue: GitLabIssue) -> bool:
        current_user_id = self._client.user_id
        if current_user_id is None:
            return True
        return current_user_id in issue.assignee_ids


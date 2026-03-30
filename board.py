"""Kanban board UI."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import asdict
from datetime import date, datetime
from pathlib import Path
import time

import requests
from PySide6.QtCore import Qt, QObject, QThread, QTimer, QUrl, Signal, Slot
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
from models import GitLabIssue, LocalTask, local_task_from_payload, local_task_to_payload
from storage import AppStorage


def _issue_to_dict(issue: GitLabIssue) -> dict:
    """Serialize GitLabIssue to a plain dict for SQLite caching."""
    return asdict(issue)


def _issue_from_dict(data: dict) -> GitLabIssue | None:
    """Deserialize a GitLabIssue from a plain dict; return None if data is invalid."""
    try:
        return GitLabIssue(
            project_id=int(data["project_id"]),
            iid=int(data["iid"]),
            title=str(data["title"]),
            web_url=str(data["web_url"]),
            labels=[str(lbl) for lbl in data.get("labels", [])],
            assignee_ids=[int(v) for v in data.get("assignee_ids", [])],
            reviewer_ids=[int(v) for v in data.get("reviewer_ids", [])],
            item_type=str(data.get("item_type", "issue")),
        )
    except (KeyError, TypeError, ValueError):
        return None


class BoardListWidget(QListWidget):
    """List that accepts dropped issue cards from other columns."""

    issue_moved = Signal(dict, str)
    start_work_requested = Signal(dict)
    stop_work_requested = Signal(dict)
    show_total_time_requested = Signal(dict)
    show_work_sessions_requested = Signal(dict)
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

    @property
    def column_label(self) -> str:
        return self._column_label


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


def _is_retryable_network_error(error: Exception) -> bool:
    """Return True for transient network errors that should be retried later."""
    if isinstance(error, requests.RequestException):
        if error.response is None:
            return True
        return error.response.status_code >= 500
    return False


class SyncThread(QThread):
    """Background thread that flushes the offline event queue to GitLab."""

    scan_needed = Signal()

    def __init__(self, client: GitLabClient, storage: AppStorage, events: list[dict], parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._client = client
        self._storage = storage
        self._events = events

    def run(self) -> None:
        processed_any = False
        for event in self._events:
            event_id = int(event["id"])
            event_type = str(event["event_type"])
            payload = event["payload"]
            try:
                if event_type == "add_spent_time":
                    self._client.add_spent_time(
                        str(payload.get("item_type", "issue")),
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
                        labels=[str(lbl) for lbl in payload.get("labels", [])],
                        assignee_ids=[int(v) for v in payload.get("assignee_ids", [])],
                        reviewer_ids=[int(v) for v in payload.get("reviewer_ids", [])],
                        item_type=str(payload.get("item_type", "issue")),
                    )
                    self._client.move_issue_to_label(
                        issue=issue,
                        target_label=str(payload["target_label"]),
                        current_column=str(payload["current_column"]),
                    )
                self._storage.delete_event(event_id)
                processed_any = True
            except Exception as error:  # noqa: BLE001
                if _is_retryable_network_error(error):
                    break
                print(
                    f"[sync] dropped event #{event_id} ({event_type}): {error}",
                    flush=True,
                )
                self._storage.delete_event(event_id)
        if processed_any:
            self.scan_needed.emit()


class RefreshThread(QThread):
    """Background thread that fetches open issues and review MRs from GitLab."""

    result = Signal(list, list)
    error = Signal(str)

    def __init__(self, client: GitLabClient, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._client = client

    def run(self) -> None:
        import sys  # noqa: PLC0415
        started = time.perf_counter()
        try:
            t0 = time.perf_counter()
            issues = self._client.fetch_open_issues()
            t1 = time.perf_counter()
            review_mrs = self._client.fetch_review_merge_requests()
            t2 = time.perf_counter()
            print(
                f"[refresh] open_issues={t1 - t0:.3f}s ({len(issues)}), "
                f"review_mrs={t2 - t1:.3f}s ({len(review_mrs)}), total={t2 - started:.3f}s",
                flush=True, file=sys.stderr,
            )
            self.result.emit(issues, review_mrs)
        except Exception as exc:  # noqa: BLE001
            print(f"[refresh] failed after {time.perf_counter() - started:.3f}s: {exc}", flush=True, file=sys.stderr)
            self.error.emit(str(exc))


class TodayScanThread(QThread):
    """Background thread that scans today's spent time across all recently-updated items."""

    item_scanned = Signal(str, int, int, list, int, bool)

    def __init__(self, client: GitLabClient, today: date, cached: dict[str, dict[str, float]], parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._client = client
        self._today = today
        self._cached = cached

    def run(self) -> None:
        started = time.perf_counter()
        try:
            candidates = self._client.fetch_items_updated_since(self._today)
        except Exception:  # noqa: BLE001
            print(f"[today_scan] candidates load failed after {time.perf_counter() - started:.3f}s")
            self.finished.emit()
            return
        t_candidates = time.perf_counter()
        dedup: dict[tuple[str, int, int], GitLabIssue] = {}
        for item in candidates:
            dedup[(item.item_type, item.project_id, item.iid)] = item
        stale_items: list[GitLabIssue] = []
        now_ts = time.time()
        for item in dedup.values():
            cache_key = f"{item.item_type}:{item.project_id}:{item.iid}"
            cached_entry = self._cached.get(cache_key)
            if cached_entry is None:
                stale_items.append(item)
                continue
            cached_ts = float(cached_entry.get("ts", 0.0))
            cached_total = int(cached_entry.get("total", 0) or 0)
            if now_ts - cached_ts <= 300:
                self.item_scanned.emit(item.item_type, item.project_id, item.iid, [], cached_total, True)
            else:
                stale_items.append(item)

        for item in stale_items:
            result = self._fetch_item_total(item)
            if result is not None:
                self.item_scanned.emit(*result, False)
        import sys  # noqa: PLC0415
        print(
            f"[today_scan] candidates={len(candidates)} dedup={len(dedup)} stale={len(stale_items)} "
            f"load_candidates={t_candidates - started:.3f}s total={time.perf_counter() - started:.3f}s",
            flush=True, file=sys.stderr,
        )

    def _fetch_item_total(self, item: GitLabIssue) -> tuple[str, int, int, list, int] | None:
        """Fetch spent events for one item and compute today's total seconds."""
        try:
            events = self._client.get_spent_time_events(
                item.item_type, item.project_id, item.iid, since_date=self._today
            )
            item_total = sum(
                int(event.get("duration_seconds", 0) or 0)
                for event in events
                if self._is_today_event(event)
            )
            return (item.item_type, item.project_id, item.iid, events, item_total)
        except Exception:  # noqa: BLE001
            return None

    def _is_today_event(self, event: dict) -> bool:
        """Return True if the event's finished_at timestamp falls on today's date."""
        finished_str = event.get("finished_at")
        if not isinstance(finished_str, str):
            return False
        if int(event.get("duration_seconds", 0) or 0) <= 0:
            return False
        normalized = finished_str.strip().replace("Z", "+00:00")
        try:
            return datetime.fromisoformat(normalized).date() == self._today
        except ValueError:
            return False


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
            cached_issues = [i for d in cached[0] if (i := _issue_from_dict(d)) is not None]
            cached_mrs = [i for d in cached[1] if (i := _issue_from_dict(d)) is not None]
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
            [_issue_to_dict(i) for i in issues],
            [_issue_to_dict(i) for i in review_mrs],
        )
        self._last_issues = issues
        self._review_items = review_mrs
        self._issues_by_iid = {(issue.project_id, issue.iid): issue for issue in issues}
        self._spent_events_cache.clear()
        self._sync_boards_from_issues(issues)
        grouped = self._group_issues(issues)
        self._rebuild_columns(grouped)
        self._today_total_seconds = 0
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
            column = BoardColumn(label, self._display_label(label), len(grouped[label]) + local_count)
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
            if _is_retryable_network_error(error):
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
                if _is_retryable_network_error(error):
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
        by_issue_key = {self._issue_key(issue): issue for issue in issues}
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
            ik = self._issue_key(issue)
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
            f"Task title for '{self._current_board}' / '{self._display_label(column_label)}':",
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
                    f"Local time saved: {self._format_duration(elapsed_seconds)}",
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
                cache_key = (self._active_target_type, self._active_target_project_id, self._active_target_iid)
                self._spent_events_cache.setdefault(cache_key, []).append(
                    {
                        "finished_at": datetime.fromtimestamp(finished_at).isoformat(timespec="seconds"),
                        "duration_seconds": elapsed_seconds,
                    }
                )
                self.statusBar().showMessage(
                    f"Spent time added to issue #{self._active_issue_iid}: {self._format_duration(elapsed_seconds)}",
                    6000,
                )
            except Exception as error:  # noqa: BLE001
                if _is_retryable_network_error(error):
                    self._storage.enqueue_event(
                        "add_spent_time",
                        {
                            "project_id": self._active_target_project_id,
                            "iid": self._active_target_iid,
                            "item_type": self._active_target_type,
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
            self._active_time_label.setText(self._format_duration(active_elapsed))

        self._today_total_label.setText(self._format_duration(self._today_total_seconds + active_elapsed))

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
        if bool(payload.get("is_local", False)):
            task = local_task_from_payload(payload)
            if task is None:
                return
            total_seconds = sum(
                int(event.get("duration_seconds", 0) or 0)
                for event in self._storage.load_local_time_events(task.task_id)
            )
            QMessageBox.information(
                self,
                "Time summary LOCAL",
                f"{task.title}\n\nTotal spent: {self._format_duration(total_seconds)}",
            )
            return
        if str(payload.get("item_type", "issue")) != "issue":
            QMessageBox.information(self, "Time Summary", "Time summary is available only for issues.")
            return
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
        if bool(payload.get("is_local", False)):
            task = local_task_from_payload(payload)
            if task is None:
                return
            events = self._storage.load_local_time_events(task.task_id)
            if not events:
                QMessageBox.information(self, "Work Sessions LOCAL", "No sessions found.")
                return
            lines = [f"LOCAL {task.title}", ""]
            for event in events:
                finished_at = datetime.fromtimestamp(float(event["finished_at"]))
                lines.append(
                    f"{finished_at.strftime('%Y-%m-%d %H:%M:%S')} | "
                    f"{self._format_duration(int(event['duration_seconds']))}"
                )
            QMessageBox.information(self, "Work Sessions LOCAL", "\n".join(lines))
            return
        if str(payload.get("item_type", "issue")) != "issue":
            QMessageBox.information(self, "Work Sessions", "Work sessions are available only for issues.")
            return
        issue_iid = int(payload.get("iid", 0))
        project_id = int(payload.get("project_id", 0))
        issue_title = str(payload.get("title", "")).strip()
        if issue_iid <= 0:
            return
        try:
            spent_events = self._load_spent_events_from_gitlab("issue", project_id, issue_iid)
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
            self._client.reset_spent_time("issue", project_id, issue_iid)
            for started, finished in sorted(updated_ranges):
                self._client.add_spent_time("issue", project_id, issue_iid, finished - started)
            cache_key = ("issue", project_id, issue_iid)
            if cache_key in self._spent_events_cache:
                del self._spent_events_cache[cache_key]
            self._start_today_total_scan()
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

        issue_totals: dict[tuple[str, int, int], int] = {}
        report_items = self._report_source_items()
        progress = QProgressDialog("Building daily report...", "Cancel", 0, len(report_items), self)
        progress.setWindowTitle("Daily Report")
        progress.setWindowModality(Qt.WindowModality.WindowModal)
        progress.setMinimumDuration(0)
        gitlab_failures: list[str] = []

        for index, issue in enumerate(report_items, start=1):
            progress.setLabelText(f"Loading events for #{issue.iid} ({index}/{len(report_items)})...")
            progress.setValue(index - 1)
            QApplication.processEvents()
            if progress.wasCanceled():
                return

            try:
                cache_key = (issue.item_type, issue.project_id, issue.iid)
                if cache_key not in self._spent_events_cache:
                    self._spent_events_cache[cache_key] = self._load_spent_events_from_gitlab(
                        issue.item_type,
                        issue.project_id,
                        issue.iid,
                    )
                events = self._spent_events_cache[cache_key]
            except Exception as error:  # noqa: BLE001
                gitlab_failures.append(
                    f"{issue.item_type} project={issue.project_id} iid={issue.iid}: {error}",
                )
                progress.setValue(index)
                QApplication.processEvents()
                continue

            key = (issue.item_type, issue.project_id, issue.iid)
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

        non_zero_items = [(key, seconds) for key, seconds in issue_totals.items() if seconds > 0]
        non_zero_items.sort(key=lambda item: item[1], reverse=True)
        local_items = self._local_totals_for_day(target_date)
        local_total = sum(seconds for _, _, seconds in local_items)
        total_seconds = sum(seconds for _, seconds in non_zero_items)
        total_seconds += local_total

        lines = [f"{target_date.isoformat()} | Total: {self._format_duration(total_seconds)}", ""]
        if not non_zero_items and not local_items:
            lines.append("No tracked time for this date.")
        else:
            for (_item_type, project_id, issue_iid), seconds in non_zero_items:
                tag = self._PROJECT_TAGS.get(project_id, f"P{project_id}")
                lines.append(f"{tag} | #{issue_iid} | {self._format_duration(seconds)}")
        for _task_id, title, seconds in local_items:
            lines.append(f"LOCAL | {title} | {self._format_duration(seconds)}")
        if gitlab_failures:
            lines.extend(["", "GitLab rows omitted due to errors:"])
            max_notes = 15
            for msg in gitlab_failures[:max_notes]:
                lines.append(f"  - {msg}")
            if len(gitlab_failures) > max_notes:
                lines.append(f"  ... and {len(gitlab_failures) - max_notes} more")

        report_text = "\n".join(lines)
        reports_dir = Path(__file__).resolve().parent / "reports"
        reports_dir.mkdir(exist_ok=True)
        report_path = reports_dir / f"daily_report_{target_date.isoformat()}.txt"
        report_path.write_text(report_text, encoding="utf-8")

        msg = f"Report saved to:\n{report_path}"
        if gitlab_failures:
            QMessageBox.warning(
                self,
                "Daily Report",
                f"{msg}\n\nSome GitLab items could not be loaded ({len(gitlab_failures)}). "
                "Local totals are included; see report file for details.",
            )
        else:
            QMessageBox.information(self, "Daily Report", msg)

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

        issues = self._report_source_items()
        progress = QProgressDialog("Building period report...", "Cancel", 0, len(issues), self)
        progress.setWindowTitle("Period Report")
        progress.setWindowModality(Qt.WindowModality.WindowModal)
        progress.setMinimumDuration(0)
        gitlab_failures: list[str] = []

        grouped: dict[str, dict[str, int]] = {}
        for index, issue in enumerate(issues, start=1):
            progress.setLabelText(f"Loading events for #{issue.iid} ({index}/{len(issues)})...")
            progress.setValue(index - 1)
            QApplication.processEvents()
            if progress.wasCanceled():
                return

            try:
                cache_key = (issue.item_type, issue.project_id, issue.iid)
                if cache_key not in self._spent_events_cache:
                    self._spent_events_cache[cache_key] = self._load_spent_events_from_gitlab(
                        issue.item_type,
                        issue.project_id,
                        issue.iid,
                    )
                events = self._spent_events_cache[cache_key]
            except Exception as error:  # noqa: BLE001
                gitlab_failures.append(
                    f"{issue.item_type} project={issue.project_id} iid={issue.iid}: {error}",
                )
                progress.setValue(index)
                QApplication.processEvents()
                continue

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
        local_items = self._local_totals_for_period(start_date, end_date)
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
        if local_items:
            lines.append("LOCAL")
            for _task_id, title, seconds in local_items:
                total_seconds += seconds
                lines.append(f"LOCAL | {title} | {self._format_duration(seconds)}")
            lines.append("")
        if gitlab_failures:
            lines.extend(["GitLab rows omitted due to errors:"])
            max_notes = 15
            for msg in gitlab_failures[:max_notes]:
                lines.append(f"  - {msg}")
            if len(gitlab_failures) > max_notes:
                lines.append(f"  ... and {len(gitlab_failures) - max_notes} more")
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
        msg = f"Report saved to:\n{report_path}"
        if gitlab_failures:
            QMessageBox.warning(
                self,
                "Period Report",
                f"{msg}\n\nSome GitLab items could not be loaded ({len(gitlab_failures)}). "
                "Local totals are included; see report file for details.",
            )
        else:
            QMessageBox.information(self, "Period Report", msg)

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

    def _report_source_items(self) -> list[GitLabIssue]:
        items: dict[tuple[str, int, int], GitLabIssue] = {}
        for issue in self._issues_by_iid.values():
            items[(issue.item_type, issue.project_id, issue.iid)] = issue
        for item in self._review_items:
            items[(item.item_type, item.project_id, item.iid)] = item
        return list(items.values())

    def _local_totals_for_day(self, target_date: date) -> list[tuple[int, str, int]]:
        totals: dict[int, int] = {}
        for event in self._storage.load_local_time_events():
            duration = int(event.get("duration_seconds", 0) or 0)
            finished_at = float(event.get("finished_at", 0) or 0)
            if duration <= 0 or finished_at <= 0:
                continue
            if datetime.fromtimestamp(finished_at).date() != target_date:
                continue
            task_id = int(event["task_id"])
            totals[task_id] = totals.get(task_id, 0) + duration
        items: list[tuple[int, str, int]] = []
        for task_id, seconds in totals.items():
            if seconds <= 0:
                continue
            task = self._local_tasks_by_id.get(task_id)
            title = task.title if task is not None else f"local task #{task_id}"
            items.append((task_id, title, seconds))
        items.sort(key=lambda item: item[2], reverse=True)
        return items

    def _local_totals_for_period(self, start_date: date, end_date: date) -> list[tuple[int, str, int]]:
        totals: dict[int, int] = {}
        for event in self._storage.load_local_time_events():
            duration = int(event.get("duration_seconds", 0) or 0)
            finished_at = float(event.get("finished_at", 0) or 0)
            if duration <= 0 or finished_at <= 0:
                continue
            event_date = datetime.fromtimestamp(finished_at).date()
            if not (start_date <= event_date <= end_date):
                continue
            task_id = int(event["task_id"])
            totals[task_id] = totals.get(task_id, 0) + duration
        items: list[tuple[int, str, int]] = []
        for task_id, seconds in totals.items():
            if seconds <= 0:
                continue
            task = self._local_tasks_by_id.get(task_id)
            title = task.title if task is not None else f"local task #{task_id}"
            items.append((task_id, title, seconds))
        items.sort(key=lambda item: item[2], reverse=True)
        return items

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
            seconds for _, _, seconds in self._local_totals_for_day(datetime.now().date())
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
            item_type=issue.item_type,
        )

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

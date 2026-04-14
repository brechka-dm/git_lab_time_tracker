"""Dialog for manual work session correction."""

from __future__ import annotations

from datetime import datetime, timedelta

from PySide6.QtCore import QDate, Qt
from PySide6.QtWidgets import (
    QDateEdit,
    QDialog,
    QDialogButtonBox,
    QHeaderView,
    QHBoxLayout,
    QLabel,
    QMenu,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QTimeEdit,
    QVBoxLayout,
    QWidget,
)


class WorkSessionsDialog(QDialog):
    """Dialog for manual work session correction."""

    def __init__(self, issue_title: str, sessions: list[dict[str, float]], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Work Sessions")
        self.resize(760, 420)
        self._original: list[tuple[int, int, int]] = [
            (int(item.get("note_id", 0)), int(item["started_at"]), int(item["finished_at"])) for item in sessions
        ]
        self._editors: list[tuple[QDateEdit, QTimeEdit, QTimeEdit]] = []
        self._note_ids: list[int] = []

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(issue_title))

        table = QTableWidget(self)
        table.setColumnCount(4)
        table.setHorizontalHeaderLabels(["Date", "Start", "End", "Duration"])
        table.setRowCount(0)
        table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        table.customContextMenuRequested.connect(self._show_table_context_menu)
        table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        layout.addWidget(table)
        self._table = table

        for item in sessions:
            started = datetime.fromtimestamp(float(item["started_at"]))
            finished = datetime.fromtimestamp(float(item["finished_at"]))
            self._add_row(started, finished, int(item.get("note_id", 0)))

        actions_row = QHBoxLayout()
        add_session_button = QPushButton("Add session")
        add_session_button.clicked.connect(self._add_session)
        actions_row.addWidget(add_session_button)
        actions_row.addStretch()
        layout.addLayout(actions_row)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        self._save_button = buttons.button(QDialogButtonBox.StandardButton.Save)
        self._save_button.setEnabled(False)
        layout.addWidget(buttons)
        self._on_changed()

    def _on_changed(self) -> None:
        self._refresh_durations()
        self._save_button.setEnabled(self.has_changes())

    def has_changes(self) -> bool:
        return self.get_rows() != self._original

    def get_rows(self) -> list[tuple[int, int, int]]:
        values: list[tuple[int, int, int]] = []
        for idx, (date_editor, start_editor, end_editor) in enumerate(self._editors):
            session_date = date_editor.date()
            start_time = start_editor.time()
            end_time = end_editor.time()
            started_dt = datetime(
                session_date.year(),
                session_date.month(),
                session_date.day(),
                start_time.hour(),
                start_time.minute(),
                start_time.second(),
            )
            finished_dt = datetime(
                session_date.year(),
                session_date.month(),
                session_date.day(),
                end_time.hour(),
                end_time.minute(),
                end_time.second(),
            )
            started = int(started_dt.timestamp())
            finished = int(finished_dt.timestamp())
            values.append((self._note_ids[idx], started, finished))
        return values

    def get_values(self) -> list[tuple[int, int]]:
        return [(started, finished) for _, started, finished in self.get_rows()]

    def _show_table_context_menu(self, position) -> None:
        row = self._table.rowAt(position.y())
        menu = QMenu(self)
        add_action = menu.addAction("Add session")
        delete_action = None
        if row >= 0:
            self._table.selectRow(row)
            delete_action = menu.addAction("Delete session")
        chosen = menu.exec(self._table.viewport().mapToGlobal(position))
        if chosen is add_action:
            self._add_session()
        elif delete_action is not None and chosen is delete_action:
            self._delete_row(row)

    def _delete_row(self, row: int) -> None:
        if row < 0:
            return
        self._table.removeRow(row)
        self._editors.pop(row)
        self._note_ids.pop(row)
        self._on_changed()

    def _refresh_durations(self) -> None:
        for idx, (_, start_editor, end_editor) in enumerate(self._editors):
            start_secs = start_editor.time().hour() * 3600 + start_editor.time().minute() * 60 + start_editor.time().second()
            end_secs = end_editor.time().hour() * 3600 + end_editor.time().minute() * 60 + end_editor.time().second()
            seconds = max(0, end_secs - start_secs)
            item = self._table.item(idx, 3)
            if item is None:
                item = QTableWidgetItem("")
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                self._table.setItem(idx, 3, item)
            item.setText(_format_duration_seconds(seconds))

    def _add_session(self) -> None:
        now = datetime.now()
        start = now.replace(second=0, microsecond=0)
        finish = start + timedelta(seconds=1)
        if finish.date() != start.date():
            finish = start.replace(hour=23, minute=59, second=59)
        self._add_row(start, finish, 0)
        self._on_changed()

    def _add_row(self, started: datetime, finished: datetime, note_id: int) -> None:
        row = self._table.rowCount()
        self._table.insertRow(row)
        date_editor = QDateEdit(self)
        date_editor.setCalendarPopup(True)
        date_editor.setDisplayFormat("yyyy-MM-dd")
        date_editor.setDate(QDate(started.year, started.month, started.day))
        self._table.setCellWidget(row, 0, date_editor)

        start_editor = QTimeEdit(self)
        start_editor.setDisplayFormat("HH:mm:ss")
        start_editor.setTime(started.time())
        self._table.setCellWidget(row, 1, start_editor)

        end_editor = QTimeEdit(self)
        end_editor.setDisplayFormat("HH:mm:ss")
        end_editor.setTime(finished.time())
        self._table.setCellWidget(row, 2, end_editor)

        duration_item = QTableWidgetItem(_format_duration_seconds(max(0, int((finished - started).total_seconds()))))
        duration_item.setFlags(duration_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
        self._table.setItem(row, 3, duration_item)

        date_editor.dateChanged.connect(self._on_changed)
        start_editor.timeChanged.connect(self._on_changed)
        end_editor.timeChanged.connect(self._on_changed)
        self._editors.append((date_editor, start_editor, end_editor))
        self._note_ids.append(note_id)


def _format_duration_seconds(seconds: int) -> str:
    value = max(0, int(seconds))
    hours = value // 3600
    minutes = (value % 3600) // 60
    secs = value % 60
    return f"{hours:02d}:{minutes:02d}:{secs:02d}"

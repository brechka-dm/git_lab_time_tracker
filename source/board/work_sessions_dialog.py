"""Dialog for manual work session correction."""

from __future__ import annotations

from datetime import datetime

from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QLabel,
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

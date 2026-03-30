"""Single kanban column with header controls and task list."""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QPushButton, QSizePolicy, QVBoxLayout

from .board_list_widget import BoardListWidget


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

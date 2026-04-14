"""Entry point for GitLab kanban tracker."""

from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QBrush, QColor, QIcon, QPainter, QPen, QPixmap
from PySide6.QtWidgets import QApplication, QMessageBox

from board import TrackerWindow
from config import load_config, resolve_gitlab_config_paths
from crash_report import install_crash_hooks
from gitlab_client import GitLabClient
from storage import AppStorage


def _exe_dir() -> Path:
    """Return the directory that contains Tracker.exe (or the project root during development)."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent.parent


def _build_app_icon() -> QIcon:
    """Build app icon programmatically (stopwatch motif)."""
    size = 256
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QBrush(QColor("#1f4f96")))
    painter.drawRoundedRect(QRectF(8, 8, size - 16, size - 16), 40, 40)

    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.setPen(QPen(QColor("#ffffff"), 16))
    painter.drawEllipse(QRectF(52, 58, 152, 152))

    painter.setPen(QPen(QColor("#ffffff"), 14, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
    center = QPointF(128, 134)
    painter.drawLine(center, QPointF(128, 94))
    painter.drawLine(center, QPointF(158, 134))

    painter.setPen(QPen(QColor("#f39c12"), 12, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
    painter.drawPoint(QPointF(184, 56))

    painter.end()
    return QIcon(pixmap)


def main() -> int:
    """Start desktop app."""
    exe_dir = _exe_dir()
    install_crash_hooks(exe_dir)
    app = QApplication(sys.argv)
    app_icon = _build_app_icon()
    app.setWindowIcon(app_icon)
    base_ini, user_ini = resolve_gitlab_config_paths(exe_dir)

    try:
        config = load_config(base_ini, user_ini)
    except FileNotFoundError as error:
        QMessageBox.critical(None, "GitLab config missing", str(error))
        return 1
    except ValueError as error:
        QMessageBox.critical(None, "GitLab config incomplete", str(error))
        return 1
    except Exception as error:  # noqa: BLE001
        QMessageBox.critical(None, "Config error", f"Cannot read config:\n{error}")
        return 1

    client = GitLabClient(config)
    database_path = exe_dir / "tracker.db"
    storage = AppStorage(database_path)
    window = TrackerWindow(client, storage=storage)
    window.setWindowIcon(app_icon)
    window.show()
    window.load_issues()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())

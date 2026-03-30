"""Entry point for GitLab kanban tracker."""

from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtWidgets import QApplication, QMessageBox

from board import TrackerWindow
from config import load_config
from gitlab_client import GitLabClient
from storage import AppStorage


def main() -> int:
    """Start desktop app."""
    app = QApplication(sys.argv)
    config_path = Path(__file__).with_name("gitlab_access.ini")

    try:
        config = load_config(config_path)
    except Exception as error:  # noqa: BLE001
        QMessageBox.critical(None, "Config error", f"Cannot read config:\n{error}")
        return 1

    client = GitLabClient(config)
    database_path = Path(__file__).with_name("tracker.db")
    storage = AppStorage(database_path)
    window = TrackerWindow(client, storage=storage)
    window.show()
    window.load_issues()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())

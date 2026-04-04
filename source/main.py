"""Entry point for GitLab kanban tracker."""

from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtWidgets import QApplication, QMessageBox

from board import TrackerWindow
from config import load_config
from crash_report import install_crash_hooks
from gitlab_client import GitLabClient
from storage import AppStorage


def _exe_dir() -> Path:
    """Return the directory that contains Tracker.exe (or the project root during development)."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent.parent


def _find_config(exe_dir: Path) -> Path:
    """Locate gitlab_access.ini: prefer a copy next to the exe, fall back to _internal."""
    candidate = exe_dir / "gitlab_access.ini"
    if candidate.exists():
        return candidate
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        bundled = Path(sys._MEIPASS) / "gitlab_access.ini"  # noqa: SLF001
        if bundled.exists():
            return bundled
    return candidate


def main() -> int:
    """Start desktop app."""
    exe_dir = _exe_dir()
    install_crash_hooks(exe_dir)
    app = QApplication(sys.argv)
    config_path = _find_config(exe_dir)

    try:
        config = load_config(config_path)
    except Exception as error:  # noqa: BLE001
        QMessageBox.critical(None, "Config error", f"Cannot read config:\n{error}")
        return 1

    client = GitLabClient(config)
    database_path = exe_dir / "tracker.db"
    storage = AppStorage(database_path)
    window = TrackerWindow(client, storage=storage)
    window.show()
    window.load_issues()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())

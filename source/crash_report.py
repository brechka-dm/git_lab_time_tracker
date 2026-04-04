"""Append-only crash log next to the executable / project root."""

from __future__ import annotations

import sys
import threading
import traceback
from datetime import datetime
from pathlib import Path

_CRASH_LOG_NAME = "crash_report.txt"


def crash_report_path(exe_dir: Path) -> Path:
    """Return path to the crash log file."""
    return exe_dir / _CRASH_LOG_NAME


def append_crash_text(exe_dir: Path, title: str, body: str) -> None:
    """Append one crash record; never raises."""
    path = crash_report_path(exe_dir)
    stamp = datetime.now().isoformat(sep=" ", timespec="seconds")
    block = f"\n{'=' * 72}\n{stamp}  {title}\n{'-' * 72}\n{body}"
    if not body.endswith("\n"):
        block += "\n"
    try:
        with path.open("a", encoding="utf-8") as handle:
            handle.write(block)
    except OSError:
        pass


def append_crash_exception(
    exe_dir: Path,
    title: str,
    exc_type: type[BaseException],
    exc_value: BaseException | None,
    exc_tb: traceback.TracebackType | None,
) -> None:
    """Format exception with traceback and append; never raises."""
    lines = traceback.format_exception(exc_type, exc_value, exc_tb)
    append_crash_text(exe_dir, title, "".join(lines))


def install_crash_hooks(exe_dir: Path) -> None:
    """Log uncaught exceptions from the main thread and from worker threads."""
    prev_thread_hook = threading.excepthook

    def _main_excepthook(
        exc_type: type[BaseException],
        exc_value: BaseException | None,
        exc_tb: traceback.TracebackType | None,
    ) -> None:
        if exc_type is KeyboardInterrupt:
            sys.__excepthook__(exc_type, exc_value, exc_tb)
            return
        if not issubclass(exc_type, Exception):
            sys.__excepthook__(exc_type, exc_value, exc_tb)
            return
        append_crash_exception(exe_dir, "Uncaught exception (main thread)", exc_type, exc_value, exc_tb)
        sys.__excepthook__(exc_type, exc_value, exc_tb)

    sys.excepthook = _main_excepthook

    def _thread_excepthook(args: object) -> None:
        exc_type = getattr(args, "exc_type", None)
        exc_value = getattr(args, "exc_value", None)
        exc_traceback = getattr(args, "exc_traceback", None)
        thread = getattr(args, "thread", None)
        thread_name = getattr(thread, "name", "?") if thread is not None else "?"
        if exc_type is KeyboardInterrupt:
            prev_thread_hook(args)
            return
        if exc_type is None or not issubclass(exc_type, Exception):
            prev_thread_hook(args)
            return
        title = f"Uncaught exception in thread {thread_name!r}"
        append_crash_exception(exe_dir, title, exc_type, exc_value, exc_traceback)
        prev_thread_hook(args)

    threading.excepthook = _thread_excepthook

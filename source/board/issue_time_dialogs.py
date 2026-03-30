"""Qt flows for issue time summary and work sessions."""

from __future__ import annotations

from datetime import datetime
from typing import Callable

from PySide6.QtWidgets import QDialog, QMessageBox, QWidget

from gitlab_client import GitLabClient
from models import local_task_from_payload
from storage import AppStorage

from .time_formatting import format_duration, parse_iso_datetime
from .work_sessions_dialog import WorkSessionsDialog


def show_issue_total_time(
    parent: QWidget,
    payload: dict,
    client: GitLabClient,
    storage: AppStorage,
) -> None:
    """Show total tracked time for local task or GitLab issue."""
    if bool(payload.get("is_local", False)):
        task = local_task_from_payload(payload)
        if task is None:
            return
        total_seconds = sum(
            int(event.get("duration_seconds", 0) or 0)
            for event in storage.load_local_time_events(task.task_id)
        )
        QMessageBox.information(
            parent,
            "Time summary LOCAL",
            f"{task.title}\n\nTotal spent: {format_duration(total_seconds)}",
        )
        return
    if str(payload.get("item_type", "issue")) != "issue":
        QMessageBox.information(parent, "Time Summary", "Time summary is available only for issues.")
        return
    issue_iid = int(payload.get("iid", 0))
    title = str(payload.get("title", "")).strip()
    project_id = int(payload.get("project_id", 0))
    if issue_iid <= 0:
        return
    try:
        stats = client.get_issue_time_summary(project_id, issue_iid)
    except Exception as error:  # noqa: BLE001
        QMessageBox.warning(parent, "GitLab time tracking", f"Failed to load time summary:\n{error}")
        return

    total_seconds = int(stats.get("total_time_spent", 0) or 0)
    estimate_seconds = int(stats.get("time_estimate", 0) or 0)
    QMessageBox.information(
        parent,
        f"Time summary #{issue_iid}",
        (
            f"{title}\n\n"
            f"Total spent: {format_duration(total_seconds)}\n"
            f"Estimate: {format_duration(estimate_seconds)}"
        ),
    )


def show_issue_work_sessions(
    parent: QWidget,
    payload: dict,
    client: GitLabClient,
    storage: AppStorage,
    spent_events_cache: dict[tuple[str, int, int], list[dict]],
    restart_today_scan: Callable[[], None],
    show_status_message: Callable[[str, int], None],
) -> None:
    """Show and optionally edit work sessions for local task or issue."""
    if bool(payload.get("is_local", False)):
        task = local_task_from_payload(payload)
        if task is None:
            return
        events = storage.load_local_time_events(task.task_id)
        if not events:
            QMessageBox.information(parent, "Work Sessions LOCAL", "No sessions found.")
            return
        lines = [f"LOCAL {task.title}", ""]
        for event in events:
            finished_at = datetime.fromtimestamp(float(event["finished_at"]))
            lines.append(
                f"{finished_at.strftime('%Y-%m-%d %H:%M:%S')} | "
                f"{format_duration(int(event['duration_seconds']))}"
            )
        QMessageBox.information(parent, "Work Sessions LOCAL", "\n".join(lines))
        return
    if str(payload.get("item_type", "issue")) != "issue":
        QMessageBox.information(parent, "Work Sessions", "Work sessions are available only for issues.")
        return
    issue_iid = int(payload.get("iid", 0))
    project_id = int(payload.get("project_id", 0))
    issue_title = str(payload.get("title", "")).strip()
    if issue_iid <= 0:
        return
    try:
        spent_events = client.get_spent_time_events("issue", project_id, issue_iid)
    except Exception as error:  # noqa: BLE001
        QMessageBox.warning(parent, "GitLab Time Tracking", f"Failed to load work sessions:\n{error}")
        return
    if not spent_events:
        QMessageBox.information(parent, f"Work Sessions #{issue_iid}", "No sessions found.")
        return

    sessions: list[dict[str, float]] = []
    for event in spent_events:
        finished_at = parse_iso_datetime(event.get("finished_at"))
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

    dialog = WorkSessionsDialog(f"#{issue_iid} {issue_title}", sessions, parent)
    if dialog.exec() != QDialog.DialogCode.Accepted:
        return
    if not dialog.has_changes():
        return

    updated_ranges = dialog.get_values()
    for started, finished in updated_ranges:
        if finished <= started:
            QMessageBox.warning(parent, "Work Sessions", "End time must be greater than start time.")
            return

    try:
        client.reset_spent_time("issue", project_id, issue_iid)
        for started, finished in sorted(updated_ranges):
            client.add_spent_time("issue", project_id, issue_iid, finished - started)
        cache_key = ("issue", project_id, issue_iid)
        if cache_key in spent_events_cache:
            del spent_events_cache[cache_key]
        restart_today_scan()
        show_status_message(f"Work sessions saved for issue #{issue_iid}", 5000)
    except Exception as error:  # noqa: BLE001
        QMessageBox.warning(parent, "Work Sessions", f"Failed to save sessions:\n{error}")

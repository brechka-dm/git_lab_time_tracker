"""Aggregations for local task time events."""

from __future__ import annotations

from datetime import date, datetime

from models import LocalTask
from storage import AppStorage


def local_totals_for_day(
    storage: AppStorage,
    local_tasks_by_id: dict[int, LocalTask],
    target_date: date,
) -> list[tuple[int, str, int]]:
    """Return local task totals for a single date."""
    totals: dict[int, int] = {}
    for event in storage.load_local_time_events():
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
        task = local_tasks_by_id.get(task_id)
        title = task.title if task is not None else f"local task #{task_id}"
        items.append((task_id, title, seconds))
    items.sort(key=lambda item: item[2], reverse=True)
    return items


def local_totals_for_period(
    storage: AppStorage,
    local_tasks_by_id: dict[int, LocalTask],
    start_date: date,
    end_date: date,
) -> list[tuple[int, str, int]]:
    """Return local task totals for an inclusive date range."""
    totals: dict[int, int] = {}
    for event in storage.load_local_time_events():
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
        task = local_tasks_by_id.get(task_id)
        title = task.title if task is not None else f"local task #{task_id}"
        items.append((task_id, title, seconds))
    items.sort(key=lambda item: item[2], reverse=True)
    return items

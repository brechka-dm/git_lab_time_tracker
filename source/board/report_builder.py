"""Pure report aggregation and rendering functions."""

from __future__ import annotations

from datetime import date, datetime
from typing import Callable

from models import GitLabIssue

from .time_formatting import format_duration, parse_iso_datetime

SpentEventsCache = dict[tuple[str, int, int], list[dict]]
LoadEventsFn = Callable[[str, int, int], list[dict]]


def collect_daily_totals(
    target_date: date,
    issue: GitLabIssue,
    spent_events_cache: SpentEventsCache,
    load_events: LoadEventsFn,
) -> tuple[int, str | None]:
    """Return total seconds for one issue on target date and optional error text."""
    try:
        cache_key = (issue.item_type, issue.project_id, issue.iid)
        if cache_key not in spent_events_cache:
            spent_events_cache[cache_key] = load_events(issue.item_type, issue.project_id, issue.iid)
        events = spent_events_cache[cache_key]
    except Exception as error:  # noqa: BLE001
        return 0, f"{issue.item_type} project={issue.project_id} iid={issue.iid}: {error}"

    total = 0
    for event in events:
        finished_at = parse_iso_datetime(event.get("finished_at"))
        duration = int(event.get("duration_seconds", 0) or 0)
        if finished_at is None or duration <= 0:
            continue
        event_date = datetime.fromtimestamp(finished_at).date()
        if event_date == target_date:
            total += duration
    return total, None


def collect_period_totals(
    start_date: date,
    end_date: date,
    issue: GitLabIssue,
    spent_events_cache: SpentEventsCache,
    load_events: LoadEventsFn,
) -> tuple[int, str | None]:
    """Return total seconds for one issue in an inclusive period and optional error text."""
    try:
        cache_key = (issue.item_type, issue.project_id, issue.iid)
        if cache_key not in spent_events_cache:
            spent_events_cache[cache_key] = load_events(issue.item_type, issue.project_id, issue.iid)
        events = spent_events_cache[cache_key]
    except Exception as error:  # noqa: BLE001
        return 0, f"{issue.item_type} project={issue.project_id} iid={issue.iid}: {error}"

    total = 0
    for event in events:
        finished_at = parse_iso_datetime(event.get("finished_at"))
        duration = int(event.get("duration_seconds", 0) or 0)
        if finished_at is None or duration <= 0:
            continue
        event_date = datetime.fromtimestamp(finished_at).date()
        if start_date <= event_date <= end_date:
            total += duration
    return total, None


def render_daily_report_text(
    target_date: date,
    issue_totals: list[tuple[tuple[str, int, int], int]],
    local_items: list[tuple[int, str, int]],
    project_tags: dict[int, str],
    gitlab_failures: list[str],
) -> str:
    """Render daily report text using collected totals."""
    local_total = sum(seconds for _, _, seconds in local_items)
    total_seconds = sum(seconds for _, seconds in issue_totals) + local_total
    lines = [f"{target_date.isoformat()} | Total: {format_duration(total_seconds)}", ""]
    if not issue_totals and not local_items:
        lines.append("No tracked time for this date.")
    else:
        for (_item_type, project_id, issue_iid), seconds in issue_totals:
            tag = project_tags.get(project_id, f"P{project_id}")
            lines.append(f"{tag} | #{issue_iid} | {format_duration(seconds)}")
    for _task_id, title, seconds in local_items:
        lines.append(f"LOCAL | {title} | {format_duration(seconds)}")
    if gitlab_failures:
        lines.extend(["", "GitLab rows omitted due to errors:"])
        max_notes = 15
        for msg in gitlab_failures[:max_notes]:
            lines.append(f"  - {msg}")
        if len(gitlab_failures) > max_notes:
            lines.append(f"  ... and {len(gitlab_failures) - max_notes} more")
    return "\n".join(lines)


def render_period_report_text(
    start_date: date,
    end_date: date,
    grouped: dict[str, dict[str, int]],
    local_items: list[tuple[int, str, int]],
    gitlab_failures: list[str],
) -> str:
    """Render period report text using collected per-project totals."""
    total_seconds = 0
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
            lines.append(f"{title} | {format_duration(seconds)}")
        lines.append("")
    if local_items:
        lines.append("LOCAL")
        for _task_id, title, seconds in local_items:
            total_seconds += seconds
            lines.append(f"LOCAL | {title} | {format_duration(seconds)}")
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
        lines.insert(1, f"Total: {format_duration(total_seconds)}")
        lines.insert(2, "")
    return "\n".join(lines)

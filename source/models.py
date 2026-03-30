"""Domain models for board items."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class GitLabIssue:
    """Open issue presented on the board."""

    project_id: int
    iid: int
    title: str
    web_url: str
    labels: list[str]
    assignee_ids: list[int]
    reviewer_ids: list[int]
    item_type: str = "issue"


@dataclass(frozen=True)
class LocalTask:
    """Local task stored only in SQLite and shown on board."""

    task_id: int
    board_name: str
    column_label: str
    title: str
    created_at: float


def local_task_to_payload(task: LocalTask) -> dict:
    """Serialize LocalTask for UI card payload."""
    return {
        "is_local": True,
        "task_id": task.task_id,
        "board_name": task.board_name,
        "column_label": task.column_label,
        "title": task.title,
        "created_at": task.created_at,
        "item_type": "local_task",
    }


def local_task_from_payload(payload: dict) -> LocalTask | None:
    """Deserialize LocalTask from UI payload."""
    try:
        if not bool(payload.get("is_local", False)):
            return None
        return LocalTask(
            task_id=int(payload["task_id"]),
            board_name=str(payload["board_name"]),
            column_label=str(payload["column_label"]),
            title=str(payload["title"]),
            created_at=float(payload["created_at"]),
        )
    except (KeyError, TypeError, ValueError):
        return None

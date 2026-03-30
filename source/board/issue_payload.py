"""Serialize GitLabIssue for SQLite issue cache."""

from __future__ import annotations

from dataclasses import asdict

from models import GitLabIssue


def issue_to_dict(issue: GitLabIssue) -> dict:
    """Serialize GitLabIssue to a plain dict for SQLite caching."""
    return asdict(issue)


def issue_from_dict(data: dict) -> GitLabIssue | None:
    """Deserialize a GitLabIssue from a plain dict; return None if data is invalid."""
    try:
        return GitLabIssue(
            project_id=int(data["project_id"]),
            iid=int(data["iid"]),
            title=str(data["title"]),
            web_url=str(data["web_url"]),
            labels=[str(lbl) for lbl in data.get("labels", [])],
            assignee_ids=[int(v) for v in data.get("assignee_ids", [])],
            reviewer_ids=[int(v) for v in data.get("reviewer_ids", [])],
            item_type=str(data.get("item_type", "issue")),
        )
    except (KeyError, TypeError, ValueError):
        return None

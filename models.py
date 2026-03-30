"""Domain models for GitLab issues."""

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

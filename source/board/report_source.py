"""Source items used by report generation."""

from __future__ import annotations

from models import GitLabIssue


def report_source_items(
    issues_by_iid: dict[tuple[int, int], GitLabIssue],
    review_items: list[GitLabIssue],
) -> list[GitLabIssue]:
    """Return unique issue/MR list for reports."""
    items: dict[tuple[str, int, int], GitLabIssue] = {}
    for issue in issues_by_iid.values():
        items[(issue.item_type, issue.project_id, issue.iid)] = issue
    for item in review_items:
        items[(item.item_type, item.project_id, item.iid)] = item
    return list(items.values())

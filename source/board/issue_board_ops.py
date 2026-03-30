"""Pure operations over issue board state."""

from __future__ import annotations

from models import GitLabIssue


def issue_key(issue: GitLabIssue) -> str:
    """Build stable issue key used in card ordering."""
    return f"{issue.project_id}:{issue.iid}"


def apply_local_move(issue: GitLabIssue, target_label: str, current_column: str) -> GitLabIssue:
    """Apply local move to issue labels without network call."""
    labels = list(issue.labels)
    if current_column != "open":
        labels = [label for label in labels if label != current_column]
    if target_label != "open":
        labels.append(target_label)
    labels = list(dict.fromkeys(labels))
    return GitLabIssue(
        project_id=issue.project_id,
        iid=issue.iid,
        title=issue.title,
        web_url=issue.web_url,
        labels=labels,
        assignee_ids=issue.assignee_ids,
        reviewer_ids=issue.reviewer_ids,
        item_type=issue.item_type,
    )

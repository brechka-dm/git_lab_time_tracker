"""GitLab API access layer."""

from __future__ import annotations

from dataclasses import asdict
import re
from typing import Any

import requests

from config import AppConfig
from models import GitLabIssue


class GitLabClient:
    """Encapsulates read/write operations with GitLab issues."""

    def __init__(self, config: AppConfig) -> None:
        self._config = config
        self._base_url = f"{self._config.gitlab_url}/api/v4"
        self._session = requests.Session()
        self._session.headers.update({"PRIVATE-TOKEN": self._config.token})

    @property
    def user_id(self) -> int | None:
        """Configured current user id."""
        return self._config.user_id

    def fetch_open_issues(self) -> list[GitLabIssue]:
        """Load opened issues from configured projects."""
        issues_map: dict[tuple[int, int], GitLabIssue] = {}
        for project_id in self._config.project_ids:
            query_variants: list[dict[str, Any]] = [{}]
            if self._config.user_id is not None:
                query_variants = [
                    {"assignee_id": self._config.user_id},
                    {"reviewer_id": self._config.user_id},
                ]
            for extra_params in query_variants:
                for issue in self._fetch_project_issues(project_id, extra_params):
                    issues_map[(issue.project_id, issue.iid)] = issue
        return list(issues_map.values())

    def _fetch_project_issues(self, project_id: int, extra_params: dict[str, Any]) -> list[GitLabIssue]:
        url = f"{self._base_url}/projects/{project_id}/issues"
        issues: list[GitLabIssue] = []
        page = 1
        while True:
            params: dict[str, Any] = {
                "state": "opened",
                "per_page": 100,
                "order_by": "updated_at",
                "sort": "desc",
                "page": page,
            }
            params.update(extra_params)

            response = self._session.get(url, params=params, timeout=15)
            response.raise_for_status()

            payload = response.json()
            for item in payload:
                assignee_ids = [int(user["id"]) for user in item.get("assignees", []) if isinstance(user, dict) and "id" in user]
                reviewer_ids = [int(user["id"]) for user in item.get("reviewers", []) if isinstance(user, dict) and "id" in user]
                issues.append(
                    GitLabIssue(
                        project_id=project_id,
                        iid=item["iid"],
                        title=item["title"],
                        web_url=item["web_url"],
                        labels=[label.strip() for label in item.get("labels", []) if label.strip()],
                        assignee_ids=assignee_ids,
                        reviewer_ids=reviewer_ids,
                    )
                )
            next_page = response.headers.get("X-Next-Page", "").strip()
            if not next_page:
                break
            page = int(next_page)
        return issues

    def move_issue_to_label(
        self,
        issue: GitLabIssue,
        target_label: str,
        current_column: str,
    ) -> GitLabIssue:
        """Move issue to another board column by updating issue labels."""
        next_labels = list(issue.labels)
        if current_column != "open":
            next_labels = [label for label in next_labels if label != current_column]

        if target_label != "open":
            next_labels.append(target_label)
        next_labels = list(dict.fromkeys(next_labels))

        labels_payload = ",".join(next_labels)
        url = f"{self._base_url}/projects/{issue.project_id}/issues/{issue.iid}"
        response = self._session.put(url, data={"labels": labels_payload}, timeout=15)
        response.raise_for_status()

        payload = response.json()
        return GitLabIssue(
            project_id=issue.project_id,
            iid=payload["iid"],
            title=payload["title"],
            web_url=payload["web_url"],
            labels=[label.strip() for label in payload.get("labels", []) if label.strip()],
            assignee_ids=issue.assignee_ids,
            reviewer_ids=issue.reviewer_ids,
        )

    def add_spent_time(self, project_id: int, issue_iid: int, spent_seconds: int) -> None:
        """Add spent time to issue in GitLab."""
        seconds = max(1, int(spent_seconds))
        duration = f"{seconds}s"
        url = f"{self._base_url}/projects/{project_id}/issues/{issue_iid}/add_spent_time"
        response = self._session.post(url, data={"duration": duration}, timeout=15)
        response.raise_for_status()

    def reset_spent_time(self, project_id: int, issue_iid: int) -> None:
        """Reset spent time for issue in GitLab."""
        url = f"{self._base_url}/projects/{project_id}/issues/{issue_iid}/reset_spent_time"
        response = self._session.post(url, timeout=15)
        response.raise_for_status()

    def get_issue_time_summary(self, project_id: int, issue_iid: int) -> dict[str, Any]:
        """Get total spent and estimate info for issue."""
        url = f"{self._base_url}/projects/{project_id}/issues/{issue_iid}"
        response = self._session.get(url, timeout=15)
        response.raise_for_status()
        payload = response.json()
        return payload.get("time_stats", {})

    def get_issue_time_logs(self, project_id: int, issue_iid: int) -> list[dict[str, Any]]:
        """Get raw timelog entries from GitLab issue time stats."""
        url = f"{self._base_url}/projects/{project_id}/issues/{issue_iid}/time_stats"
        response = self._session.get(url, timeout=15)
        response.raise_for_status()
        payload = response.json()
        timelogs = payload.get("timelogs", [])
        if isinstance(timelogs, list):
            return [entry for entry in timelogs if isinstance(entry, dict)]
        return []

    def get_issue_spent_time_events(self, project_id: int, issue_iid: int) -> list[dict[str, Any]]:
        """Parse system notes with spent time additions."""
        pattern = re.compile(r"added\s+(.+?)\s+of time spent", re.IGNORECASE)
        events: list[dict[str, Any]] = []
        for note in self._fetch_issue_notes(project_id, issue_iid):
            if not bool(note.get("system", False)):
                continue
            body = str(note.get("body", ""))
            match = pattern.search(body)
            if match is None:
                continue
            duration_seconds = _parse_duration_to_seconds(match.group(1))
            if duration_seconds <= 0:
                continue
            if self._config.user_id is not None:
                author = note.get("author", {})
                author_id = int(author.get("id", 0)) if isinstance(author, dict) else 0
                if author_id != self._config.user_id:
                    continue
            created_at = str(note.get("created_at", "")).strip()
            if not created_at:
                continue
            events.append({"finished_at": created_at, "duration_seconds": duration_seconds})
        return events

    def _fetch_issue_notes(self, project_id: int, issue_iid: int) -> list[dict[str, Any]]:
        notes: list[dict[str, Any]] = []
        page = 1
        while True:
            url = f"{self._base_url}/projects/{project_id}/issues/{issue_iid}/notes"
            response = self._session.get(
                url,
                params={"per_page": 100, "page": page, "order_by": "created_at", "sort": "asc"},
                timeout=15,
            )
            response.raise_for_status()
            chunk = response.json()
            if not isinstance(chunk, list):
                break

            for note in chunk:
                if isinstance(note, dict):
                    notes.append(note)

            next_page = response.headers.get("X-Next-Page", "").strip()
            if not next_page:
                break
            page = int(next_page)
        return notes


def _parse_duration_to_seconds(text: str) -> int:
    total = 0
    for value, unit in re.findall(r"(\d+)\s*([hms])", text.lower()):
        amount = int(value)
        if unit == "h":
            total += amount * 3600
        elif unit == "m":
            total += amount * 60
        elif unit == "s":
            total += amount
    return total

def issue_to_dict(issue: GitLabIssue) -> dict[str, Any]:
    """Convert dataclass to serializable dict for Qt item payload."""
    return asdict(issue)

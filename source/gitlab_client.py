"""GitLab API access layer."""

from __future__ import annotations

from dataclasses import asdict
from datetime import date, datetime, timezone
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
                inferred_reviewer_id: int | None = None
                if "reviewer_id" in extra_params:
                    inferred_reviewer_id = int(extra_params["reviewer_id"])
                for issue in self._fetch_project_issues(
                    project_id,
                    extra_params,
                    inferred_reviewer_id=inferred_reviewer_id,
                ):
                    issues_map[(issue.project_id, issue.iid)] = issue
        return list(issues_map.values())

    def fetch_review_merge_requests(self) -> list[GitLabIssue]:
        """Load opened merge requests where current user is reviewer."""
        if self._config.user_id is None:
            return []

        items: list[GitLabIssue] = []
        for project_id in self._config.project_ids:
            url = f"{self._base_url}/projects/{project_id}/merge_requests"
            page = 1
            while True:
                params: dict[str, Any] = {
                    "state": "opened",
                    "reviewer_id": self._config.user_id,
                    "per_page": 100,
                    "page": page,
                }
                response = self._session.get(url, params=params, timeout=(5, 10))
                response.raise_for_status()
                payload = response.json()
                for item in payload:
                    assignee_ids = [int(user["id"]) for user in item.get("assignees", []) if isinstance(user, dict) and "id" in user]
                    reviewer_ids = [int(user["id"]) for user in item.get("reviewers", []) if isinstance(user, dict) and "id" in user]
                    if self._config.user_id not in reviewer_ids:
                        reviewer_ids.append(self._config.user_id)
                    items.append(
                        GitLabIssue(
                            project_id=project_id,
                            iid=item["iid"],
                            title=item["title"],
                            web_url=item["web_url"],
                            labels=[],
                            assignee_ids=assignee_ids,
                            reviewer_ids=reviewer_ids,
                            item_type="merge_request",
                        )
                    )
                next_page = response.headers.get("X-Next-Page", "").strip()
                if not next_page:
                    break
                page = int(next_page)
        return items

    def fetch_items_updated_since(self, since_date: date) -> list[GitLabIssue]:
        """Load issues and merge requests updated since date."""
        since_dt = datetime(since_date.year, since_date.month, since_date.day, tzinfo=timezone.utc).isoformat()
        items: list[GitLabIssue] = []
        for project_id in self._config.project_ids:
            items.extend(self._fetch_updated_items(project_id, "issues", since_dt))
            items.extend(self._fetch_updated_items(project_id, "merge_requests", since_dt))
        return items

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
        response = self._session.put(url, data={"labels": labels_payload}, timeout=(5, 10))
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
            item_type=issue.item_type,
        )

    def add_spent_time(
        self,
        item_type: str,
        project_id: int,
        item_iid: int,
        spent_seconds: int,
        spent_at: str | None = None,
    ) -> None:
        """Add spent time to issue or merge request in GitLab."""
        seconds = max(1, int(spent_seconds))
        duration = f"{seconds}s"
        resource = "issues" if item_type == "issue" else "merge_requests"
        url = f"{self._base_url}/projects/{project_id}/{resource}/{item_iid}/add_spent_time"
        payload: dict[str, str] = {"duration": duration}
        if isinstance(spent_at, str):
            spent_date = spent_at.strip()
            if spent_date:
                payload["spent_at"] = spent_date
        response = self._session.post(url, data=payload, timeout=(5, 10))
        response.raise_for_status()

    def add_spent_time_for_day(
        self,
        item_type: str,
        project_id: int,
        item_iid: int,
        spent_seconds: int,
        spent_day: str,
    ) -> None:
        """Add spent time tied to a specific calendar day via quick action."""
        resource = "issues" if item_type == "issue" else "merge_requests"
        duration_text = _format_seconds_for_spend_command(spent_seconds)
        day_text = spent_day.strip()
        if not day_text:
            self.add_spent_time(item_type, project_id, item_iid, spent_seconds)
            return
        url = f"{self._base_url}/projects/{project_id}/{resource}/{item_iid}/notes"
        payload = {"body": f"/spend {duration_text} {day_text}"}
        response = self._session.post(url, data=payload, timeout=(5, 10))
        response.raise_for_status()

    def reset_spent_time(self, item_type: str, project_id: int, item_iid: int) -> None:
        """Reset spent time for issue or merge request in GitLab."""
        resource = "issues" if item_type == "issue" else "merge_requests"
        url = f"{self._base_url}/projects/{project_id}/{resource}/{item_iid}/reset_spent_time"
        response = self._session.post(url, timeout=(5, 10))
        response.raise_for_status()

    def delete_issue_note(self, item_type: str, project_id: int, item_iid: int, note_id: int) -> None:
        """Delete a single issue or merge request note (used to remove one spent-time system note)."""
        resource = "issues" if item_type == "issue" else "merge_requests"
        url = f"{self._base_url}/projects/{project_id}/{resource}/{item_iid}/notes/{note_id}"
        response = self._session.delete(url, timeout=(5, 10))
        response.raise_for_status()

    def apply_issue_work_session_edits(
        self,
        project_id: int,
        issue_iid: int,
        original_rows: list[dict[str, int]],
        updated_rows: list[tuple[int, int, int]],
    ) -> None:
        """Push work-session edits without resetting the whole issue when possible.

        Increases only call add_spent_time with the delta. Decreases delete the matching
        system note (note_id) then re-add the new duration. If a decrease targets an
        entry without note_id, falls back to reset_spent_time plus re-adding all sessions.
        Deleted rows are removed by deleting their spent-time note.
        """
        ops: list[tuple[str, ...]] = []
        old_rows_by_note: dict[int, tuple[int, int]] = {}
        old_missing_durations: list[int] = []
        for row in original_rows:
            note_id = int(row.get("note_id", 0) or 0)
            duration = int(row["finished_at"]) - int(row["started_at"])
            if note_id <= 0:
                old_missing_durations.append(duration)
                continue
            old_rows_by_note[note_id] = (duration, int(row["finished_at"]))
        if len(old_rows_by_note) + len(old_missing_durations) != len(original_rows):
            self._reset_issue_spent_time(project_id, issue_iid, updated_rows)
            return

        new_rows_by_note: dict[int, tuple[int, str]] = {}
        new_missing_durations: list[int] = []
        for note_id, started_at, finished_at in updated_rows:
            note_key = int(note_id)
            duration = int(finished_at) - int(started_at)
            if note_key <= 0:
                new_missing_durations.append(duration)
                continue
            if note_key not in old_rows_by_note:
                self._reset_issue_spent_time(project_id, issue_iid, updated_rows)
                return
            spent_day = datetime.fromtimestamp(int(finished_at)).date().isoformat()
            new_rows_by_note[note_key] = (duration, spent_day)
        if len(new_rows_by_note) + len(new_missing_durations) != len(updated_rows):
            self._reset_issue_spent_time(project_id, issue_iid, updated_rows)
            return

        for note_id, old_row in old_rows_by_note.items():
            old_duration, _old_finished_at = old_row
            new_row = new_rows_by_note.get(note_id)
            if new_row is None:
                ops.append(("delete", note_id))
                continue
            new_duration, spent_day = new_row
            if new_duration == old_duration:
                continue
            if new_duration > old_duration:
                ops.append(("add_delta", new_duration - old_duration, spent_day))
            else:
                ops.append(("replace", note_id, new_duration, spent_day))

        if len(new_missing_durations) != len(old_missing_durations):
            self._reset_issue_spent_time(project_id, issue_iid, updated_rows)
            return
        for old_duration, new_duration in zip(old_missing_durations, new_missing_durations):
            if new_duration < old_duration:
                self._reset_issue_spent_time(project_id, issue_iid, updated_rows)
                return
            if new_duration > old_duration:
                ops.append(("add_delta", new_duration - old_duration, None))

        try:
            for op in ops:
                if op[0] == "delete":
                    _, note_id = op
                    self.delete_issue_note("issue", project_id, issue_iid, int(note_id))
                elif op[0] == "add_delta":
                    _, delta, spent_day = op
                    if spent_day is None:
                        self.add_spent_time("issue", project_id, issue_iid, int(delta))
                    else:
                        self.add_spent_time_for_day("issue", project_id, issue_iid, int(delta), str(spent_day))
                elif op[0] == "replace":
                    _, note_id, new_dur, spent_day = op
                    self.delete_issue_note("issue", project_id, issue_iid, int(note_id))
                    self.add_spent_time_for_day("issue", project_id, issue_iid, int(new_dur), str(spent_day))
        except requests.HTTPError as error:
            status_code = getattr(error.response, "status_code", None)
            if int(status_code or 0) == 403:
                self._reset_issue_spent_time(project_id, issue_iid, updated_rows)
                return
            raise

    def _reset_issue_spent_time(
        self,
        project_id: int,
        issue_iid: int,
        updated_rows: list[tuple[int, int, int]],
    ) -> None:
        """Fallback path when note identifiers are missing or changed."""
        self.reset_spent_time("issue", project_id, issue_iid)
        for _note_id, started, finished in sorted(updated_rows, key=lambda item: item[1]):
            spent_day = datetime.fromtimestamp(int(finished)).date().isoformat()
            self.add_spent_time_for_day("issue", project_id, issue_iid, finished - started, spent_day)
        return

    def get_issue_time_summary(self, project_id: int, issue_iid: int) -> dict[str, Any]:
        """Get total spent and estimate info for issue."""
        url = f"{self._base_url}/projects/{project_id}/issues/{issue_iid}"
        response = self._session.get(url, timeout=(5, 10))
        response.raise_for_status()
        payload = response.json()
        return payload.get("time_stats", {})

    def get_issue_time_logs(self, project_id: int, issue_iid: int) -> list[dict[str, Any]]:
        """Get raw timelog entries from GitLab issue time stats."""
        url = f"{self._base_url}/projects/{project_id}/issues/{issue_iid}/time_stats"
        response = self._session.get(url, timeout=(5, 10))
        response.raise_for_status()
        payload = response.json()
        timelogs = payload.get("timelogs", [])
        if isinstance(timelogs, list):
            return [entry for entry in timelogs if isinstance(entry, dict)]
        return []

    def get_spent_time_events(
        self,
        item_type: str,
        project_id: int,
        item_iid: int,
        since_date: date | None = None,
    ) -> list[dict[str, Any]]:
        """Parse system notes with spent time additions for issue or merge request."""
        pattern = re.compile(r"added\s+(.+?)\s+of time spent(?:\s+at\s+(\d{4}-\d{2}-\d{2}))?", re.IGNORECASE)
        reset_pattern = re.compile(r"removed\s+time\s+spent", re.IGNORECASE)
        events: list[dict[str, Any]] = []
        for note in self._fetch_notes(item_type, project_id, item_iid):
            if not bool(note.get("system", False)):
                continue
            body = str(note.get("body", ""))
            if reset_pattern.search(body):
                events.clear()
                continue
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
            spent_at_date = (match.group(2) or "").strip()
            finished_at_value = created_at
            if spent_at_date:
                # Keep a local-naive midday timestamp to avoid date shift by timezone conversion.
                finished_at_value = f"{spent_at_date}T12:00:00"
            if since_date is not None:
                created_dt = _parse_iso_datetime(finished_at_value)
                if created_dt is None:
                    continue
                if created_dt.date() < since_date:
                    continue
            note_id = int(note.get("id", 0) or 0)
            events.append(
                {
                    "finished_at": finished_at_value,
                    "duration_seconds": duration_seconds,
                    "note_id": note_id,
                }
            )
        return events

    def get_merge_request_closing_issue(self, project_id: int, mr_iid: int) -> tuple[int, int] | None:
        """Return (project_id, issue_iid) closed by merge request, if present."""
        url = f"{self._base_url}/projects/{project_id}/merge_requests/{mr_iid}/closes_issues"
        response = self._session.get(url, timeout=(5, 10))
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, list) or not payload:
            return None
        first = payload[0]
        if not isinstance(first, dict):
            return None
        issue_iid = int(first.get("iid", 0))
        target_project_id = int(first.get("project_id", project_id) or project_id)
        if issue_iid <= 0:
            return None
        return (target_project_id, issue_iid)

    def _fetch_updated_items(self, project_id: int, resource: str, since_iso: str) -> list[GitLabIssue]:
        url = f"{self._base_url}/projects/{project_id}/{resource}"
        page = 1
        items: list[GitLabIssue] = []
        item_type = "issue" if resource == "issues" else "merge_request"
        while True:
            params = {
                "scope": "all",
                "state": "all",
                "updated_after": since_iso,
                "per_page": 100,
                "page": page,
                "order_by": "updated_at",
                "sort": "desc",
            }
            response = self._session.get(url, params=params, timeout=(5, 10))
            response.raise_for_status()
            payload = response.json()
            for item in payload:
                assignee_ids = [int(user["id"]) for user in item.get("assignees", []) if isinstance(user, dict) and "id" in user]
                reviewer_ids = [int(user["id"]) for user in item.get("reviewers", []) if isinstance(user, dict) and "id" in user]
                items.append(
                    GitLabIssue(
                        project_id=project_id,
                        iid=int(item["iid"]),
                        title=str(item.get("title", "")),
                        web_url=str(item.get("web_url", "")),
                        labels=[label.strip() for label in item.get("labels", []) if isinstance(label, str) and label.strip()],
                        assignee_ids=assignee_ids,
                        reviewer_ids=reviewer_ids,
                        item_type=item_type,
                    )
                )
            next_page = response.headers.get("X-Next-Page", "").strip()
            if not next_page:
                break
            page = int(next_page)
        return items

    def _fetch_project_issues(
        self,
        project_id: int,
        extra_params: dict[str, Any],
        inferred_reviewer_id: int | None = None,
    ) -> list[GitLabIssue]:
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

            response = self._session.get(url, params=params, timeout=(5, 10))
            response.raise_for_status()

            payload = response.json()
            for item in payload:
                assignee_ids = [int(user["id"]) for user in item.get("assignees", []) if isinstance(user, dict) and "id" in user]
                reviewer_ids = [int(user["id"]) for user in item.get("reviewers", []) if isinstance(user, dict) and "id" in user]
                if inferred_reviewer_id is not None and inferred_reviewer_id not in reviewer_ids:
                    reviewer_ids.append(inferred_reviewer_id)
                issues.append(
                    GitLabIssue(
                        project_id=project_id,
                        iid=item["iid"],
                        title=item["title"],
                        web_url=item["web_url"],
                        labels=[label.strip() for label in item.get("labels", []) if label.strip()],
                        assignee_ids=assignee_ids,
                        reviewer_ids=reviewer_ids,
                        item_type="issue",
                    )
                )
            next_page = response.headers.get("X-Next-Page", "").strip()
            if not next_page:
                break
            page = int(next_page)
        return issues

    def _fetch_notes(self, item_type: str, project_id: int, item_iid: int) -> list[dict[str, Any]]:
        notes: list[dict[str, Any]] = []
        page = 1
        resource = "issues" if item_type == "issue" else "merge_requests"
        while True:
            url = f"{self._base_url}/projects/{project_id}/{resource}/{item_iid}/notes"
            response = self._session.get(
                url,
                params={"per_page": 100, "page": page, "order_by": "created_at", "sort": "asc"},
                timeout=(5, 10),
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


def _parse_iso_datetime(value: str) -> datetime | None:
    normalized = value.strip().replace("Z", "+00:00")
    if not normalized:
        return None
    try:
        return datetime.fromisoformat(normalized)
    except ValueError:
        return None


def _format_seconds_for_spend_command(seconds: int) -> str:
    """Format seconds to GitLab quick-action duration like '1h 2m 3s'."""
    total = max(1, int(seconds))
    hours = total // 3600
    minutes = (total % 3600) // 60
    secs = total % 60
    parts: list[str] = []
    if hours > 0:
        parts.append(f"{hours}h")
    if minutes > 0:
        parts.append(f"{minutes}m")
    if secs > 0 or not parts:
        parts.append(f"{secs}s")
    return " ".join(parts)


def issue_to_dict(issue: GitLabIssue) -> dict[str, Any]:
    """Convert dataclass to serializable dict for Qt item payload."""
    return asdict(issue)

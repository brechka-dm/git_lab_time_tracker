"""Background thread that flushes the offline event queue to GitLab."""

from __future__ import annotations

from PySide6.QtCore import QObject, QThread, Signal

from gitlab_client import GitLabClient
from models import GitLabIssue
from storage import AppStorage

from .network import is_retryable_network_error


class SyncThread(QThread):
    """Background thread that flushes the offline event queue to GitLab."""

    scan_needed = Signal()

    def __init__(
        self,
        client: GitLabClient,
        storage: AppStorage,
        events: list[dict],
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._client = client
        self._storage = storage
        self._events = events

    def run(self) -> None:
        processed_any = False
        for event in self._events:
            event_id = int(event["id"])
            event_type = str(event["event_type"])
            payload = event["payload"]
            try:
                if event_type == "add_spent_time":
                    self._client.add_spent_time(
                        str(payload.get("item_type", "issue")),
                        int(payload["project_id"]),
                        int(payload["iid"]),
                        int(payload["spent_seconds"]),
                    )
                elif event_type == "move_issue":
                    issue = GitLabIssue(
                        project_id=int(payload["project_id"]),
                        iid=int(payload["iid"]),
                        title=str(payload["title"]),
                        web_url=str(payload["web_url"]),
                        labels=[str(lbl) for lbl in payload.get("labels", [])],
                        assignee_ids=[int(v) for v in payload.get("assignee_ids", [])],
                        reviewer_ids=[int(v) for v in payload.get("reviewer_ids", [])],
                        item_type=str(payload.get("item_type", "issue")),
                    )
                    self._client.move_issue_to_label(
                        issue=issue,
                        target_label=str(payload["target_label"]),
                        current_column=str(payload["current_column"]),
                    )
                self._storage.delete_event(event_id)
                processed_any = True
            except Exception as error:  # noqa: BLE001
                if is_retryable_network_error(error):
                    break
                print(
                    f"[sync] dropped event #{event_id} ({event_type}): {error}",
                    flush=True,
                )
                self._storage.delete_event(event_id)
        if processed_any:
            self.scan_needed.emit()

"""Background thread that fetches open issues and review MRs from GitLab."""

from __future__ import annotations

import time

from PySide6.QtCore import QObject, QThread, Signal

from gitlab_client import GitLabClient


class RefreshThread(QThread):
    """Background thread that fetches open issues and review MRs from GitLab."""

    result = Signal(list, list)
    error = Signal(str)

    def __init__(self, client: GitLabClient, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._client = client

    def run(self) -> None:
        import sys  # noqa: PLC0415
        started = time.perf_counter()
        try:
            t0 = time.perf_counter()
            issues = self._client.fetch_open_issues()
            t1 = time.perf_counter()
            review_mrs = self._client.fetch_review_merge_requests()
            t2 = time.perf_counter()
            print(
                f"[refresh] open_issues={t1 - t0:.3f}s ({len(issues)}), "
                f"review_mrs={t2 - t1:.3f}s ({len(review_mrs)}), total={t2 - started:.3f}s",
                flush=True,
                file=sys.stderr,
            )
            self.result.emit(issues, review_mrs)
        except Exception as exc:  # noqa: BLE001
            print(f"[refresh] failed after {time.perf_counter() - started:.3f}s: {exc}", flush=True, file=sys.stderr)
            self.error.emit(str(exc))

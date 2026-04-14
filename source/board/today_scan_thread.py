"""Background thread that scans today's spent time across recently-updated items."""

from __future__ import annotations

import time
from datetime import date, datetime

from PySide6.QtCore import QObject, QThread, Signal

from gitlab_client import GitLabClient
from models import GitLabIssue


def spent_events_total_for_calendar_day(events: list, day: date) -> int:
    """Sum ``duration_seconds`` for events whose ``finished_at`` falls on ``day`` (same rules as today scan)."""
    total = 0
    for event in events:
        finished_str = event.get("finished_at")
        if not isinstance(finished_str, str):
            continue
        if int(event.get("duration_seconds", 0) or 0) <= 0:
            continue
        normalized = finished_str.strip().replace("Z", "+00:00")
        try:
            if datetime.fromisoformat(normalized).date() == day:
                total += int(event.get("duration_seconds", 0) or 0)
        except ValueError:
            continue
    return total


class TodayScanThread(QThread):
    """Background thread that scans today's spent time across all recently-updated items."""

    item_scanned = Signal(str, int, int, list, int, bool)

    def __init__(
        self,
        client: GitLabClient,
        today: date,
        cached: dict[str, dict[str, float]],
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._client = client
        self._today = today
        self._cached = cached

    def run(self) -> None:
        started = time.perf_counter()
        try:
            candidates = self._client.fetch_items_updated_since(self._today)
        except Exception:  # noqa: BLE001
            print(f"[today_scan] candidates load failed after {time.perf_counter() - started:.3f}s")
            self.finished.emit()
            return
        t_candidates = time.perf_counter()
        dedup: dict[tuple[str, int, int], GitLabIssue] = {}
        for item in candidates:
            dedup[(item.item_type, item.project_id, item.iid)] = item
        stale_items: list[GitLabIssue] = []
        now_ts = time.time()
        for item in dedup.values():
            cache_key = f"{item.item_type}:{item.project_id}:{item.iid}"
            cached_entry = self._cached.get(cache_key)
            if cached_entry is None:
                stale_items.append(item)
                continue
            cached_ts = float(cached_entry.get("ts", 0.0))
            cached_total = int(cached_entry.get("total", 0) or 0)
            if now_ts - cached_ts <= 300:
                self.item_scanned.emit(item.item_type, item.project_id, item.iid, [], cached_total, True)
            else:
                stale_items.append(item)

        for item in stale_items:
            result = self._fetch_item_total(item)
            if result is not None:
                self.item_scanned.emit(*result, False)
        import sys  # noqa: PLC0415
        print(
            f"[today_scan] candidates={len(candidates)} dedup={len(dedup)} stale={len(stale_items)} "
            f"load_candidates={t_candidates - started:.3f}s total={time.perf_counter() - started:.3f}s",
            flush=True,
            file=sys.stderr,
        )

    def _fetch_item_total(self, item: GitLabIssue) -> tuple[str, int, int, list, int] | None:
        """Fetch spent events for one item and compute today's total seconds."""
        try:
            events = self._client.get_spent_time_events(
                item.item_type, item.project_id, item.iid, since_date=self._today
            )
            item_total = spent_events_total_for_calendar_day(events, self._today)
            return (item.item_type, item.project_id, item.iid, events, item_total)
        except Exception:  # noqa: BLE001
            return None

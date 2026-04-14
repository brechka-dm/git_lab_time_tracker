"""SQLite storage for UI state and offline events."""

from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path


def _clean_board_column_labels(values: list) -> list[str]:
    """Normalize label list: strip, dedupe preserving order, always include open first."""
    labels = (item.strip() for item in values if isinstance(item, str))
    unique = [label for label in dict.fromkeys(labels) if label]
    return ["open", *[label for label in unique if label != "open"]]


class AppStorage:
    """Persist board and tracking state in SQLite."""

    def __init__(self, db_path: Path) -> None:
        self._db_path = db_path
        self._init_db()

    def load_boards(self) -> tuple[dict[str, list[str]], str | None]:
        raw_boards = self._get_value("boards")
        raw_selected = self._get_value("selected_board")
        payload: dict = {}
        if raw_boards:
            try:
                loaded = json.loads(raw_boards)
                payload = loaded if isinstance(loaded, dict) else {}
            except Exception:  # noqa: BLE001
                payload = {}

        boards = {
            board_name: _clean_board_column_labels(labels)
            for board_name, labels in payload.items()
            if isinstance(board_name, str) and isinstance(labels, list)
        }
        selected = raw_selected.strip() if raw_selected else None
        return boards, selected

    def save_boards(self, boards: dict[str, list[str]], selected_board: str) -> None:
        self._set_value("boards", json.dumps(boards, ensure_ascii=True))
        self._set_value("selected_board", selected_board)

    def load_tracking(
        self,
    ) -> tuple[int | None, str, float | None, int | None, str, int | None, int | None, str, bool, int | None]:
        iid_raw = self._get_value("active_issue_iid")
        title = self._get_value("active_issue_title") or ""
        started_raw = self._get_value("active_started_at")
        project_raw = self._get_value("active_issue_project_id")
        item_type = self._get_value("active_item_type") or "issue"
        target_project_raw = self._get_value("active_target_project_id")
        target_iid_raw = self._get_value("active_target_iid")
        target_type = self._get_value("active_target_type") or "issue"
        is_local_raw = self._get_value("active_is_local")
        local_task_id_raw = self._get_value("active_local_task_id")

        issue_iid: int | None = None
        started_at: float | None = None
        issue_project_id: int | None = None
        target_project_id: int | None = None
        target_iid: int | None = None
        local_task_id: int | None = None
        active_is_local = bool(is_local_raw == "1")
        if iid_raw:
            try:
                value = int(iid_raw)
                if value > 0:
                    issue_iid = value
            except ValueError:
                pass
        if started_raw:
            try:
                started_at = float(started_raw)
            except ValueError:
                pass
        if project_raw:
            try:
                issue_project_id = int(project_raw)
            except ValueError:
                pass
        if target_project_raw:
            try:
                target_project_id = int(target_project_raw)
            except ValueError:
                pass
        if target_iid_raw:
            try:
                target_iid = int(target_iid_raw)
            except ValueError:
                pass
        if local_task_id_raw:
            try:
                local_task_id = int(local_task_id_raw)
            except ValueError:
                pass
        return (
            issue_iid,
            title,
            started_at,
            issue_project_id,
            item_type,
            target_project_id,
            target_iid,
            target_type,
            active_is_local,
            local_task_id,
        )

    def save_tracking(
        self,
        issue_iid: int | None,
        title: str,
        started_at: float | None,
        issue_project_id: int | None,
        item_type: str,
        target_project_id: int | None,
        target_iid: int | None,
        target_type: str,
        active_is_local: bool = False,
        active_local_task_id: int | None = None,
    ) -> None:
        self._set_value("active_issue_iid", "" if issue_iid is None else str(issue_iid))
        self._set_value("active_issue_title", title)
        self._set_value("active_started_at", "" if started_at is None else str(started_at))
        self._set_value("active_issue_project_id", "" if issue_project_id is None else str(issue_project_id))
        self._set_value("active_item_type", item_type)
        self._set_value("active_target_project_id", "" if target_project_id is None else str(target_project_id))
        self._set_value("active_target_iid", "" if target_iid is None else str(target_iid))
        self._set_value("active_target_type", target_type)
        self._set_value("active_is_local", "1" if active_is_local else "0")
        self._set_value("active_local_task_id", "" if active_local_task_id is None else str(active_local_task_id))

    def load_local_tasks(self) -> list[dict]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT id, board_name, column_label, title, created_at
                FROM local_tasks
                ORDER BY id ASC
                """
            ).fetchall()
        tasks: list[dict] = []
        for row in rows:
            tasks.append(
                {
                    "task_id": int(row[0]),
                    "board_name": str(row[1]),
                    "column_label": str(row[2]),
                    "title": str(row[3]),
                    "created_at": float(row[4]),
                }
            )
        return tasks

    def create_local_task(self, board_name: str, column_label: str, title: str) -> dict:
        now = time.time()
        with self._connect() as connection:
            cursor = connection.execute(
                """
                INSERT INTO local_tasks(board_name, column_label, title, created_at)
                VALUES (?, ?, ?, ?)
                """,
                (board_name, column_label, title, now),
            )
            connection.commit()
            task_id = int(cursor.lastrowid)
        return {
            "task_id": task_id,
            "board_name": board_name,
            "column_label": column_label,
            "title": title,
            "created_at": now,
        }

    def move_local_task(self, task_id: int, board_name: str, column_label: str) -> None:
        with self._connect() as connection:
            connection.execute(
                "UPDATE local_tasks SET board_name = ?, column_label = ? WHERE id = ?",
                (board_name, column_label, task_id),
            )
            connection.commit()

    def add_local_time_event(self, task_id: int, started_at: float, finished_at: float, duration_seconds: int) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO local_time_events(local_task_id, started_at, finished_at, duration_seconds, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (task_id, started_at, finished_at, duration_seconds, time.time()),
            )
            connection.commit()

    def load_local_time_events(self, task_id: int | None = None) -> list[dict]:
        with self._connect() as connection:
            if task_id is None:
                rows = connection.execute(
                    """
                    SELECT local_task_id, started_at, finished_at, duration_seconds
                    FROM local_time_events
                    ORDER BY finished_at ASC
                    """
                ).fetchall()
            else:
                rows = connection.execute(
                    """
                    SELECT local_task_id, started_at, finished_at, duration_seconds
                    FROM local_time_events
                    WHERE local_task_id = ?
                    ORDER BY finished_at ASC
                    """,
                    (task_id,),
                ).fetchall()
        events: list[dict] = []
        for row in rows:
            events.append(
                {
                    "task_id": int(row[0]),
                    "started_at": float(row[1]),
                    "finished_at": float(row[2]),
                    "duration_seconds": int(row[3]),
                }
            )
        return events

    def load_issue_orders(self) -> dict[str, list[str]]:
        raw = self._get_value("issue_orders")
        if not raw:
            return {}
        try:
            payload = json.loads(raw)
        except Exception:  # noqa: BLE001
            return {}
        if not isinstance(payload, dict):
            return {}
        orders: dict[str, list[str]] = {}
        for key, values in payload.items():
            if not isinstance(key, str) or not isinstance(values, list):
                continue
            parsed = [str(value) for value in values if isinstance(value, str) and value.strip()]
            orders[key] = parsed
        return orders

    def save_issue_orders(self, orders: dict[str, list[str]]) -> None:
        self._set_value("issue_orders", json.dumps(orders, ensure_ascii=True))

    def load_highlighted_cards(self) -> set[str]:
        """Load highlighted card keys."""
        raw = self._get_value("highlighted_cards")
        if not raw:
            return set()
        try:
            payload = json.loads(raw)
        except Exception:  # noqa: BLE001
            return set()
        if not isinstance(payload, list):
            return set()
        return {str(value) for value in payload if isinstance(value, str) and value.strip()}

    def save_highlighted_cards(self, keys: set[str]) -> None:
        """Persist highlighted card keys."""
        payload = sorted(key for key in keys if isinstance(key, str) and key.strip())
        self._set_value("highlighted_cards", json.dumps(payload, ensure_ascii=True))

    def load_today_scan_cache(self, day: str) -> dict[str, dict[str, float]]:
        """Load cached per-item totals for a date."""
        raw = self._get_value(f"today_scan_cache:{day}")
        if not raw:
            return {}
        try:
            payload = json.loads(raw)
        except Exception:  # noqa: BLE001
            return {}
        if not isinstance(payload, dict):
            return {}
        cleaned: dict[str, dict[str, float]] = {}
        for key, value in payload.items():
            if not isinstance(key, str) or not isinstance(value, dict):
                continue
            total = value.get("total", 0)
            ts = value.get("ts", 0.0)
            try:
                total_int = int(total)
                ts_float = float(ts)
            except Exception:  # noqa: BLE001
                continue
            if total_int < 0:
                continue
            cleaned[key] = {"total": float(total_int), "ts": ts_float}
        return cleaned

    def save_today_scan_cache(self, day: str, cache: dict[str, dict[str, float]]) -> None:
        """Persist per-item totals cache for a date."""
        self._set_value(f"today_scan_cache:{day}", json.dumps(cache, ensure_ascii=True))

    def save_issues_cache(self, issues: list[dict], review_mrs: list[dict]) -> None:
        """Persist fetched issues and review MRs for instant startup."""
        payload = {"issues": issues, "review_mrs": review_mrs, "ts": time.time()}
        self._set_value("issues_cache", json.dumps(payload, ensure_ascii=True))

    def load_issues_cache(self) -> tuple[list[dict], list[dict]] | None:
        """Return cached (issues, review_mrs) dicts, or None if no cache exists."""
        raw = self._get_value("issues_cache")
        if not raw:
            return None
        try:
            payload = json.loads(raw)
        except Exception:  # noqa: BLE001
            return None
        if not isinstance(payload, dict):
            return None
        issues = payload.get("issues")
        review_mrs = payload.get("review_mrs")
        if not isinstance(issues, list) or not isinstance(review_mrs, list):
            return None
        return issues, review_mrs

    def enqueue_event(self, event_type: str, payload: dict) -> None:
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO event_queue(event_type, payload, created_at) VALUES (?, ?, ?)",
                (event_type, json.dumps(payload, ensure_ascii=True), time.time()),
            )
            connection.commit()

    def load_events(self, limit: int = 200) -> list[dict]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT id, event_type, payload FROM event_queue ORDER BY id ASC LIMIT ?",
                (limit,),
            ).fetchall()
        events: list[dict] = []
        for row in rows:
            try:
                payload = json.loads(str(row[2]))
            except Exception:  # noqa: BLE001
                payload = {}
            if isinstance(payload, dict):
                events.append({"id": int(row[0]), "event_type": str(row[1]), "payload": payload})
        return events

    def delete_event(self, event_id: int) -> None:
        with self._connect() as connection:
            connection.execute("DELETE FROM event_queue WHERE id = ?", (event_id,))
            connection.commit()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self._db_path)
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    def _init_db(self) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS app_state (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS event_queue (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    event_type TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    created_at REAL NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS local_tasks (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    board_name TEXT NOT NULL,
                    column_label TEXT NOT NULL,
                    title TEXT NOT NULL,
                    created_at REAL NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS local_time_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    local_task_id INTEGER NOT NULL,
                    started_at REAL NOT NULL,
                    finished_at REAL NOT NULL,
                    duration_seconds INTEGER NOT NULL,
                    created_at REAL NOT NULL,
                    FOREIGN KEY(local_task_id) REFERENCES local_tasks(id) ON DELETE CASCADE
                )
                """
            )
            connection.commit()

    def _get_value(self, key: str) -> str | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT value FROM app_state WHERE key = ?",
                (key,),
            ).fetchone()
        return None if row is None else str(row[0])

    def _set_value(self, key: str, value: str) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO app_state(key, value) VALUES (?, ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value
                """,
                (key, value),
            )
            connection.commit()

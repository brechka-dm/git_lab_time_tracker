"""SQLite storage for UI state and offline events."""

from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path


class AppStorage:
    """Persist board and tracking state in SQLite."""

    def __init__(self, db_path: Path) -> None:
        self._db_path = db_path
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self._db_path)

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

    def load_boards(self) -> tuple[dict[str, list[str]], str | None]:
        raw_boards = self._get_value("boards")
        raw_selected = self._get_value("selected_board")
        boards: dict[str, list[str]] = {}
        if raw_boards:
            try:
                payload = json.loads(raw_boards)
            except Exception:  # noqa: BLE001
                payload = {}
            if isinstance(payload, dict):
                for board_name, labels in payload.items():
                    if not isinstance(board_name, str) or not isinstance(labels, list):
                        continue
                    cleaned = ["open"]
                    for item in labels:
                        if isinstance(item, str):
                            label = item.strip()
                            if label and label not in cleaned:
                                cleaned.append(label)
                    boards[board_name] = cleaned
        selected = raw_selected.strip() if raw_selected else None
        return boards, selected

    def save_boards(self, boards: dict[str, list[str]], selected_board: str) -> None:
        self._set_value("boards", json.dumps(boards, ensure_ascii=True))
        self._set_value("selected_board", selected_board)

    def load_tracking(self) -> tuple[int | None, str, float | None]:
        iid_raw = self._get_value("active_issue_iid")
        title = self._get_value("active_issue_title") or ""
        started_raw = self._get_value("active_started_at")

        issue_iid: int | None = None
        started_at: float | None = None
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
        return issue_iid, title, started_at

    def save_tracking(self, issue_iid: int | None, title: str, started_at: float | None) -> None:
        self._set_value("active_issue_iid", "" if issue_iid is None else str(issue_iid))
        self._set_value("active_issue_title", title)
        self._set_value("active_started_at", "" if started_at is None else str(started_at))

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

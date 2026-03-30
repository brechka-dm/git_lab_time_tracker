"""Helpers for time parsing and formatting."""

from __future__ import annotations

from datetime import datetime


def parse_iso_datetime(value: object) -> float | None:
    """Parse ISO datetime value and return UNIX timestamp in seconds."""
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text:
        return None
    normalized = text.replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(normalized).timestamp()
    except ValueError:
        return None


def format_duration(seconds: int) -> str:
    """Format seconds as HH:MM:SS."""
    value = max(0, int(seconds))
    hours = value // 3600
    minutes = (value % 3600) // 60
    secs = value % 60
    return f"{hours:02d}:{minutes:02d}:{secs:02d}"

"""Board label parsing and display helpers."""

from __future__ import annotations


def parse_label(label: str) -> tuple[str, str]:
    """Split board label into (category, column_name)."""
    text = label.strip()
    if ":" in text:
        category, column_name = text.split(":", 1)
        category = category.strip() or "Other"
        return category, (column_name.strip() or text)
    return "Other", text


def display_label(raw_label: str, current_board: str) -> str:
    """Render compact label text for the current board."""
    if raw_label == "open":
        return "open"
    category, column_name = parse_label(raw_label)
    if category == current_board and column_name:
        return column_name
    return raw_label

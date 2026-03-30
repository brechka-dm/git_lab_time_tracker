"""Qt dialogs and flows for report generation."""

from __future__ import annotations

from datetime import date, datetime
from pathlib import Path
from typing import Callable

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QApplication,
    QCalendarWidget,
    QDateEdit,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QProgressDialog,
    QVBoxLayout,
    QWidget,
)

from models import GitLabIssue

from .project_tags import PROJECT_TAGS
from .report_builder import (
    collect_daily_totals,
    collect_period_totals,
    render_daily_report_text,
    render_period_report_text,
)

LoadEventsFn = Callable[[str, int, int], list[dict]]
LocalDayTotalsFn = Callable[[date], list[tuple[int, str, int]]]
LocalPeriodTotalsFn = Callable[[date, date], list[tuple[int, str, int]]]


def run_daily_report(
    parent: QWidget,
    report_items: list[GitLabIssue],
    spent_events_cache: dict[tuple[str, int, int], list[dict]],
    load_events: LoadEventsFn,
    load_local_totals_for_day: LocalDayTotalsFn,
) -> None:
    """Run daily report dialog and save generated report file."""
    dialog = QDialog(parent)
    dialog.setWindowTitle("Daily Report Date")
    dialog_layout = QVBoxLayout(dialog)
    calendar = QCalendarWidget(dialog)
    calendar.setGridVisible(True)
    dialog_layout.addWidget(calendar)
    buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
    buttons.accepted.connect(dialog.accept)
    buttons.rejected.connect(dialog.reject)
    dialog_layout.addWidget(buttons)
    if dialog.exec() != QDialog.DialogCode.Accepted:
        return
    target_qdate = calendar.selectedDate()
    target_date = datetime(target_qdate.year(), target_qdate.month(), target_qdate.day()).date()

    progress = QProgressDialog("Building daily report...", "Cancel", 0, len(report_items), parent)
    progress.setWindowTitle("Daily Report")
    progress.setWindowModality(Qt.WindowModality.WindowModal)
    progress.setMinimumDuration(0)
    gitlab_failures: list[str] = []
    issue_totals: dict[tuple[str, int, int], int] = {}
    for index, issue in enumerate(report_items, start=1):
        progress.setLabelText(f"Loading events for #{issue.iid} ({index}/{len(report_items)})...")
        progress.setValue(index - 1)
        QApplication.processEvents()
        if progress.wasCanceled():
            return

        total_seconds, error = collect_daily_totals(target_date, issue, spent_events_cache, load_events)
        if error is not None:
            gitlab_failures.append(error)
            progress.setValue(index)
            QApplication.processEvents()
            continue
        key = (issue.item_type, issue.project_id, issue.iid)
        issue_totals[key] = issue_totals.get(key, 0) + total_seconds
        progress.setValue(index)
        QApplication.processEvents()
    progress.close()

    non_zero_items = [(key, seconds) for key, seconds in issue_totals.items() if seconds > 0]
    non_zero_items.sort(key=lambda item: item[1], reverse=True)
    local_items = load_local_totals_for_day(target_date)
    report_text = render_daily_report_text(
        target_date,
        non_zero_items,
        local_items,
        PROJECT_TAGS,
        gitlab_failures,
    )
    reports_dir = Path(__file__).resolve().parents[2] / "reports"
    reports_dir.mkdir(exist_ok=True)
    report_path = reports_dir / f"daily_report_{target_date.isoformat()}.txt"
    report_path.write_text(report_text, encoding="utf-8")
    msg = f"Report saved to:\n{report_path}"
    if gitlab_failures:
        QMessageBox.warning(
            parent,
            "Daily Report",
            f"{msg}\n\nSome GitLab items could not be loaded ({len(gitlab_failures)}). "
            "Local totals are included; see report file for details.",
        )
    else:
        QMessageBox.information(parent, "Daily Report", msg)


def run_period_report(
    parent: QWidget,
    report_items: list[GitLabIssue],
    spent_events_cache: dict[tuple[str, int, int], list[dict]],
    load_events: LoadEventsFn,
    load_local_totals_for_period: LocalPeriodTotalsFn,
) -> None:
    """Run period report dialog and save generated report file."""
    dialog = QDialog(parent)
    dialog.setWindowTitle("Period Report")
    layout = QVBoxLayout(dialog)

    from_row = QHBoxLayout()
    from_row.addWidget(QLabel("From:"))
    from_date = QDateEdit(dialog)
    from_date.setCalendarPopup(True)
    from_date.setDate(datetime.now().date())
    from_row.addWidget(from_date)
    layout.addLayout(from_row)

    to_row = QHBoxLayout()
    to_row.addWidget(QLabel("To:"))
    to_date = QDateEdit(dialog)
    to_date.setCalendarPopup(True)
    to_date.setDate(datetime.now().date())
    to_row.addWidget(to_date)
    layout.addLayout(to_row)

    buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
    buttons.accepted.connect(dialog.accept)
    buttons.rejected.connect(dialog.reject)
    layout.addWidget(buttons)
    if dialog.exec() != QDialog.DialogCode.Accepted:
        return

    start_qdate = from_date.date()
    end_qdate = to_date.date()
    start_date = datetime(start_qdate.year(), start_qdate.month(), start_qdate.day()).date()
    end_date = datetime(end_qdate.year(), end_qdate.month(), end_qdate.day()).date()
    if start_date > end_date:
        QMessageBox.warning(parent, "Period Report", "'From' date must be before or equal to 'To' date.")
        return

    progress = QProgressDialog("Building period report...", "Cancel", 0, len(report_items), parent)
    progress.setWindowTitle("Period Report")
    progress.setWindowModality(Qt.WindowModality.WindowModal)
    progress.setMinimumDuration(0)
    gitlab_failures: list[str] = []
    grouped: dict[str, dict[str, int]] = {}
    for index, issue in enumerate(report_items, start=1):
        progress.setLabelText(f"Loading events for #{issue.iid} ({index}/{len(report_items)})...")
        progress.setValue(index - 1)
        QApplication.processEvents()
        if progress.wasCanceled():
            return

        total_seconds, error = collect_period_totals(
            start_date,
            end_date,
            issue,
            spent_events_cache,
            load_events,
        )
        if error is not None:
            gitlab_failures.append(error)
            progress.setValue(index)
            QApplication.processEvents()
            continue
        project_tag = PROJECT_TAGS.get(issue.project_id, f"P{issue.project_id}")
        per_project = grouped.setdefault(project_tag, {})
        per_project.setdefault(issue.title, 0)
        per_project[issue.title] += total_seconds
        progress.setValue(index)
        QApplication.processEvents()
    progress.close()

    local_items = load_local_totals_for_period(start_date, end_date)
    report_text = render_period_report_text(
        start_date,
        end_date,
        grouped,
        local_items,
        gitlab_failures,
    )
    reports_dir = Path(__file__).resolve().parents[2] / "reports"
    reports_dir.mkdir(exist_ok=True)
    report_path = reports_dir / f"period_report_{start_date.isoformat()}_{end_date.isoformat()}.txt"
    report_path.write_text(report_text, encoding="utf-8")
    msg = f"Report saved to:\n{report_path}"
    if gitlab_failures:
        QMessageBox.warning(
            parent,
            "Period Report",
            f"{msg}\n\nSome GitLab items could not be loaded ({len(gitlab_failures)}). "
            "Local totals are included; see report file for details.",
        )
    else:
        QMessageBox.information(parent, "Period Report", msg)

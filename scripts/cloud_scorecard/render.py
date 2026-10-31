"""Render check reports as Markdown: one table per group, then totals."""
from __future__ import annotations

from collections import Counter
from typing import Sequence

from scripts.cloud_scorecard.model import CheckReport, Status

TABLE_HEADER = ("| Status | Check | Detail |", "|---|---|---|")


def _escape_cell(text: str) -> str:
    return text.replace("|", "\\|").replace("\n", " ")


def _row(report: CheckReport) -> str:
    check = report.check
    label = f"`{check.identifier}` {_escape_cell(check.description)}"
    return f"| {report.outcome.status.value} | {label} | {_escape_cell(report.outcome.detail)} |"


def _totals(reports: Sequence[CheckReport]) -> str:
    counts = Counter(report.outcome.status for report in reports)
    return " · ".join(f"{status.value} {counts[status]}" for status in Status)


def render_group(group: str, reports: Sequence[CheckReport]) -> str:
    rows = [_row(report) for report in reports if report.check.group == group]
    return "\n".join([f"### {group}", "", *TABLE_HEADER, *rows])


def render_markdown(
    reports: Sequence[CheckReport], groups: Sequence[str], commit: str, revision: str,
) -> str:
    sections = [
        "## Forseti cloud scorecard",
        f"Commit `{commit}` · Cloud Run revision `{revision}`",
        _totals(reports),
        *(render_group(group, reports) for group in groups),
    ]
    return "\n\n".join(sections) + "\n"

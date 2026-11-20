"""The figure and the tables together, as one HTML fragment to drop into a page."""

from __future__ import annotations

from html import escape

from taskviz.cohort import Counts
from taskviz.figure import render_svg
from taskviz.tables import predicates_table, windows_table
from taskviz.task import Task


def render_html(
    task: Task,
    counts: Counts | list[Counts] | None = None,
    theme: str = "auto",
    title: str | None = None,
    min_count: int = 1,
) -> str:
    """A ``<figure>`` with the timeline, then the windows, predicates and (if given) cohort tables.

    The fragment has no styles of its own beyond the SVG's, so the tables take the page's table styling.
    ``counts`` may be one :class:`Counts` or one per dataset.
    """
    parts = [f'<figure class="taskviz-figure">{render_svg(task, theme=theme, title=title)}</figure>']
    parts.append(f"<h4>Windows</h4>\n{windows_table(task).to_html()}")
    if task.predicates:
        parts.append(f"<h4>Predicates</h4>\n{predicates_table(task).to_html()}")
    for item in [counts] if isinstance(counts, Counts) else (counts or []):
        heading = f"Cohort on {item.dataset}" if item.dataset else "Cohort"
        parts.append(f"<h4>{escape(heading)}</h4>\n{item.table(min_count).to_html()}")
    return '<div class="taskviz">\n' + "\n".join(parts) + "</div>\n"

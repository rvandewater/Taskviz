"""The task in tables: its windows, its predicates and (given a cohort) its numbers.

Every table is a :class:`Table` of plain strings, rendered as GitHub-flavoured Markdown, as an HTML
``<table>``, or kept as rows for a site generator to lay out itself.
"""

from __future__ import annotations

from dataclasses import dataclass
from html import escape

from taskviz.task import Task


@dataclass
class Table:
    columns: list[str]
    rows: list[list[str]]
    numeric: tuple[int, ...] = ()  # columns to right-align

    def to_markdown(self) -> str:
        def line(cells):
            return "| " + " | ".join(str(cell).replace("|", "\\|") for cell in cells) + " |"

        rule = ["---:" if i in self.numeric else "---" for i in range(len(self.columns))]
        return "\n".join([line(self.columns), line(rule), *map(line, self.rows)]) + "\n"

    def to_html(self, class_name: str = "taskviz-table") -> str:
        def cells(tag, values):
            right = ' style="text-align:right"'
            return "".join(
                f"<{tag}{right if i in self.numeric else ''}>{escape(str(v))}</{tag}>"
                for i, v in enumerate(values)
            )

        body = "".join(f"<tr>{cells('td', row)}</tr>" for row in self.rows)
        return (
            f'<table class="{escape(class_name)}"><thead><tr>{cells("th", self.columns)}</tr></thead>'
            f"<tbody>{body}</tbody></table>\n"
        )

    def to_records(self) -> list[dict[str, str]]:
        return [dict(zip(self.columns, row)) for row in self.rows]


def windows_table(task: Task) -> Table:
    """One row per window: where it starts and ends, what it requires, and its role.

    >>> from taskviz import read_task
    >>> task = read_task({"trigger": "adm", "windows": {
    ...     "input": {"start": None, "end": "trigger + 24h", "index_timestamp": "end"},
    ...     "target": {"start": "input.end", "end": "start -> death", "label": "death",
    ...                "start_inclusive": False}}})
    >>> print(windows_table(task).to_markdown())
    | Window | From | To | Requires | Role |
    | --- | --- | --- | --- | --- |
    | input | record start | trigger + 1d (inclusive) |  | prediction time at its end |
    | target | trigger + 1d (exclusive) | next death (inclusive) |  | label: death |
    <BLANKLINE>
    """
    rows = []
    for window in task.windows:
        roles = []
        if window.index:
            roles.append(f"prediction time at its {window.index}")
        if window.label:
            roles.append(f"label: {window.label}")

        def side(which, inclusive):
            if which.kind == "record":
                return which.text
            return f"{which.text} ({'inclusive' if inclusive else 'exclusive'})"

        rows.append(
            [
                window.name,
                side(window.start, window.start_inclusive),
                side(window.end, window.end_inclusive),
                "; ".join(window.requirements),
                "; ".join(roles),
            ]
        )
    return Table(["Window", "From", "To", "Requires", "Role"], rows)


def predicates_table(task: Task) -> Table:
    """One row per predicate: what it matches and what the task uses it for."""
    rows = []
    for predicate in task.predicates:
        roles = list(predicate.roles) + (["demographic"] if predicate.demographic else [])
        rows.append([predicate.name, predicate.definition, ", ".join(roles)])
    return Table(["Predicate", "Matches", "Role"], rows)

"""Draw a task's windows on a time axis around the trigger, as a standalone SVG.

Each side of a window is fixed (hours from the trigger), the record's start or end (the bar fades out at the
edge), or the next or previous occurrence of an event (a dashed arrow to the edge, named). The label window
has its own colour and the prediction time is marked with a diamond.

The SVG carries its own styles and no scripts, so it works as an ``<img>``, inlined in a page, or in a
Markdown file. Every element has a ``tv-*`` class, so a page that inlines it can restyle it.
"""

from __future__ import annotations

import hashlib
import math
from xml.sax.saxutils import escape, quoteattr

from taskviz.task import Task, Window

THEMES = {
    "light": {
        "bg": "#eef2f7",
        "text": "#13233a",
        "muted": "#5b6b80",
        "line": "#dbe2ec",
        "accent": "#0f6e8c",
        "bar": "#3d8fb0",
        "label": "#d9822b",
        "index": "#1d7a4f",
    },
    "dark": {
        "bg": "#1d2734",
        "text": "#e6ebf2",
        "muted": "#98a6b9",
        "line": "#2e3b4f",
        "accent": "#5cc1dd",
        "bar": "#5cc1dd",
        "label": "#e8a15a",
        "index": "#5cc48f",
    },
}

_STEPS = [1, 2, 3, 6, 12, 24, 48, 72, 168, 336, 720, 2160, 4380, 8760]
_EPS = 1e-9
_FONT = "system-ui, -apple-system, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif"
_MONO = "ui-monospace, SFMono-Regular, Menlo, Consolas, monospace"


def hours_label(hours: float, step: float = 1) -> str:
    """An axis tick, in hours, days or years from the trigger.

    >>> hours_label(0), hours_label(24, 24), hours_label(-6), hours_label(-157776), hours_label(36, 12)
    ('0', '+1d', '−6h', '−18y', '+36h')
    """
    if abs(hours) < _EPS:
        return "0"
    sign = "+" if hours > 0 else "−"
    value = abs(hours)
    if value >= 8760:
        years = value / 8760
        return f"{sign}{round(years)}y" if abs(years - round(years)) < 0.02 else f"{sign}{years:.1f}y"
    if value % 24 == 0 and (step % 24 == 0 or value >= 24 * 14):
        return f"{sign}{value / 24:g}d"
    return f"{sign}{round(value, 1):g}h"


def _rules(colors: dict, scope: str) -> str:
    c = colors
    return (
        f"{scope} .tv-bg{{fill:{c['bg']}}}"
        f"{scope} text{{fill:{c['muted']}}}"
        f"{scope} .tv-name,{scope} .tv-title{{fill:{c['text']}}}"
        f"{scope} .tv-trigger-label{{fill:{c['accent']}}}"
        f"{scope} .tv-label{{fill:{c['label']}}}"
        f"{scope} .tv-index-label{{fill:{c['index']}}}"
        f"{scope} .tv-axis,{scope} .tv-break-mark{{stroke:{c['muted']}}}"
        f"{scope} .tv-grid{{stroke:{c['line']}}}"
        f"{scope} .tv-trigger{{stroke:{c['accent']}}}"
        f"{scope} .tv-anchor{{stroke:{c['bar']}}}"
        f"{scope} .tv-anchor-label{{fill:{c['bar']}}}"
        f"{scope} .tv-bar,{scope} .tv-arrow-head{{fill:{c['bar']}}}"
        f"{scope} .tv-bar.tv-is-label{{fill:{c['label']}}}"
        f"{scope} .tv-stop{{stop-color:{c['bar']}}}"
        f"{scope} .tv-stop.tv-is-label{{stop-color:{c['label']}}}"
        f"{scope} .tv-open{{stroke:{c['bar']}}}"
        f"{scope} .tv-index{{fill:{c['index']}}}"
        f"{scope} .tv-break{{fill:{c['bg']}}}"
    )


def _style(theme: str, scope: str) -> str:
    base = (
        f"{scope} text{{font-family:{_FONT};font-size:12px}}"
        f"{scope} .tv-name{{font-family:{_MONO};font-size:13px;font-weight:600}}"
        f"{scope} .tv-title{{font-size:14px;font-weight:700}}"
        f"{scope} .tv-trigger-label{{font-weight:700}}"
        f"{scope} .tv-label,{scope} .tv-index-label{{font-size:11px;font-weight:600}}"
        f"{scope} .tv-note{{font-size:11px;font-style:italic}}"
        f"{scope} .tv-axis,{scope} .tv-grid{{stroke-width:1}}"
        f"{scope} .tv-trigger{{stroke-width:2;stroke-dasharray:4 3}}"
        f"{scope} .tv-anchor{{stroke-width:1.5;stroke-dasharray:2 3}}"
        f"{scope} .tv-anchor-label{{font-weight:600}}"
        f"{scope} .tv-open{{stroke-width:2.5;stroke-dasharray:6 4;fill:none}}"
        f"{scope} .tv-break-mark{{stroke-width:1.5;fill:none}}"
    )
    if theme == "auto":
        dark = _rules(THEMES["dark"], scope)
        return base + _rules(THEMES["light"], scope) + f"@media (prefers-color-scheme:dark){{{dark}}}"
    return base + _rules(THEMES[theme], scope)


class _Svg:
    def __init__(self):
        self.parts: list[str] = []
        self.late: list[str] = []  # the windows' texts, drawn over the axis breaks

    def add(self, tag: str, text: str | None = None, late: bool = False, **attrs) -> None:
        (self.late if late else self.parts).append(_element(tag, text, **attrs))


def _element(tag: str, text: str | None = None, **attrs) -> str:
    rendered = "".join(
        f" {key.rstrip('_').replace('_', '-')}={quoteattr(_number(value))}"
        for key, value in attrs.items()
        if value is not None
    )
    if text is None:
        return f"<{tag}{rendered}/>"
    return f"<{tag}{rendered}>{escape(text)}</{tag}>"


def _number(value) -> str:
    return f"{value:.1f}".rstrip("0").rstrip(".") if isinstance(value, float) else str(value)


def can_draw(task: Task) -> bool:
    """Whether every window side is one the figure knows how to place."""
    return bool(task.windows) and all(
        side.kind != "unknown" for window in task.windows for side in (window.start, window.end)
    )


class _Segment:
    """One stretch of the axis: the hours around one origin (the trigger, or an event windows count from).

    Offsets far beyond the segment's own timescale (an 18-year look-back used as an age filter, next to
    24-hour windows) go to a compressed part at its edge, behind a break, so the windows near the origin
    keep their width. "Far" is more than ten times the median distance from the origin (and more than three
    days).
    """

    def __init__(self, origin: str, values: list[float], alone: bool = True):
        self.origin = origin
        self.alone = alone  # the only segment of the axis
        self.values = values
        distances = sorted(abs(v) for v in values if abs(v) > _EPS)
        typical = distances[len(distances) // 2] if distances else 24
        reach = max(typical * 10, 72)
        near = [v for v in values if abs(v) <= reach]
        self.low, self.high = min([0, *near]), max([0, *near])
        self.point = self.high - self.low < _EPS  # nothing but the origin itself
        self.far_before = [v for v in values if v < self.low - _EPS]
        self.far_after = [v for v in values if v > self.high + _EPS]
        self.weight = 0.5 if self.point and not alone else 1.6 if origin == "trigger" else 1.0
        self.weight += 0.35 * (bool(self.far_before) + bool(self.far_after))

    def place(self, start: float, end: float) -> None:
        self.gap = 16
        self.end = end
        self.far_width = min(0.2 * (end - start), 110)
        self.x0 = start + (self.far_width + self.gap if self.far_before else 0)
        self.x1 = end - (self.far_width + self.gap if self.far_after else 0)
        if self.point:
            if self.alone:
                self.low, self.high = -24, 24
            else:
                self.x0 = self.x1 = (start + end) / 2
        self.breaks = [
            at for present, at in ((self.far_before, self.x0 - 8), (self.far_after, self.x1 + 8)) if present
        ]

    def _far(self, hours, values, edge, origin, direction):
        # Within the far part, positions are logarithmic in the distance beyond the break.
        extreme = max(abs(v - edge) for v in values)
        t = math.log1p(abs(hours - edge)) / math.log1p(extreme) if extreme > 0 else 1
        return origin + direction * (self.gap + t * self.far_width)

    def x(self, hours: float) -> float:
        if hours < self.low - _EPS:
            return self._far(hours, self.far_before, self.low, self.x0, -1)
        if hours > self.high + _EPS:
            return self._far(hours, self.far_after, self.high, self.x1, 1)
        if self.high - self.low < _EPS:
            return self.x0
        return self.x0 + (hours - self.low) / (self.high - self.low) * (self.x1 - self.x0)

    def ticks(self) -> list[tuple[float, str]]:
        if self.high - self.low < _EPS:
            return [(v, hours_label(v)) for v in dict.fromkeys(self.far_before + self.far_after)]
        step = next(
            (s for s in _STEPS if (self.high - self.low) / s <= (6 if self.origin == "trigger" else 4)), 8760
        )
        ticks = []
        t = math.ceil(self.low / step) * step
        while t <= self.high + _EPS:
            ticks.append((t, hours_label(t, step)))
            t += step
        # Each far offset gets its own tick, labelled with its real value.
        return ticks + [(t, hours_label(t)) for t in dict.fromkeys(self.far_before + self.far_after)]


def _segments(task: Task) -> list[_Segment]:
    """The axis from left to right: the trigger's segment, and one for each event that a window is counted
    from, after (a next event) or before (a previous event) the segment the wait for it starts in."""
    sides = {s.key: s for w in task.windows for s in (w.start, w.end)}
    values: dict[str, list[float]] = {"trigger": []}
    for side in sides.values():
        if side.kind == "fixed":
            values.setdefault(side.origin, []).append(side.hours)
    order = ["trigger"]
    pending = [origin for origin in values if origin != "trigger"]
    while pending:
        progressed = False
        for origin in list(pending):
            window, _, which = origin.rpartition(".")
            other = sides.get(f"{window}.{'end' if which == 'start' else 'start'}")
            base = other.origin if other is not None and other.kind == "fixed" else "trigger"
            if base not in order:
                continue
            at = order.index(base)
            order.insert(at + 1 if sides[origin].kind == "next" else at, origin)
            pending.remove(origin)
            progressed = True
        if not progressed:  # origins that wait on each other: put them at the end rather than loop
            order += pending
            break
    return [_Segment(origin, values[origin], alone=len(order) == 1) for origin in order]


def render_svg(
    task: Task,
    theme: str = "auto",
    title: str | None = None,
    width: int = 960,
    background: bool = True,
    legend: bool = True,
) -> str:
    """The task's timeline as an SVG document.

    Args:
        task: from :func:`taskviz.read_task`.
        theme: ``"light"``, ``"dark"``, or ``"auto"`` (light, switching to dark with the reader's system
            setting through ``prefers-color-scheme``).
        title: a heading drawn above the timeline.
        width: the width of the drawing's coordinate system; the SVG itself scales to its container.
        background: draw the rounded panel behind the timeline. Without it the SVG is transparent.
        legend: draw the legend under the axis.

    Raises:
        ValueError: a window side could not be placed (see :func:`can_draw`), or the theme is unknown.
    """
    if theme not in (*THEMES, "auto"):
        raise ValueError(f"Unknown theme {theme!r}; use 'light', 'dark' or 'auto'.")
    if not can_draw(task):
        raise ValueError("The task has no windows, or a window boundary that could not be read.")
    rows = task.windows
    uid = "tv" + hashlib.sha256(repr((task.data, theme, title, width)).encode()).hexdigest()[:8]
    shift = 26 if title else 0
    left, right, row_height, top = 150, width - 20, 56, 50 + shift
    axis_y = top + len(rows) * row_height + 4
    height = axis_y + 22 + (30 if legend else 6)

    segments = _segments(task)
    between = 34  # the break between two segments: the time between them is not to scale
    pad = (0.16 if len(segments) == 1 else 0.09) * (right - left)  # room for open-ended sides
    room = right - left - 2 * pad - between * (len(segments) - 1)
    cursor = left + pad
    for segment in segments:
        share = room * segment.weight / sum(s.weight for s in segments)
        segment.place(cursor, cursor + share)
        cursor += share + between
    by_origin = {segment.origin: segment for segment in segments}
    sides = {s.key: s for w in rows for s in (w.start, w.end)}

    def position(side) -> float | None:
        if side.kind == "fixed":
            return by_origin[side.origin].x(side.hours)
        if side.key in by_origin:  # an event other windows are counted from
            return by_origin[side.key].x(0)
        return None

    svg = _Svg()
    if background:
        svg.add("rect", class_="tv-bg", x=0, y=0, width=width, height=height, rx=9)
    if title:
        svg.add("text", title, class_="tv-title", x=16, y=24)

    # Open-ended bars fade out towards the record's start or end; the label window in its own colour.
    defs = []
    for label in ("", " tv-is-label"):
        for direction in ("left", "right"):
            stops = "".join(
                _element("stop", offset=offset, class_=f"tv-stop{label}", stop_opacity=opacity)
                for offset, opacity in ((0, int(direction == "right")), (1, int(direction == "left")))
            )
            name = f"{uid}-fade-{direction}{'-label' if label else ''}"
            defs.append(f'<linearGradient id="{name}" x1="0" x2="1">{stops}</linearGradient>')
    defs.append(
        f'<marker id="{uid}-arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" '
        'orient="auto-start-reverse"><path d="M0 0 L10 5 L0 10 Z" class="tv-arrow-head"/></marker>'
    )
    svg.parts.append(f"<defs>{''.join(defs)}</defs>")

    svg.add("line", class_="tv-axis", x1=left, x2=right, y1=axis_y, y2=axis_y)
    for segment in segments:
        is_trigger = segment.origin == "trigger"
        for t, text in segment.ticks():
            at = segment.x(t)
            svg.add("line", class_="tv-grid", x1=at, x2=at, y1=top - 12, y2=axis_y)
            svg.add("text", text, class_="tv-tick", x=at, y=axis_y + 15, text_anchor="middle")
        at = segment.x(0)
        anchor = "start" if at < left + 170 else "end" if at > right - 170 else "middle"
        text = f"trigger: {task.trigger_text}" if is_trigger else sides[segment.origin].text
        svg.add(
            "line", class_="tv-trigger" if is_trigger else "tv-anchor", x1=at, x2=at, y1=26 + shift, y2=axis_y
        )
        svg.add(
            "text",
            text,
            class_="tv-trigger-label" if is_trigger else "tv-anchor-label",
            x=at + {"start": -4, "end": 4, "middle": 0}[anchor],
            y=18 + shift,
            text_anchor=anchor,
        )

    for i, row in enumerate(rows):
        y = top + i * row_height + row_height / 2
        _draw_window(svg, row, y, position(row.start), position(row.end), left, right, uid)

    breaks = [at for segment in segments for at in segment.breaks]
    breaks += [segment.end + between / 2 for segment in segments[:-1]]
    for at in breaks:
        svg.add("rect", class_="tv-break", x=at - 4, y=top - 14, width=8, height=axis_y - top + 22)
        svg.add(
            "path",
            class_="tv-break-mark",
            d=f"M{at - 7:.1f} {axis_y + 5} l6 -10 M{at + 1:.1f} {axis_y + 5} l6 -10",
        )
        svg.add("text", "≈", class_="tv-tick", x=at, y=top - 18, text_anchor="middle")

    svg.parts += svg.late
    if legend:
        _draw_legend(svg, left, height - 12)

    label = f"Timeline of the task's windows around the trigger ({task.trigger_text})"
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" class="taskviz {uid}" viewBox="0 0 {width} {height}" '
        f'width="100%" role="img" aria-label={quoteattr(label)}>'
        f"<title>{escape(title or label)}</title><style>{_style(theme, '.' + uid)}</style>"
        + "".join(svg.parts)
        + "</svg>\n"
    )


def _draw_window(svg: _Svg, row: Window, y: float, a, b, left: float, right: float, uid: str) -> None:
    """One window's row; ``a`` and ``b`` are where its start and end sit on the axis, when they do."""
    is_label = " tv-is-label" if row.label else ""
    bar = f"tv-bar{is_label}"
    fade = "-label" if row.label else ""
    svg.add("text", row.name, late=True, class_="tv-name", x=left - 10, y=y + 4, text_anchor="end")
    notes = []
    if a is not None and b is not None and row.end.kind == "next":
        # Until an event that other windows are counted from: the wait is not to scale.
        svg.add("rect", class_=bar, x=a, y=y - 7, width=10, height=14, rx=4)
        svg.add("line", class_="tv-open", x1=a + 10, x2=b, y1=y, y2=y, marker_end=f"url(#{uid}-arrow)")
    elif a is not None and b is not None and row.start.kind == "previous":
        svg.add("rect", class_=bar, x=b - 10, y=y - 7, width=10, height=14, rx=4)
        svg.add("line", class_="tv-open", x1=b - 10, x2=a, y1=y, y2=y, marker_end=f"url(#{uid}-arrow)")
    elif a is not None and b is not None:
        svg.add("rect", class_=bar, x=min(a, b), y=y - 7, width=max(abs(b - a), 3), height=14, rx=4)
    elif a is None and b is None:
        svg.add("line", class_="tv-open", x1=left, x2=right, y1=y, y2=y)
        notes.append(((left + right) / 2, "middle", f"{row.start.text} to {row.end.text}"))
    # Open start: the record's start (fading out) or a previous event (a dashed arrow back to it).
    if a is None and b is not None:
        if row.start.kind == "record":
            svg.add(
                "rect",
                x=left,
                y=y - 7,
                width=max(b - left, 3),
                height=14,
                rx=4,
                fill=f"url(#{uid}-fade-left{fade})",
            )
            notes.append((left + 2, "start", "record start"))
        else:
            svg.add("line", class_="tv-open", x1=b, x2=left + 4, y1=y, y2=y, marker_end=f"url(#{uid}-arrow)")
            notes.append((left + 2, "start", row.start.text))
    # Open end: the record's end (fading out) or the next event (a dashed arrow on to it).
    if b is None and a is not None:
        if row.end.kind == "record":
            svg.add(
                "rect",
                x=a,
                y=y - 7,
                width=max(right - a, 3),
                height=14,
                rx=4,
                fill=f"url(#{uid}-fade-right{fade})",
            )
            notes.append((right - 2, "end", "record end"))
        else:
            svg.add("rect", class_=bar, x=a, y=y - 7, width=10, height=14, rx=4)
            svg.add(
                "line", class_="tv-open", x1=a + 10, x2=right - 4, y1=y, y2=y, marker_end=f"url(#{uid}-arrow)"
            )
            notes.append((right - 2, "end", row.end.text))
    for nx, anchor, text in notes:
        svg.add("text", text, late=True, class_="tv-note", x=nx, y=y - 13, text_anchor=anchor)
    if row.label:
        svg.add(
            "text",
            f"label: {row.label}",
            late=True,
            class_="tv-label",
            x=(a if a is not None else left) + 4,
            y=y + 24,
        )
    # The prediction time: the window side its index_timestamp names.
    side = None
    if row.index == "start":
        side = a if a is not None else left if row.start.kind == "record" else None
    elif row.index == "end":
        side = b if b is not None else right if row.end.kind == "record" else None
    if side is not None:
        svg.add("path", class_="tv-index", d=f"M{side:.1f} {y - 12:.1f} l6 6 l-6 6 l-6 -6 Z")
        if row.index == "end" and a is not None and b is not None and abs(b - a) < 95:
            # A short window: the caption goes beside the diamond instead of back across the window's start.
            where = {"x": side + 10, "y": y + 4, "text_anchor": "start"}
        else:
            where = {"x": side, "y": y + 25, "text_anchor": "end" if row.index == "end" else "start"}
        svg.add("text", "prediction time", late=True, class_="tv-index-label", **where)


def _draw_legend(svg: _Svg, x: float, y: float) -> None:
    svg.add("rect", class_="tv-bar", x=x, y=y - 9, width=10, height=10, rx=2)
    svg.add("text", "window", class_="tv-legend", x=x + 15, y=y)
    svg.add("rect", class_="tv-bar tv-is-label", x=x + 78, y=y - 9, width=10, height=10, rx=2)
    svg.add("text", "label window", class_="tv-legend", x=x + 93, y=y)
    svg.add("path", class_="tv-index", d=f"M{x + 192} {y - 10} l6 6 l-6 6 l-6 -6 Z")
    svg.add("text", "prediction time", class_="tv-legend", x=x + 203, y=y)
    svg.add("line", class_="tv-open", x1=x + 306, x2=x + 326, y1=y - 4, y2=y - 4)
    svg.add("text", "until an event", class_="tv-legend", x=x + 332, y=y)

"""Read an ACES task configuration into the pieces a figure and a table need.

:func:`read_task` takes the YAML text (or a path) and returns a :class:`Task`: the trigger, the predicates in
plain words, and one :class:`Window` per window with each side resolved to where it sits on a time axis
around the trigger. Nothing here needs ACES or any data.

Durations are read with ``pytimeparse``, the parser ACES itself uses, so an offset ACES accepts is placed
where ACES places it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import timedelta
from pathlib import Path

import yaml
from pytimeparse import parse as _timeparse

_BOUNDARY_RE = re.compile(r"^([A-Za-z_][\w.]*)\s*(?:([+-])\s*(.+))?$")
_EXPR_RE = re.compile(r"^\s*(and|or)\s*\((.*)\)\s*$", re.DOTALL)
_HAS_RE = re.compile(r"^\(?\s*([^,()]+?)\s*,\s*([^,()]+?)\s*\)?$")

SPECIAL_PREDICATES = {
    "_ANY_EVENT": "any event",
    "_RECORD_START": "the start of the record",
    "_RECORD_END": "the end of the record",
}


class TaskError(ValueError):
    """The text is not an ACES task configuration that can be drawn."""


def parse_duration(text: str) -> timedelta | None:
    """Parse a duration the way ACES does; None when it is not one.

    >>> parse_duration("24h"), parse_duration("6574 days").days
    (datetime.timedelta(days=1), 6574)
    >>> parse_duration("soon") is None
    True
    """
    seconds = _timeparse(str(text).strip())
    return None if seconds is None else timedelta(seconds=seconds)


def humanize(delta: timedelta) -> str:
    """Render a duration in the largest unit that divides it.

    >>> humanize(timedelta(days=3)), humanize(timedelta(hours=-2)), humanize(timedelta(minutes=90))
    ('3d', '-2h', '90m')
    """
    seconds = delta.total_seconds()
    sign = "-" if seconds < 0 else ""
    seconds = abs(seconds)
    for unit, size in (("d", 86400), ("h", 3600), ("m", 60)):
        if seconds >= size and seconds % size == 0:
            return f"{sign}{int(seconds // size)}{unit}"
    return f"{sign}{int(seconds)}s"


@dataclass
class Side:
    """One side of a window.

    ``kind`` says how it is drawn: ``fixed`` (``hours`` from its ``origin``), ``record`` (the start or end
    of the subject's record), ``next`` or ``previous`` (the next or previous ``event``), or ``unknown``.

    The ``origin`` of a fixed side is ``"trigger"``, or the ``key`` of the event-bounded side it is counted
    from: in a readmission task the target window runs 30 days from ``input.end``, the next discharge.
    """

    kind: str = "unknown"
    text: str = ""  # in words: "trigger + 1d", "record start", "next discharge", "next discharge + 30d"
    hours: float | None = None
    event: str | None = None
    origin: str = "trigger"
    key: str = ""  # "<window>.<start|end>"


@dataclass
class Window:
    name: str
    start: Side
    end: Side
    start_inclusive: bool = True
    end_inclusive: bool = True
    has: dict[str, str] = field(default_factory=dict)  # predicate -> "(min, max)" as written
    label: str | None = None
    index: str | None = None  # "start" or "end": the side that is the prediction time

    @property
    def requirements(self) -> list[str]:
        """The ``has`` constraints in words.

        >>> w = Window("gap", Side(), Side(), has={"death": "(None, 0)", "_ANY_EVENT": "(5, None)"})
        >>> w.requirements
        ['no death', 'at least 5 of any event']
        """
        return [describe_count(name, count) for name, count in self.has.items()]


@dataclass
class Predicate:
    name: str
    definition: str  # in words: "code ADMISSION", "codes matching ^LAB//", "discharge or death"
    demographic: bool = False
    roles: list[str] = field(default_factory=list)  # "trigger", "trigger end", "label"


@dataclass
class Task:
    """An ACES task, ready to draw."""

    trigger: str
    windows: list[Window]
    predicates: list[Predicate] = field(default_factory=list)
    every: str | None = None  # a repeated trigger's interval, as written (ACES 0.8)
    until: str | None = None
    name: str | None = None
    data: dict = field(default_factory=dict)

    @property
    def trigger_text(self) -> str:
        """The trigger in words: ``admission``, or ``icu_admission, every 1h until icu_discharge``."""
        text = self.trigger
        if self.every:
            text += f", every {self.every}"
        if self.until:
            text += f" until {self.until}"
        return text

    @property
    def label(self) -> str | None:
        return next((w.label for w in self.windows if w.label), None)


def describe_count(name: str, count) -> str:
    """A ``has`` entry in words.

    >>> describe_count("admission", "(1, None)"), describe_count("death", "(None, 0)")
    ('at least 1 admission', 'no death')
    >>> describe_count("lab", "(2, 5)"), describe_count("x", "(1, 1)"), describe_count("x", "weird")
    ('2 to 5 lab', 'exactly 1 x', 'x: weird')
    """
    what = SPECIAL_PREDICATES.get(name, name)
    match = _HAS_RE.match(str(count))
    if not match:
        return f"{name}: {count}"

    def number(text):
        return None if text.lower() in {"none", "null"} else text

    low, high = number(match.group(1)), number(match.group(2))
    of = "of " if name in SPECIAL_PREDICATES else ""
    if high == "0" and low in (None, "0"):
        return f"no {what}"
    if low is None and high is None:
        return f"any number of {what}"
    if high is None:
        return f"at least {low} {of}{what}"
    if low is None:
        return f"at most {high} {of}{what}"
    if low == high:
        return f"exactly {low} {of}{what}"
    return f"{low} to {high} {of}{what}"


def _describe_code(code) -> str:
    if isinstance(code, dict) and "regex" in code:
        return f"codes matching {code['regex']}"
    if isinstance(code, dict) and isinstance(code.get("any"), list):
        return _listed("code", code["any"])
    if isinstance(code, dict) and isinstance(code.get("descendants_of"), list):
        return _listed("code", code["descendants_of"]) + " and everything below in the ontology"
    return f"code {code}"


def _listed(noun: str, items: list, shown: int = 4) -> str:
    items = [str(item) for item in items]
    text = ", ".join(items[:shown]) + (f" and {len(items) - shown} more" if len(items) > shown else "")
    return f"{noun}{'s' if len(items) != 1 else ''} {text}"


def describe_predicate(body) -> str:
    """A predicate's definition in words.

    >>> describe_predicate({"code": {"regex": "^ADMISSION//"}})
    'codes matching ^ADMISSION//'
    >>> describe_predicate({"expr": "or(discharge, death)"})
    'discharge or death'
    >>> describe_predicate({"code": "LAB//SpO2", "value_max": 90, "value_max_inclusive": True})
    'code LAB//SpO2 with value ≤ 90'
    """
    if not isinstance(body, dict):
        return str(body)
    if isinstance(body.get("expr"), str):
        match = _EXPR_RE.match(body["expr"])
        if not match:
            return body["expr"]
        parts = [part.strip() for part in match.group(2).split(",") if part.strip()]
        return f" {match.group(1)} ".join(parts)
    text = _describe_code(body.get("code"))
    low, high = body.get("value_min"), body.get("value_max")
    bounds = []
    if low is not None:
        bounds.append(f"{'≥' if body.get('value_min_inclusive') else '>'} {low}")
    if high is not None:
        bounds.append(f"{'≤' if body.get('value_max_inclusive') else '<'} {high}")
    if bounds:
        text += " with value " + " and ".join(bounds)
    for column, value in (body.get("other_cols") or {}).items():
        text += f", {column} = {value}"
    return text


@dataclass
class _Boundary:
    kind: str  # "unbounded" | "offset" | "event" | "unparsed"
    text: str = ""
    anchor: str | None = None
    offset: timedelta | None = None
    predicate: str | None = None
    direction: str | None = None


def _parse_boundary(value) -> _Boundary:
    if value is None:
        return _Boundary("unbounded")
    text = str(value).strip()
    if text.lower() in {"null", "none", ""}:
        return _Boundary("unbounded", text)
    for arrow, direction in (("->", "next"), ("<-", "previous")):
        if arrow in text:
            anchor, predicate = (part.strip() for part in text.split(arrow, 1))
            return _Boundary("event", text, anchor=anchor or None, predicate=predicate, direction=direction)
    match = _BOUNDARY_RE.match(text)
    if not match:
        return _Boundary("unparsed", text)
    anchor, sign, duration = match.groups()
    if duration is None:
        return _Boundary("offset", text, anchor=anchor, offset=timedelta(0))
    delta = parse_duration(duration)
    if delta is None:
        return _Boundary("unparsed", text)
    return _Boundary("offset", text, anchor=anchor, offset=-delta if sign == "-" else delta)


def _resolve(boundaries: dict[tuple[str, str], _Boundary]) -> dict[tuple[str, str], tuple | None]:
    """Where each side sits: ``(origin, offset)``, or None when it is unbounded or cannot be placed.

    The origin is ``"trigger"`` or the key of an event-bounded side, which is the origin of whatever is
    counted from it.
    """
    resolved: dict = {}

    def target(anchor, window):
        if anchor == "trigger":
            return None
        if anchor in ("start", "end"):
            return (window, anchor)
        other, _, side = anchor.partition(".")
        return (other, side if side in ("start", "end") else "start")

    def resolve(key, seen):
        if key in resolved:
            return resolved[key]
        boundary = boundaries.get(key)
        if boundary is None or key in seen or boundary.kind in ("unbounded", "unparsed"):
            resolved[key] = None
        elif boundary.kind == "event":
            resolved[key] = (key, timedelta(0))
        else:
            ref = target(boundary.anchor, key[0])
            base = ("trigger", timedelta(0)) if ref is None else resolve(ref, seen | {key})
            resolved[key] = None if base is None else (base[0], base[1] + boundary.offset)
        return resolved[key]

    for key in boundaries:
        resolve(key, frozenset())
    return resolved


def _offset_text(origin: str, offset: timedelta) -> str:
    if offset == timedelta(0):
        return origin
    return f"{origin} {'+' if offset > timedelta(0) else '-'} {humanize(abs(offset))}"


def _sides(boundaries: dict[tuple[str, str], _Boundary]) -> dict[tuple[str, str], Side]:
    placed = _resolve(boundaries)
    sides: dict[tuple[str, str], Side] = {}
    for key, boundary in boundaries.items():
        name = ".".join(key)
        if boundary.kind == "unbounded":
            sides[key] = Side("record", "record start" if key[1] == "start" else "record end", key=name)
        elif boundary.kind == "event":
            kind = "next" if boundary.direction == "next" else "previous"
            sides[key] = Side(kind, f"{kind} {boundary.predicate}", event=boundary.predicate, key=name)
    for key, boundary in boundaries.items():
        if key in sides:
            continue
        place = placed.get(key)
        if place is None:
            sides[key] = Side("unknown", boundary.text, key=".".join(key))
            continue
        origin, offset = place
        origin_text = "trigger" if origin == "trigger" else sides[origin].text
        sides[key] = Side(
            "fixed",
            _offset_text(origin_text, offset),
            hours=offset.total_seconds() / 3600,
            origin="trigger" if origin == "trigger" else ".".join(origin),
            key=".".join(key),
        )
    return sides


def read_task(source: str | Path | dict, name: str | None = None) -> Task:
    """Read an ACES task configuration from YAML text, a ``.yaml`` path or an already loaded mapping.

    Raises:
        TaskError: the source is not a mapping with a trigger and windows.

    >>> task = read_task('''
    ... predicates:
    ...   admission: {code: ADMISSION}
    ...   death: {code: DEATH}
    ... trigger: admission
    ... windows:
    ...   input: {start: null, end: trigger + 24h, index_timestamp: end}
    ...   target: {start: input.end, end: start + 7d, label: death}
    ... ''')
    >>> [(w.name, w.start.text, w.end.text) for w in task.windows]
    [('input', 'record start', 'trigger + 1d'), ('target', 'trigger + 1d', 'trigger + 8d')]
    >>> task.windows[1].end.hours, task.label
    (192.0, 'death')
    """
    if isinstance(source, dict):
        data = source
    else:
        text = str(source)
        if isinstance(source, Path) or ("\n" not in text and text.endswith((".yaml", ".yml"))):
            path = Path(source)
            name = name or path.stem
            text = path.read_text(encoding="utf-8")
        try:
            data = yaml.safe_load(text)
        except yaml.YAMLError as error:
            raise TaskError(f"Not valid YAML: {error}") from error
    if not isinstance(data, dict) or "trigger" not in data:
        raise TaskError("An ACES task configuration is a mapping with 'predicates', 'trigger' and 'windows'.")

    trigger, every, until = data["trigger"], None, None
    if isinstance(trigger, dict):  # ACES 0.8: {predicate, every, until}
        every = str(trigger["every"]).strip() if trigger.get("every") is not None else None
        until = trigger.get("until")
        trigger = trigger.get("predicate")
    if not isinstance(trigger, str):
        raise TaskError("'trigger' must name one predicate (or be a mapping with 'predicate').")

    bodies = data.get("windows") if isinstance(data.get("windows"), dict) else {}
    bodies = {str(name_): body for name_, body in bodies.items() if isinstance(body, dict)}
    boundaries = {
        (window, side): _parse_boundary(body.get(side))
        for window, body in bodies.items()
        for side in ("start", "end")
    }
    sides = _sides(boundaries)
    windows = []
    for window, body in bodies.items():
        index = body.get("index_timestamp")
        windows.append(
            Window(
                name=window,
                start=sides[(window, "start")],
                end=sides[(window, "end")],
                start_inclusive=bool(body.get("start_inclusive", True)),
                end_inclusive=bool(body.get("end_inclusive", True)),
                has={str(k): str(v) for k, v in (body.get("has") or {}).items()},
                label=body["label"] if isinstance(body.get("label"), str) else None,
                index=index if index in ("start", "end") else None,
            )
        )

    label = next((w.label for w in windows if w.label), None)
    predicates = []
    for section, demographic in (("predicates", False), ("patient_demographics", True)):
        for predicate, body in (
            (data.get(section) or {}).items() if isinstance(data.get(section), dict) else []
        ):
            roles = [
                role
                for role, target in (("trigger", trigger), ("trigger end", until), ("label", label))
                if target == predicate
            ]
            predicates.append(Predicate(str(predicate), describe_predicate(body), demographic, roles))
    return Task(trigger, windows, predicates, every=every, until=until, name=name, data=data)

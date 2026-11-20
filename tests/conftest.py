"""A tiny synthetic MEDS dataset: admissions, discharges and deaths for a few made-up subjects."""

from datetime import datetime, timedelta
from pathlib import Path

import pytest

EXAMPLES = Path(__file__).resolve().parent.parent / "examples" / "tasks"

MORTALITY = """
predicates:
  admission: {code: {regex: "^ADMISSION//"}}
  discharge: {code: DISCHARGE}
  death: {code: MEDS_DEATH}
  discharge_or_death:
    expr: or(discharge, death)
trigger: admission
windows:
  input:
    start: NULL
    end: trigger + 24h
    start_inclusive: True
    end_inclusive: True
    index_timestamp: end
  target:
    start: input.end
    end: start -> discharge_or_death
    start_inclusive: False
    end_inclusive: True
    label: death
"""


def _subject(subject_id: int, dies: bool) -> list[dict]:
    start = datetime(2020, 1, 1) + timedelta(days=subject_id)
    events = [
        (start, "ADMISSION//EMERGENCY"),
        (start + timedelta(hours=3), "LAB//X"),
        (start + timedelta(days=3), "MEDS_DEATH" if dies else "DISCHARGE"),
    ]
    return [
        {"subject_id": subject_id, "time": time, "code": code, "numeric_value": None} for time, code in events
    ]


@pytest.fixture(scope="session")
def meds_root(tmp_path_factory) -> Path:
    """Six subjects in ``train`` (two die) and three in ``held_out`` (one dies)."""
    import polars as pl

    root = tmp_path_factory.mktemp("meds")
    schema = {
        "subject_id": pl.Int64,
        "time": pl.Datetime("us"),
        "code": pl.String,
        "numeric_value": pl.Float32,
    }
    for split, subjects in (("train", range(1, 7)), ("held_out", range(7, 10))):
        rows = [row for subject in subjects for row in _subject(subject, dies=subject % 3 == 0)]
        (root / "data" / split).mkdir(parents=True)
        pl.DataFrame(rows, schema=schema).write_parquet(root / "data" / split / "0.parquet")
    (root / "metadata").mkdir()
    pl.DataFrame(
        {"code": ["MEDS_DEATH"], "description": ["death"], "parent_codes": [None]},
        schema={"code": pl.String, "description": pl.String, "parent_codes": pl.List(pl.String)},
    ).write_parquet(root / "metadata" / "codes.parquet")
    return root


@pytest.fixture(scope="session")
def mortality_config(tmp_path_factory) -> Path:
    path = tmp_path_factory.mktemp("task") / "mortality.yaml"
    path.write_text(MORTALITY)
    return path

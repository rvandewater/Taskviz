"""The numbers of a task on a dataset: how many prediction times, subjects and positive labels.

Two ways to get them:

- :func:`count_labels` reads label files that already exist (MEDS labels, as ACES, MEDS-DEV and others
  write them: ``subject_id``, ``prediction_time``, ``boolean_value``);
- :func:`extract_counts` runs ACES on a MEDS dataset with the task configuration and counts what it returns.
  It needs the ``extract`` extra (``pip install "taskviz[extract]"``).

Both return a :class:`Counts` holding aggregate counts only, per split. No row of the data is kept or
returned. ``min_count`` hides small cells when the table is rendered, for data that may not be published
at that resolution.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from taskviz.tables import Table

SPLIT_ORDER = ("train", "tuning", "held_out")


@dataclass
class SplitCounts:
    samples: int = 0  # prediction times (label rows)
    subjects: int = 0
    positives: int | None = None  # None when the task has no label
    trigger_events: int | None = None  # events matching the trigger, before any window constraint


@dataclass
class Counts:
    """Aggregate counts of one task on one dataset."""

    per_split: dict[str, SplitCounts] = field(default_factory=dict)
    dataset: str | None = None
    dropped: int = 0  # rows ACES returned without a prediction time, left out

    @property
    def total(self) -> SplitCounts:
        splits = list(self.per_split.values())

        def add(values):
            return None if any(v is None for v in values) or not values else sum(values)

        return SplitCounts(
            samples=sum(s.samples for s in splits),
            subjects=sum(s.subjects for s in splits),  # a subject is in one split
            positives=add([s.positives for s in splits]),
            trigger_events=add([s.trigger_events for s in splits]),
        )

    def as_dict(self) -> dict:
        """Plain numbers, for JSON."""

        def one(split: SplitCounts) -> dict:
            out = dict(split.__dict__)
            out["prevalence"] = (
                split.positives / split.samples if split.positives is not None and split.samples else None
            )
            return out

        return {
            "dataset": self.dataset,
            "splits": {name: one(split) for name, split in self.per_split.items()},
            "total": one(self.total),
            "dropped": self.dropped,
        }

    def table(self, min_count: int = 1) -> Table:
        """One row per split and a total.

        A count below ``min_count`` (and above zero) is shown as ``<min_count``, and a prevalence computed
        from one is left out.

        >>> counts = Counts({"train": SplitCounts(800, 500, 40), "held_out": SplitCounts(90, 60, 4)})
        >>> print(counts.table(min_count=10).to_markdown())
        | Split | Prediction times | Subjects | Positive labels | Prevalence |
        | --- | ---: | ---: | ---: | ---: |
        | train | 800 | 500 | 40 | 5.0% |
        | held_out | 90 | 60 | <10 |  |
        | Total | 890 | 560 | 44 | 4.9% |
        <BLANKLINE>
        """

        def shown(value):
            if value is None:
                return ""
            return f"<{min_count}" if 0 < value < min_count else f"{value:,}"

        def prevalence(split):
            if split.positives is None or not split.samples:
                return ""
            if 0 < split.positives < min_count or 0 < split.samples < min_count:
                return ""
            return f"{split.positives / split.samples:.1%}"

        total = self.total
        with_triggers = total.trigger_events is not None
        with_labels = any(split.positives is not None for split in self.per_split.values())
        columns = ["Split"] + (["Trigger events"] if with_triggers else []) + ["Prediction times", "Subjects"]
        columns += ["Positive labels", "Prevalence"] if with_labels else []
        rows = []
        order = [s for s in SPLIT_ORDER if s in self.per_split] + sorted(
            set(self.per_split) - set(SPLIT_ORDER)
        )
        named = [(name, self.per_split[name]) for name in order]
        for name, split in named + ([("Total", total)] if len(named) > 1 else []):
            row = [name] + ([shown(split.trigger_events)] if with_triggers else [])
            row += [shown(split.samples), shown(split.subjects)]
            row += [shown(split.positives), prevalence(split)] if with_labels else []
            rows.append(row)
        return Table(columns, rows, numeric=tuple(range(1, len(columns))))


def _split_of(path: Path, root: Path) -> str:
    """A shard's split is its directory under the root; files directly in the root are one split, 'all'."""
    relative = path.relative_to(root)
    return relative.parts[0] if len(relative.parts) > 1 else "all"


def count_labels(labels_dir: str | Path, dataset: str | None = None) -> Counts:
    """Count existing MEDS label files under ``labels_dir`` (``<split>/<shard>.parquet``, or flat)."""
    import polars as pl

    root = Path(labels_dir)
    files = sorted(root.rglob("*.parquet"))
    if not files:
        raise FileNotFoundError(f"No label files (*.parquet) under {root}.")
    subjects: dict[str, set] = {}
    counts = Counts(dataset=dataset)
    for path in files:
        split = counts.per_split.setdefault(_split_of(path, root), SplitCounts())
        frame = pl.read_parquet(path)
        _add(split, subjects.setdefault(_split_of(path, root), set()), frame, "boolean_value")
    return counts


def _add(split: SplitCounts, subjects: set, frame, label_column: str) -> None:
    split.samples += frame.height
    if "subject_id" in frame.columns:
        subjects.update(frame["subject_id"].unique().to_list())
        split.subjects = len(subjects)
    if label_column in frame.columns and frame[label_column].null_count() < max(frame.height, 1):
        split.positives = (split.positives or 0) + int(frame[label_column].cast(int).sum() or 0)
    elif label_column in frame.columns and split.positives is None and frame.height == 0:
        split.positives = 0


def extract_counts(
    config: str | Path,
    dataset_root: str | Path,
    dataset: str | None = None,
    on_shard: Callable[[int, int], None] | None = None,
) -> Counts:
    """Run ACES on every shard of a MEDS dataset and count the cohort.

    Args:
        config: path of the ACES task configuration (``.yaml``).
        dataset_root: a MEDS dataset (``data/<split>/<shard>.parquet``, ``metadata/``).
        dataset: a name for the dataset, kept on the result.
        on_shard: called as ``on_shard(done, total)`` after each shard.

    Only counts leave this function: ACES's result for a shard is counted and discarded.
    """
    try:
        import polars as pl
        from aces import config as aces_config
        from aces import predicates, query
        from omegaconf import DictConfig
    except ImportError as error:  # pragma: no cover
        raise ImportError('Extracting a cohort needs ACES: pip install "taskviz[extract]"') from error
    import logging

    logging.getLogger("aces").setLevel(logging.ERROR)
    try:
        from loguru import logger

        logger.disable("aces")
    except ImportError:  # pragma: no cover
        pass

    root = Path(dataset_root)
    data = root / "data" if (root / "data").is_dir() else root
    shards = sorted(data.rglob("*.parquet"))
    if not shards:
        raise FileNotFoundError(f"No MEDS shards (*.parquet) under {data}.")
    task = aces_config.TaskExtractorConfig.load(config_path=Path(config))
    trigger = task.trigger.predicate
    counts = Counts(dataset=dataset or root.name)
    subjects: dict[str, set] = {}
    for done, shard in enumerate(shards, start=1):
        name = _split_of(shard, data)
        split = counts.per_split.setdefault(name, SplitCounts())
        frame = predicates.get_predicates_df(task, DictConfig({"path": str(shard), "standard": "meds"}))
        if trigger in frame.columns:
            split.trigger_events = (split.trigger_events or 0) + int((frame[trigger] > 0).sum())
        result = query.query(task, frame)
        if result.is_empty():  # ACES returns a frame without columns when nothing qualifies
            result = pl.DataFrame(schema={"subject_id": pl.Int64, "label": pl.Int64})
            if not task.label_window:
                result = result.drop("label")
        if "index_timestamp" in result.columns:
            # ACES 0.8.0 returns a row without a prediction time for a trigger whose window never closes.
            complete = result.filter(pl.col("index_timestamp").is_not_null())
            counts.dropped += result.height - complete.height
            result = complete
        _add(split, subjects.setdefault(name, set()), result, "label")
        if task.label_window and split.positives is None:
            split.positives = 0
        if on_shard:
            on_shard(done, len(shards))
    return counts

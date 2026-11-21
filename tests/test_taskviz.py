import json
import xml.etree.ElementTree as ET

import pytest

from taskviz import (
    TaskError,
    can_draw,
    count_labels,
    extract_counts,
    predicates_table,
    read_task,
    render_html,
    render_svg,
    windows_table,
)
from taskviz.cli import main
from tests.conftest import EXAMPLES, MORTALITY

SVG = "{http://www.w3.org/2000/svg}"


def texts(svg: str) -> list[str]:
    return [node.text for node in ET.fromstring(svg).iter(f"{SVG}text")]


@pytest.mark.parametrize("path", sorted(EXAMPLES.glob("*.yaml")), ids=lambda p: p.stem)
@pytest.mark.parametrize("theme", ["auto", "light", "dark"])
def test_every_example_draws_as_well_formed_svg(path, theme):
    task = read_task(path)
    assert can_draw(task)
    svg = render_svg(task, theme=theme, title=path.stem)
    root = ET.fromstring(svg)
    assert root.tag == f"{SVG}svg" and root.get("viewBox")
    found = texts(svg)
    assert all(window.name in found for window in task.windows)
    assert f"trigger: {task.trigger_text}" in found
    assert "<script" not in svg


def test_sides_are_placed_relative_to_the_trigger():
    task = read_task(MORTALITY)
    input_, target = task.windows
    assert (input_.start.kind, input_.end.kind, input_.end.hours, input_.index) == (
        "record",
        "fixed",
        24.0,
        "end",
    )
    assert (target.start.hours, target.end.kind, target.end.event) == (24.0, "next", "discharge_or_death")
    assert task.label == "death"
    svg = render_svg(task)
    assert {"label: death", "prediction time", "next discharge_or_death", "record start"} <= set(texts(svg))


def test_a_window_counted_from_an_event_gets_its_own_axis_segment():
    task = read_task(EXAMPLES / "readmission_risk.yaml")
    target = next(w for w in task.windows if w.name == "target")
    assert (target.start.origin, target.start.hours) == ("input.end", 0.0)
    assert (target.end.text, target.end.hours) == ("next discharge + 30d", 720.0)
    found = texts(render_svg(task))
    assert "next discharge" in found and "≈" in found and "+28d" in found


def test_a_far_offset_goes_behind_an_axis_break():
    found = texts(render_svg(read_task(EXAMPLES / "new_admission_mortality.yaml")))
    assert "−5y" in found and "≈" in found and "+24h" in found


def test_a_repeated_trigger_is_spelled_out():
    task = read_task(EXAMPLES / "icu_mortality_hourly.yaml")
    assert task.trigger_text == "icu_admission, every 1h until icu_discharge"
    assert "trigger: icu_admission, every 1h until icu_discharge" in texts(render_svg(task))
    roles = {p.name: p.roles for p in task.predicates}
    assert roles == {"icu_admission": ["trigger"], "icu_discharge": ["trigger end"], "death": ["label"]}


def test_a_repeated_trigger_gets_a_bar_per_prediction_time():
    def bars(svg):
        return [n for n in ET.fromstring(svg).iter(f"{SVG}rect") if "tv-every" in (n.get("class") or "")]

    svg = render_svg(read_task(EXAMPLES / "icu_mortality_hourly.yaml"))
    assert len(bars(svg)) == 25  # the trigger and each of the 24 hours on the axis
    assert {"every 1h", "… until icu_discharge"} <= set(texts(svg))
    assert not bars(render_svg(read_task(MORTALITY)))
    # Too close together to draw one by one: a single band.
    dense = read_task(MORTALITY.replace("trigger: admission", "trigger: {predicate: admission, every: 1m}"))
    assert len(bars(render_svg(dense))) == 1


def test_a_gap_before_the_label_window_is_measured_out():
    assert "gap: 1d" in texts(render_svg(read_task(EXAMPLES / "inhospital_mortality.yaml")))
    # Without a gap window it is measured in the label window's row; no gap, no measure.
    late = read_task(MORTALITY.replace("start: input.end", "start: input.end + 90m"))
    assert "gap: 90m" in texts(render_svg(late))
    for name in ("imminent_mortality", "new_admission_mortality", "readmission_risk"):
        assert not any(t.startswith("gap:") for t in texts(render_svg(read_task(EXAMPLES / f"{name}.yaml"))))


def test_the_theme_decides_the_colours():
    task = read_task(MORTALITY)
    assert "prefers-color-scheme" in render_svg(task) and "prefers-color-scheme" not in render_svg(
        task, "light"
    )
    assert "#1d2734" in render_svg(task, "dark") and "#1d2734" not in render_svg(task, "light")
    with pytest.raises(ValueError):
        render_svg(task, theme="neon")


def test_two_figures_on_one_page_do_not_share_ids():
    a, b = (render_svg(read_task(MORTALITY)), render_svg(read_task(EXAMPLES / "readmission_risk.yaml")))
    ids = lambda svg: {node.get("id") for node in ET.fromstring(svg).iter() if node.get("id")}  # noqa: E731
    assert ids(a) and not ids(a) & ids(b)


def test_text_from_the_configuration_is_escaped():
    task = read_task(MORTALITY.replace("target:", '"<b>&":'))
    assert "<b>" not in render_svg(task) and "&lt;b&gt;&amp;" in render_svg(task)
    assert "<b>&" not in windows_table(task).to_html()


def test_what_cannot_be_read_is_reported():
    with pytest.raises(TaskError):
        read_task("just: text")
    with pytest.raises(TaskError):
        read_task("predicates: [unclosed")
    task = read_task(MORTALITY.replace("trigger + 24h", "trigger + soon"))
    assert not can_draw(task)
    with pytest.raises(ValueError):
        render_svg(task)


def test_tables_describe_windows_and_predicates():
    task = read_task(EXAMPLES / "inhospital_mortality.yaml")
    windows = {row["Window"]: row for row in windows_table(task).to_records()}
    assert windows["gap"]["Requires"] == "no admission; no discharge; no death"
    assert windows["input"]["Requires"] == "at least 5 of any event"
    assert windows["target"]["Role"] == "label: death"
    predicates = {row["Predicate"]: row for row in predicates_table(task).to_records()}
    assert predicates["discharge_or_death"]["Matches"] == "discharge or death"
    assert predicates["male"]["Role"] == "demographic"
    assert windows_table(task).to_html().count("<tr>") == 4


def test_counts_from_aces_on_a_meds_dataset(meds_root, mortality_config):
    counts = extract_counts(mortality_config, meds_root, dataset="synthetic")
    train, held_out = counts.per_split["train"], counts.per_split["held_out"]
    assert (train.trigger_events, train.samples, train.subjects, train.positives) == (6, 6, 6, 2)
    assert (held_out.samples, held_out.positives) == (3, 1)
    assert counts.total.samples == 9 and counts.as_dict()["total"]["prevalence"] == pytest.approx(1 / 3)
    table = counts.table()
    assert table.columns == [
        "Split",
        "Trigger events",
        "Prediction times",
        "Subjects",
        "Positive labels",
        "Prevalence",
    ]
    assert table.rows[-1] == ["Total", "9", "9", "9", "3", "33.3%"]
    # Small cells can be hidden, and a prevalence that would give one away goes with them.
    assert counts.table(min_count=5).rows[1] == ["held_out", "<5", "<5", "<5", "<5", ""]
    assert "Cohort on synthetic" in render_html(read_task(mortality_config), counts)


def test_hidden_counts_cannot_be_recovered_from_the_total():
    from taskviz.cohort import Counts, SplitCounts

    counts = Counts({"train": SplitCounts(800, 500, 40, 900), "held_out": SplitCounts(90, 60, 4, 95)})
    total = counts.table(min_count=10).rows[-1]
    # Positives are hidden in one split: the total is a bound, and no total prevalence is given.
    assert total == ["Total", "995", "890", "560", ">40", ""]
    # Without suppression the total is exact.
    assert counts.table().rows[-1] == ["Total", "995", "890", "560", "44", "4.9%"]
    # Every split hidden: only the bound the hidden cells imply themselves.
    small = Counts({"train": SplitCounts(4, 4, 1), "held_out": SplitCounts(3, 3, 0)})
    assert small.table(min_count=5).rows[-1] == ["Total", "<9", "<9", "<5", ""]


def test_counts_from_existing_label_files(tmp_path):
    import polars as pl
    from datetime import datetime

    for split, labels in (("train", [True, False, False]), ("held_out", [False])):
        (tmp_path / split).mkdir()
        pl.DataFrame(
            {
                "subject_id": list(range(len(labels))),
                "prediction_time": [datetime(2020, 1, 1)] * len(labels),
                "boolean_value": labels,
            }
        ).write_parquet(tmp_path / split / "0.parquet")
    counts = count_labels(tmp_path)
    assert counts.per_split["train"].positives == 1 and counts.total.samples == 4
    assert counts.total.trigger_events is None and "Trigger events" not in counts.table().columns
    with pytest.raises(FileNotFoundError):
        count_labels(tmp_path / "nowhere")


def test_cli_writes_figure_tables_and_counts(meds_root, mortality_config, tmp_path, capsys):
    assert main(["figure", str(mortality_config), "-o", str(tmp_path / "f.svg"), "--theme", "dark"]) == 0
    assert ET.fromstring((tmp_path / "f.svg").read_text()).tag == f"{SVG}svg"
    assert main(["table", str(mortality_config), "--what", "predicates", "--format", "json"]) == 0
    assert json.loads(capsys.readouterr().out)[0]["Predicate"] == "admission"
    assert main(["counts", str(mortality_config), "--data", str(meds_root), "--format", "json"]) == 0
    assert json.loads(capsys.readouterr().out)["total"]["samples"] == 9
    assert (
        main(["page", str(mortality_config), "--data", str(meds_root), "-o", str(tmp_path / "p.html")]) == 0
    )
    assert "<svg" in (tmp_path / "p.html").read_text()
    assert main(["figure", str(tmp_path / "missing.yaml")]) == 1

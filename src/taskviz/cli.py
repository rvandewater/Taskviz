"""Command line: ``taskviz figure``, ``taskviz table``, ``taskviz counts`` and ``taskviz page``."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from taskviz import cohort, figure, page, tables
from taskviz.task import TaskError, read_task


def _write(text: str, output: str | None) -> None:
    if output:
        Path(output).parent.mkdir(parents=True, exist_ok=True)
        Path(output).write_text(text, encoding="utf-8")
        print(f"wrote {output}", file=sys.stderr)
    else:
        sys.stdout.write(text)


def _render(table: tables.Table, fmt: str) -> str:
    if fmt == "html":
        return table.to_html()
    if fmt == "json":
        return json.dumps(table.to_records(), indent=2) + "\n"
    return table.to_markdown()


def _counts(args) -> cohort.Counts | None:
    if getattr(args, "labels", None):
        return cohort.count_labels(args.labels, dataset=args.dataset)
    if getattr(args, "data", None):

        def progress(done, total):
            if sys.stderr.isatty():
                print(f"\rshard {done}/{total}", end="" if done < total else "\n", file=sys.stderr)

        return cohort.extract_counts(args.config, args.data, dataset=args.dataset, on_shard=progress)
    return None


def _cohort_arguments(parser, required: bool) -> None:
    source = parser.add_mutually_exclusive_group(required=required)
    source.add_argument("--data", help="a MEDS dataset root; ACES is run on it (needs taskviz[extract])")
    source.add_argument("--labels", help="a directory of existing MEDS label files to count instead")
    parser.add_argument("--dataset", help="the dataset's name, for headings")
    parser.add_argument(
        "--min-count", type=int, default=1, help="show counts below this as '<N' (small-cell suppression)"
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="taskviz", description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)

    fig = commands.add_parser("figure", help="the task's timeline as an SVG")
    fig.add_argument("config", help="ACES task configuration (.yaml)")
    fig.add_argument("-o", "--output", help="file to write (default: stdout)")
    fig.add_argument("--theme", choices=["auto", "light", "dark"], default="auto")
    fig.add_argument("--title")
    fig.add_argument("--no-background", action="store_true", help="transparent, without the panel")
    fig.add_argument("--no-legend", action="store_true")

    tab = commands.add_parser("table", help="the task's windows or predicates as a table")
    tab.add_argument("config")
    tab.add_argument("--what", choices=["windows", "predicates"], default="windows")
    tab.add_argument("--format", choices=["markdown", "html", "json"], default="markdown")
    tab.add_argument("-o", "--output")

    cnt = commands.add_parser("counts", help="the cohort's numbers on a dataset as a table")
    cnt.add_argument("config")
    _cohort_arguments(cnt, required=True)
    cnt.add_argument("--format", choices=["markdown", "html", "json"], default="markdown")
    cnt.add_argument("-o", "--output")

    pg = commands.add_parser("page", help="figure and tables together as an HTML fragment")
    pg.add_argument("config")
    _cohort_arguments(pg, required=False)
    pg.add_argument("--theme", choices=["auto", "light", "dark"], default="auto")
    pg.add_argument("--title")
    pg.add_argument("-o", "--output")

    args = parser.parse_args(argv)
    try:
        task = read_task(Path(args.config))
        if args.command == "figure":
            text = figure.render_svg(
                task,
                theme=args.theme,
                title=args.title,
                background=not args.no_background,
                legend=not args.no_legend,
            )
        elif args.command == "table":
            table = tables.windows_table(task) if args.what == "windows" else tables.predicates_table(task)
            text = _render(table, args.format)
        elif args.command == "counts":
            counts = _counts(args)
            if args.format == "json" and args.min_count <= 1:
                text = json.dumps(counts.as_dict(), indent=2) + "\n"
            else:
                text = _render(counts.table(args.min_count), args.format)
        else:
            text = page.render_html(
                task, _counts(args), theme=args.theme, title=args.title, min_count=args.min_count
            )
    except (TaskError, FileNotFoundError, ValueError, ImportError) as error:
        print(f"taskviz: {error}", file=sys.stderr)
        return 1
    _write(text, args.output)
    return 0


if __name__ == "__main__":
    sys.exit(main())

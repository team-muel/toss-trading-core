from __future__ import annotations

import argparse
from pathlib import Path


def render_sql(
    *,
    source: Path,
    output: Path,
    project_id: str,
    dataset_id: str,
    table_id: str,
) -> str:
    text = source.read_text(encoding="utf-8")
    for placeholder in (
        "__PROJECT_ID__",
        "__DATASET_ID__",
        "__TABLE_ID__",
    ):
        if placeholder not in text:
            raise ValueError(f"SQL template lacks {placeholder}")
    text = text.replace("__PROJECT_ID__", project_id).replace(
        "__DATASET_ID__",
        dataset_id,
    ).replace("__TABLE_ID__", table_id)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(text, encoding="utf-8")
    return text


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--source",
        default="deploy/bigquery/latest_run_summaries.sql",
    )
    parser.add_argument("--output", required=True)
    parser.add_argument("--project-id", required=True)
    parser.add_argument("--dataset-id", required=True)
    parser.add_argument("--table-id", required=True)
    args = parser.parse_args(argv)
    render_sql(
        source=Path(args.source),
        output=Path(args.output),
        project_id=args.project_id,
        dataset_id=args.dataset_id,
        table_id=args.table_id,
    )
    print(args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

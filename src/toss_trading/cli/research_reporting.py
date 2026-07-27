from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any

from toss_trading.research.reporting import (
    build_monitoring_event,
    summary_to_bigquery_row,
)


def _read_summary(path: str | Path) -> dict[str, Any]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("reporting summary must be a JSON object")
    if payload.get("schema_version") != "research-visual-report-v1":
        raise ValueError("unsupported reporting summary schema")
    return payload


def insert_bigquery_row(
    *,
    summary: dict[str, Any],
    project_id: str,
    dataset_id: str,
    table_id: str,
) -> dict[str, Any]:
    import google.auth
    from google.auth.transport.requests import AuthorizedSession

    credentials, _ = google.auth.default(
        scopes=["https://www.googleapis.com/auth/cloud-platform"]
    )
    session = AuthorizedSession(credentials)
    url = (
        "https://bigquery.googleapis.com/bigquery/v2/projects/"
        f"{project_id}/datasets/{dataset_id}/tables/{table_id}/insertAll"
    )
    payload = {
        "skipInvalidRows": False,
        "ignoreUnknownValues": False,
        "rows": [
            {
                "insertId": summary["run_id"],
                "json": summary_to_bigquery_row(summary),
            }
        ],
    }
    response = session.post(url, json=payload, timeout=30)
    response.raise_for_status()
    result = response.json()
    if result.get("insertErrors"):
        raise RuntimeError(
            "BigQuery rejected reporting row: "
            + json.dumps(result["insertErrors"], sort_keys=True)
        )
    return result


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Publish verified research reporting summaries."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    event = subparsers.add_parser("event")
    event.add_argument("--summary", required=True)

    upload = subparsers.add_parser("upload-bigquery")
    upload.add_argument("--summary", required=True)
    upload.add_argument(
        "--project-id",
        default=os.environ.get("GCP_PROJECT_ID"),
    )
    upload.add_argument(
        "--dataset-id",
        default=os.environ.get(
            "RESEARCH_BIGQUERY_DATASET",
            "toss_research_reporting",
        ),
    )
    upload.add_argument(
        "--table-id",
        default=os.environ.get(
            "RESEARCH_BIGQUERY_TABLE",
            "run_summaries",
        ),
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    summary = _read_summary(args.summary)
    if args.command == "event":
        print(
            json.dumps(
                build_monitoring_event(summary),
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
        )
        return 0
    if not args.project_id:
        raise ValueError("--project-id or GCP_PROJECT_ID is required")
    insert_bigquery_row(
        summary=summary,
        project_id=args.project_id,
        dataset_id=args.dataset_id,
        table_id=args.table_id,
    )
    print(
        json.dumps(
            {
                "dataset_id": args.dataset_id,
                "project_id": args.project_id,
                "run_id": summary["run_id"],
                "state": "inserted",
                "table_id": args.table_id,
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

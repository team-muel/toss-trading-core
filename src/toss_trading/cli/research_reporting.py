from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
from typing import Any

from toss_trading.research.reporting import (
    build_monitoring_event,
    summary_to_bigquery_row,
)
from toss_trading.research.email_digest import (
    EmailDeliveryLedger,
    GmailApiClient,
    deliver_research_digest,
)
from toss_trading.research.interpretation import (
    DEFAULT_VERTEX_MODEL,
    VertexResearchInterpreter,
    build_research_evidence,
    deterministic_interpretation,
    load_interpretation,
    save_interpretation,
)


def _read_summary(path: str | Path) -> dict[str, Any]:
    summary_path = Path(path).resolve()
    payload = json.loads(summary_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("reporting summary must be a JSON object")
    if payload.get("schema_version") != "research-visual-report-v1":
        raise ValueError("unsupported reporting summary schema")
    if payload.get("ready_for_upload") is not True:
        raise ValueError("reporting summary is not upload-ready")
    run_root = summary_path.parent.parent
    status_path = run_root / "run-status.json"
    checksum_path = run_root / "SHA256SUMS"
    if not status_path.is_file() or not checksum_path.is_file():
        raise ValueError("verified run status and checksums are required")
    status = json.loads(status_path.read_text(encoding="utf-8"))
    if status.get("ready_for_upload") is not True:
        raise ValueError("research run is not upload-ready")
    expected_summary = status.get("reporting", {}).get("summary")
    if expected_summary != summary_path.relative_to(run_root).as_posix():
        raise ValueError("run status points to a different reporting summary")
    for field in ("run_id", "code_revision", "verified_at"):
        if status.get(field) != payload.get(field):
            raise ValueError(f"run status and reporting summary disagree on {field}")

    checked_paths: set[str] = set()
    for line in checksum_path.read_text(encoding="utf-8").splitlines():
        digest, separator, relative = line.partition("  ")
        if not separator or len(digest) != 64 or relative in checked_paths:
            raise ValueError("invalid or duplicate SHA256SUMS entry")
        checked_paths.add(relative)
        artifact = (run_root / relative).resolve()
        try:
            artifact.relative_to(run_root)
        except ValueError as exc:
            raise ValueError("SHA256SUMS entry escapes the research run") from exc
        if not artifact.is_file():
            raise ValueError(f"checksummed artifact is missing: {relative}")
        actual = hashlib.sha256(artifact.read_bytes()).hexdigest()
        if actual != digest:
            raise ValueError(f"checksummed artifact was modified: {relative}")
    summary_relative = summary_path.relative_to(run_root).as_posix()
    if summary_relative not in checked_paths or "run-status.json" not in checked_paths:
        raise ValueError("SHA256SUMS does not cover reporting and run status")
    return payload


def _read_optional_previous_summary(
    path: str | Path | None,
) -> dict[str, Any] | None:
    if not path:
        return None
    try:
        return _read_summary(path)
    except (OSError, ValueError):
        # A prior run is comparison-only evidence. Never let a missing, legacy,
        # or tampered prior artifact block the current verified report.
        return None


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
    interpret = subparsers.add_parser(
        "interpret",
        help="Create an evidence-bound Korean research interpretation.",
    )
    interpret.add_argument("--summary", required=True)
    interpret.add_argument("--previous-summary")
    interpret.add_argument("--output", required=True)
    interpret.add_argument(
        "--project-id",
        default=os.environ.get("GCP_PROJECT_ID"),
    )
    interpret.add_argument(
        "--location",
        default=os.environ.get("RESEARCH_INTERPRETATION_LOCATION", "global"),
    )
    interpret.add_argument(
        "--model",
        default=os.environ.get(
            "RESEARCH_INTERPRETATION_MODEL",
            DEFAULT_VERTEX_MODEL,
        ),
    )
    email = subparsers.add_parser(
        "email",
        help="Send one interpreted daily/weekly report through Gmail API OAuth.",
    )
    email.add_argument("--summary", required=True)
    email.add_argument("--previous-summary")
    email.add_argument("--interpretation", required=True)
    email.add_argument(
        "--sender",
        default=os.environ.get("RESEARCH_EMAIL_SENDER"),
    )
    email.add_argument(
        "--recipient",
        default=os.environ.get("RESEARCH_EMAIL_RECIPIENT"),
    )
    email.add_argument(
        "--dashboard-url",
        default=os.environ.get("RESEARCH_DASHBOARD_URL"),
    )
    email.add_argument(
        "--delivery-ledger",
        default=os.environ.get(
            "RESEARCH_EMAIL_LEDGER",
            "research-runtime/research_email.sqlite",
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
    if args.command == "upload-bigquery":
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

    previous = _read_optional_previous_summary(
        getattr(args, "previous_summary", None)
    )
    evidence = build_research_evidence(summary, previous=previous)
    if args.command == "interpret":
        output_path = Path(args.output)
        state = "reused"
        failure_type: str | None = None
        if output_path.is_file():
            interpretation = load_interpretation(
                output_path,
                evidence=evidence,
            )
        else:
            state = "generated"
            if os.environ.get("RESEARCH_INTERPRETATION_ENABLED", "1") == "1":
                if not args.project_id:
                    raise ValueError(
                        "--project-id or GCP_PROJECT_ID is required for Vertex AI"
                    )
                try:
                    interpretation = VertexResearchInterpreter(
                        project_id=args.project_id,
                        location=args.location,
                        model=args.model,
                    ).interpret(evidence)
                except Exception as exc:
                    failure_type = type(exc).__name__
                    interpretation = deterministic_interpretation(
                        summary,
                        evidence=evidence,
                        failure_reason=failure_type,
                    )
            else:
                failure_type = "VertexInterpretationDisabled"
                interpretation = deterministic_interpretation(
                    summary,
                    evidence=evidence,
                    failure_reason=failure_type,
                )
            save_interpretation(interpretation, output_path)
        result = {
            "current_run_id": summary["run_id"],
            "previous_run_id": evidence.previous_run_id,
            "source": interpretation.source,
            "state": state,
        }
        if failure_type:
            result["fallback_reason_type"] = failure_type
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
        return 0

    if not args.sender or not args.recipient:
        raise ValueError("research email sender and recipient are required")
    interpretation = load_interpretation(
        args.interpretation,
        evidence=evidence,
    )
    credentials = {
        name: os.environ.get(name, "")
        for name in (
            "GMAIL_OAUTH_CLIENT_ID",
            "GMAIL_OAUTH_CLIENT_SECRET",
            "GMAIL_OAUTH_REFRESH_TOKEN",
        )
    }
    missing = sorted(name for name, value in credentials.items() if not value)
    if missing:
        raise ValueError(f"missing Gmail OAuth environment: {', '.join(missing)}")
    result = deliver_research_digest(
        summary,
        sender=args.sender,
        recipient=args.recipient,
        client=GmailApiClient(
            client_id=credentials["GMAIL_OAUTH_CLIENT_ID"],
            client_secret=credentials["GMAIL_OAUTH_CLIENT_SECRET"],
            refresh_token=credentials["GMAIL_OAUTH_REFRESH_TOKEN"],
        ),
        ledger=EmailDeliveryLedger(args.delivery_ledger),
        interpretation=interpretation,
        dashboard_url=args.dashboard_url,
    )
    print(
        json.dumps(
            {
                "run_id": summary["run_id"],
                "state": result["state"],
                "interpretation_source": interpretation.source,
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

from __future__ import annotations

import hashlib
import json
import os
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Iterable


def _canonical(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _date(value: object, *, field: str) -> date:
    try:
        return date.fromisoformat(str(value))
    except ValueError as exc:
        raise ValueError(f"invalid {field}: {value!r}") from exc


def _timestamp(value: object, *, field: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"invalid {field}: {value!r}") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{field} must include a timezone")
    return parsed.astimezone(timezone.utc)


def append_collection_observation(
    path: str | Path,
    *,
    provider: str,
    adjustment: str,
    run_id: str,
    code_revision: str,
    requested_through_date: str,
    collected_at: str,
    latest_dates_by_symbol: dict[str, str],
) -> dict[str, Any]:
    """Append immutable, non-performance evidence for a successful collection."""

    if not latest_dates_by_symbol:
        raise ValueError("collection observation requires symbol dates")
    requested = _date(requested_through_date, field="requested_through_date")
    collected = _timestamp(collected_at, field="collected_at")
    normalized_dates = {
        str(symbol).strip().upper(): _date(value, field="latest symbol date").isoformat()
        for symbol, value in latest_dates_by_symbol.items()
        if str(symbol).strip()
    }
    if len(normalized_dates) != len(latest_dates_by_symbol):
        raise ValueError("collection observation contains an empty symbol")
    complete_through = min(_date(value, field="latest symbol date") for value in normalized_dates.values())
    if complete_through > requested:
        raise ValueError("complete_through_date exceeds the requested date")
    record: dict[str, Any] = {
        "schema_version": "prospective-collection-observation-v1",
        "provider": provider.strip(),
        "adjustment": adjustment.strip(),
        "run_id": run_id.strip(),
        "code_revision": code_revision.strip(),
        "requested_through_date": requested.isoformat(),
        "complete_through_date": complete_through.isoformat(),
        "collected_at": collected.isoformat(),
        "latest_dates_by_symbol": dict(sorted(normalized_dates.items())),
        "state": "collected",
    }
    required_text = ("provider", "adjustment", "run_id", "code_revision")
    if any(not record[field] for field in required_text):
        raise ValueError("collection observation contains an empty identity field")
    record["observation_id"] = hashlib.sha256(_canonical(record)).hexdigest()

    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    existing = load_collection_observations([destination])
    if any(item.get("observation_id") == record["observation_id"] for item in existing):
        return record
    with destination.open("ab") as handle:
        handle.write(_canonical(record) + b"\n")
        handle.flush()
        os.fsync(handle.fileno())
    return record


def append_run_completion(
    path: str | Path,
    *,
    run_id: str,
    code_revision: str,
    completed_at: str,
) -> dict[str, Any]:
    """Commit collection evidence only after the complete research run succeeds."""

    completed = _timestamp(completed_at, field="completed_at")
    record: dict[str, Any] = {
        "schema_version": "prospective-collection-observation-v1",
        "run_id": run_id.strip(),
        "code_revision": code_revision.strip(),
        "completed_at": completed.isoformat(),
        "state": "run_completed",
    }
    if not record["run_id"] or not record["code_revision"]:
        raise ValueError("run completion contains an empty identity field")
    record["observation_id"] = hashlib.sha256(_canonical(record)).hexdigest()
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    existing = load_collection_observations([destination])
    if any(item.get("observation_id") == record["observation_id"] for item in existing):
        return record
    with destination.open("ab") as handle:
        handle.write(_canonical(record) + b"\n")
        handle.flush()
        os.fsync(handle.fileno())
    return record


def load_collection_observations(paths: Iterable[str | Path]) -> list[dict[str, Any]]:
    observations: list[dict[str, Any]] = []
    for value in paths:
        path = Path(value)
        if not path.exists():
            continue
        for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if not line.strip():
                continue
            payload = json.loads(line)
            if not isinstance(payload, dict):
                raise ValueError(f"invalid observation at {path}:{line_number}")
            observations.append(payload)
    return observations


def assess_collection_continuity(
    validation_protocol: dict[str, Any],
    prospective_dates: Iterable[str],
    observations: Iterable[dict[str, Any]],
) -> dict[str, Any]:
    """Verify that every prospective market date was captured within the sealed lag."""

    policy = validation_protocol.get("prospective_collection_policy")
    if not isinstance(policy, dict):
        raise ValueError("validation protocol lacks prospective_collection_policy")
    provider = str(policy.get("provider", "")).strip()
    adjustment = str(policy.get("adjustment", "")).strip()
    max_lag = policy.get("maximum_collection_lag_calendar_days")
    if not provider or not adjustment:
        raise ValueError("prospective collection provider and adjustment are required")
    if isinstance(max_lag, bool) or not isinstance(max_lag, int) or max_lag < 0:
        raise ValueError("maximum collection lag must be a nonnegative integer")

    initial = list(validation_protocol.get("initial_collection_evidence", []))
    runtime = list(observations)
    completed_run_ids = {
        str(item.get("run_id", ""))
        for item in runtime
        if isinstance(item, dict) and item.get("state") == "run_completed"
    }
    combined = initial + [
        item
        for item in runtime
        if isinstance(item, dict)
        and item.get("state") == "collected"
        and str(item.get("run_id", "")) in completed_run_ids
    ]
    eligible: list[tuple[date, datetime, str]] = []
    for item in combined:
        if not isinstance(item, dict):
            raise ValueError("prospective collection evidence must be an object")
        if item.get("state") != "collected":
            continue
        if item.get("provider") != provider or item.get("adjustment") != adjustment:
            continue
        eligible.append(
            (
                _date(item.get("complete_through_date"), field="complete_through_date"),
                _timestamp(item.get("collected_at"), field="collected_at"),
                str(item.get("run_id", "")),
            )
        )

    invalid_intervals: list[tuple[date, date]] = []
    for item in policy.get("invalid_intervals", []):
        if not isinstance(item, dict):
            raise ValueError("invalid interval must be an object")
        start = _date(item.get("start"), field="invalid interval start")
        end = _date(item.get("end"), field="invalid interval end")
        if end < start:
            raise ValueError("invalid interval ends before it starts")
        invalid_intervals.append((start, end))

    verified: list[str] = []
    first_invalid: str | None = None
    evidence_runs: set[str] = set()
    for text in sorted(set(prospective_dates)):
        session = _date(text, field="prospective date")
        if any(start <= session <= end for start, end in invalid_intervals):
            first_invalid = session.isoformat()
            break
        matches = [
            (complete_through, collected_at, run_id)
            for complete_through, collected_at, run_id in eligible
            if complete_through >= session
            and collected_at.date() >= session
            and (collected_at.date() - session).days <= max_lag
        ]
        if not matches:
            first_invalid = session.isoformat()
            break
        _, _, run_id = min(matches, key=lambda item: item[1])
        verified.append(session.isoformat())
        if run_id:
            evidence_runs.add(run_id)

    return {
        "state": "invalid_data_gap" if first_invalid else "verified",
        "provider": provider,
        "adjustment": adjustment,
        "maximum_collection_lag_calendar_days": max_lag,
        "verified_trading_days": len(verified),
        "first_invalid_date": first_invalid,
        "evidence_run_ids": sorted(evidence_runs),
    }

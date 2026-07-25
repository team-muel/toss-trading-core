from __future__ import annotations

import hashlib
import json
import os
import tempfile
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable


@dataclass(frozen=True)
class CollectionWindow:
    start_date: str
    through_date: str
    realtime_start: str
    realtime_end: str


def resolve_collection_window(
    mode: str,
    *,
    today_utc: date | None = None,
) -> CollectionWindow:
    today = today_utc or datetime.now(timezone.utc).date()
    through = today - timedelta(days=1)
    if mode == "daily":
        start = today - timedelta(days=45)
        realtime_start = today - timedelta(days=90)
    elif mode == "weekly":
        start = date(2004, 1, 1)
        realtime_start = date(2004, 1, 1)
    else:
        raise ValueError("research automation mode must be daily or weekly")
    return CollectionWindow(
        start_date=start.isoformat(),
        through_date=through.isoformat(),
        realtime_start=realtime_start.isoformat(),
        realtime_end=today.isoformat(),
    )


def parse_provider_states(values: Iterable[str]) -> dict[str, str]:
    states: dict[str, str] = {}
    for value in values:
        provider, separator, state = value.partition("=")
        if not separator or not provider.strip() or not state.strip():
            raise ValueError(
                "provider state must use a non-empty provider=state value"
            )
        states[provider.strip()] = state.strip()
    return dict(sorted(states.items()))


def _read_json(path: Path) -> dict:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"expected a JSON object: {path}")
    return payload


def _validate_toss_bundle(path: Path, *, adjusted: bool) -> dict:
    payload = _read_json(path)
    request = payload.get("request")
    pages = payload.get("pages")
    failures = payload.get("failures")
    if not isinstance(request, dict):
        raise ValueError(f"Toss bundle has no request object: {path}")
    if request.get("adjusted") is not adjusted:
        raise ValueError(f"Toss bundle adjustment mismatch: {path}")
    if not isinstance(pages, list) or not pages:
        raise ValueError(f"Toss bundle contains no pages: {path}")
    if not isinstance(failures, list):
        raise ValueError(f"Toss bundle failures is not a list: {path}")
    for failure in failures:
        if not isinstance(failure, dict) or (
            failure.get("status_code") != 404
            or failure.get("code") != "stock-not-found"
            or failure.get("reason") != "provider_symbol_unavailable"
        ):
            raise ValueError(f"unexpected Toss collection failure: {failure!r}")
    return payload


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _atomic_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = tempfile.NamedTemporaryFile(
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
        delete=False,
        mode="w",
        encoding="utf-8",
    )
    temporary = Path(handle.name)
    try:
        with handle:
            json.dump(
                payload,
                handle,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def verify_research_run(
    run_dir: str | Path,
    *,
    mode: str,
    code_revision: str,
    provider_states: dict[str, str],
) -> dict:
    root = Path(run_dir)
    if not root.is_dir():
        raise ValueError(f"research run directory does not exist: {root}")
    raw_bundle = _validate_toss_bundle(
        root / "input" / "toss-candles-raw.json",
        adjusted=False,
    )
    adjusted_bundle = _validate_toss_bundle(
        root / "input" / "toss-candles-adjusted.json",
        adjusted=True,
    )
    raw_symbols = set(raw_bundle["request"].get("symbols", []))
    adjusted_symbols = set(adjusted_bundle["request"].get("symbols", []))
    if raw_symbols != adjusted_symbols:
        raise ValueError("Toss raw and adjusted bundles use different universes")

    qa = _read_json(root / "reports" / "market-bars-qa.json")
    if qa.get("ok") is not True:
        raise ValueError("market-bar QA did not pass")
    required_adjustments = {"raw", "split_adjusted"}
    if not required_adjustments.issubset(set(qa.get("adjustments", []))):
        raise ValueError("market-bar QA lacks Toss raw/split-adjusted coverage")

    manifests = sorted((root / "lake" / "catalog" / "manifests").glob("*.json"))
    if not manifests:
        raise ValueError("research run contains no data-lake manifests")
    parquet_files = sorted((root / "lake" / "silver").rglob("*.parquet"))
    if not parquet_files:
        raise ValueError("research run contains no normalized market bars")

    artifact_paths = sorted(
        path
        for path in root.rglob("*")
        if path.is_file()
        and path.name not in {"SHA256SUMS", "run-status.json"}
    )
    checksums = [
        f"{_sha256(path)}  {path.relative_to(root).as_posix()}"
        for path in artifact_paths
    ]
    (root / "SHA256SUMS").write_text(
        "\n".join(checksums) + "\n",
        encoding="utf-8",
    )

    status = {
        "schema_version": "research-automation-run-v1",
        "verified_at": datetime.now(timezone.utc).isoformat(),
        "mode": mode,
        "code_revision": code_revision,
        "ready_for_upload": True,
        "provider_states": provider_states,
        "toss": {
            "symbols_requested": len(raw_symbols),
            "raw_pages": len(raw_bundle["pages"]),
            "adjusted_pages": len(adjusted_bundle["pages"]),
            "raw_failures": raw_bundle["failures"],
            "adjusted_failures": adjusted_bundle["failures"],
        },
        "quality": {
            "adjustments": qa["adjustments"],
            "duplicate_rows": qa["duplicate_rows"],
            "invalid_rows": qa["invalid_rows"],
            "coverage_mismatch_rows": qa["coverage_mismatch_rows"],
            "symbols": qa["symbols"],
        },
        "artifacts": {
            "files": len(artifact_paths),
            "bytes": sum(path.stat().st_size for path in artifact_paths),
            "manifests": len(manifests),
            "parquet_files": len(parquet_files),
        },
    }
    _atomic_json(root / "run-status.json", status)
    return status

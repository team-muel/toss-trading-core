from __future__ import annotations

import hashlib
import json
import os
import tempfile
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

from toss_trading.cli.research_validate_bars import validate_parquet
from toss_trading.research.reporting import (
    build_research_summary,
    render_visual_report,
)


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
        # The UTC prior day is never ahead of the US-hosted FRED service date.
        # Long ALFRED ranges are split by the collector to remain below the
        # API's per-request vintage-date limit.
        realtime_end=through.isoformat(),
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


def _validate_code_revision(code_revision: str) -> str:
    revision = code_revision.strip()
    if not revision or revision.lower() == "unknown":
        raise ValueError("an immutable code revision is required")
    return revision


def _canonical_json(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _verified_manifests(
    root: Path,
    *,
    code_revision: str,
) -> tuple[dict[str, dict[str, Any]], list[Path]]:
    lake_root = (root / "lake").resolve()
    manifest_dir = lake_root / "catalog" / "manifests"
    manifest_paths = sorted(manifest_dir.glob("*.json"))
    if not manifest_paths:
        raise ValueError("research run contains no data-lake manifests")

    required = {
        "manifest_id",
        "layer",
        "source",
        "dataset",
        "schema_version",
        "retrieved_at",
        "available_at",
        "content_sha256",
        "byte_count",
        "media_type",
        "relative_path",
        "license_tag",
        "code_revision",
    }
    manifests: dict[str, dict[str, Any]] = {}
    parquet_from_manifests: set[Path] = set()
    for manifest_path in manifest_paths:
        payload = _read_json(manifest_path)
        missing = sorted(required - set(payload))
        if missing:
            raise ValueError(
                f"data-lake manifest lacks required fields: {manifest_path}: {missing}"
            )
        manifest_id = payload["manifest_id"]
        if not isinstance(manifest_id, str) or manifest_path.stem != manifest_id:
            raise ValueError(f"data-lake manifest identity mismatch: {manifest_path}")
        if manifest_id in manifests:
            raise ValueError(f"duplicate data-lake manifest id: {manifest_id}")
        if payload["layer"] not in {"bronze", "silver"}:
            raise ValueError(f"unsupported data-lake manifest layer: {manifest_id}")
        if payload["code_revision"] != code_revision:
            raise ValueError(f"data-lake manifest code revision mismatch: {manifest_id}")

        relative_path = payload["relative_path"]
        if not isinstance(relative_path, str) or not relative_path.strip():
            raise ValueError(f"data-lake manifest has no object path: {manifest_id}")
        object_path = (lake_root / relative_path).resolve()
        try:
            object_path.relative_to(lake_root)
        except ValueError as exc:
            raise ValueError(
                f"data-lake manifest escapes the run lake: {manifest_id}"
            ) from exc
        if not object_path.is_file():
            raise ValueError(f"data-lake object is missing: {manifest_id}")
        if object_path.stat().st_size != payload["byte_count"]:
            raise ValueError(f"data-lake object byte count mismatch: {manifest_id}")
        if _sha256(object_path) != payload["content_sha256"]:
            raise ValueError(f"data-lake object hash mismatch: {manifest_id}")

        request_sha256 = payload.get("request_sha256")
        request_metadata = payload.get("request_metadata")
        if request_sha256 is not None:
            actual_request_hash = hashlib.sha256(
                _canonical_json(request_metadata)
            ).hexdigest()
            if actual_request_hash != request_sha256:
                raise ValueError(
                    f"data-lake request metadata hash mismatch: {manifest_id}"
                )
        if object_path.suffix == ".parquet":
            if payload["layer"] != "silver":
                raise ValueError(
                    f"Parquet object must use a silver manifest: {manifest_id}"
                )
            parquet_from_manifests.add(object_path)
        manifests[manifest_id] = payload

    for manifest_id, payload in manifests.items():
        parents = payload.get("parent_manifest_ids", [])
        if not isinstance(parents, list):
            raise ValueError(f"manifest parents must be a list: {manifest_id}")
        missing_parents = sorted(set(parents) - set(manifests))
        if missing_parents:
            raise ValueError(
                f"manifest parent lineage is incomplete: {manifest_id}: {missing_parents}"
            )
        if payload["layer"] == "silver" and not parents:
            raise ValueError(f"silver manifest has no raw parent lineage: {manifest_id}")

    parquet_files = sorted((lake_root / "silver").rglob("*.parquet"))
    resolved_parquet = {path.resolve() for path in parquet_files}
    if not resolved_parquet:
        raise ValueError("research run contains no normalized market bars")
    if resolved_parquet != parquet_from_manifests:
        raise ValueError("normalized Parquet files and manifests do not match")
    return manifests, parquet_files


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


def _atomic_text(path: Path, text: str) -> None:
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
            handle.write(text)
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
    strategy_experiment: str | Path | None = None,
    hypothesis_plan: str | Path | None = None,
    hypothesis_evaluation: str | Path | None = None,
) -> dict:
    root = Path(run_dir)
    if not root.is_dir():
        raise ValueError(f"research run directory does not exist: {root}")
    code_revision = _validate_code_revision(code_revision)
    generated_outputs = (
        root / "run-status.json",
        root / "SHA256SUMS",
        root / "reports" / "reporting-summary.json",
        root / "reports" / "visual-report.html",
    )
    existing_outputs = [str(path) for path in generated_outputs if path.exists()]
    if existing_outputs:
        raise FileExistsError(
            "research run is already verified and immutable: "
            + ", ".join(existing_outputs)
        )
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
    raw_request = {
        key: value
        for key, value in raw_bundle["request"].items()
        if key != "adjusted"
    }
    adjusted_request = {
        key: value
        for key, value in adjusted_bundle["request"].items()
        if key != "adjusted"
    }
    if raw_request != adjusted_request:
        raise ValueError("Toss raw and adjusted bundles use different requests")

    qa = _read_json(root / "reports" / "market-bars-qa.json")
    required_adjustments = {"raw", "split_adjusted"}
    manifests, parquet_files = _verified_manifests(
        root,
        code_revision=code_revision,
    )
    recomputed_qa = validate_parquet(
        [str(path) for path in parquet_files],
        required_adjustments=required_adjustments,
        cross_provider_sources=(
            ("toss-openapi", "tiingo-eod")
            if provider_states.get("tiingo") == "collected"
            else ()
        ),
    )
    if recomputed_qa.get("ok") is not True:
        raise ValueError("recomputed market-bar QA did not pass")
    if qa != recomputed_qa:
        raise ValueError("stored market-bar QA does not match recomputed results")

    strategy_path: Path | None = None
    if strategy_experiment is not None:
        source_strategy = Path(strategy_experiment).resolve()
        if not source_strategy.is_file():
            raise ValueError(f"strategy experiment does not exist: {source_strategy}")
        strategy_path = root / "gold" / "experiments" / source_strategy.name
        if strategy_path.resolve() != source_strategy:
            strategy_path.parent.mkdir(parents=True, exist_ok=True)
            source_text = source_strategy.read_text(encoding="utf-8")
            if strategy_path.exists():
                if strategy_path.read_text(encoding="utf-8") != source_text:
                    raise FileExistsError(
                        f"immutable strategy experiment conflict: {strategy_path}"
                    )
            else:
                _atomic_text(strategy_path, source_text)
        else:
            strategy_path = source_strategy

    verified_at = datetime.now(timezone.utc).isoformat()
    toss_status = {
        "symbols_requested": len(raw_symbols),
        "raw_pages": len(raw_bundle["pages"]),
        "adjusted_pages": len(adjusted_bundle["pages"]),
        "raw_failures": raw_bundle["failures"],
        "adjusted_failures": adjusted_bundle["failures"],
    }
    quality_status = {
        "adjustments": qa["adjustments"],
        "duplicate_rows": qa["duplicate_rows"],
        "invalid_rows": qa["invalid_rows"],
        "coverage_mismatch_rows": qa["coverage_mismatch_rows"],
        "symbols": qa["symbols"],
        "provider_cross_check": qa.get("provider_cross_check"),
    }
    reporting_names = {
        "reporting-summary.json",
        "visual-report.html",
    }
    source_artifact_paths = sorted(
        path
        for path in root.rglob("*")
        if path.is_file()
        and path.name
        not in {"SHA256SUMS", "run-status.json", *reporting_names}
    )
    summary = build_research_summary(
        run_id=root.name,
        verified_at=verified_at,
        mode=mode,
        code_revision=code_revision,
        provider_states=provider_states,
        toss=toss_status,
        quality=quality_status,
        artifacts={
            "source_files": len(source_artifact_paths),
            "source_bytes": sum(
                path.stat().st_size for path in source_artifact_paths
            ),
            "manifests": len(manifests),
            "parquet_files": len(parquet_files),
        },
        strategy_experiment=strategy_path,
        hypothesis_plan=hypothesis_plan,
        hypothesis_evaluation=hypothesis_evaluation,
        available_manifest_ids={
            manifest_id
            for manifest_id, manifest in manifests.items()
            if manifest["layer"] == "silver"
            and "/adjustment=total_return/" in f"/{manifest['relative_path']}"
        },
    )
    _atomic_json(root / "reports" / "reporting-summary.json", summary)
    _atomic_text(
        root / "reports" / "visual-report.html",
        render_visual_report(summary),
    )

    artifact_paths = sorted(
        path
        for path in root.rglob("*")
        if path.is_file()
        and path.name not in {"SHA256SUMS", "run-status.json"}
    )
    status = {
        "schema_version": "research-automation-run-v2",
        "run_id": root.name,
        "verified_at": verified_at,
        "mode": mode,
        "code_revision": code_revision,
        "ready_for_upload": True,
        "provider_states": provider_states,
        "toss": toss_status,
        "quality": quality_status,
        "artifacts": {
            "files": len(artifact_paths),
            "bytes": sum(path.stat().st_size for path in artifact_paths),
            "manifests": len(manifests),
            "parquet_files": len(parquet_files),
        },
        "reporting": {
            "schema_version": summary["schema_version"],
            "summary": "reports/reporting-summary.json",
            "visual_report": "reports/visual-report.html",
            "strategy_state": summary["strategy"]["state"],
        },
    }
    _atomic_json(root / "run-status.json", status)
    checksum_paths = sorted(
        path
        for path in root.rglob("*")
        if path.is_file() and path.name != "SHA256SUMS"
    )
    checksums = [
        f"{_sha256(path)}  {path.relative_to(root).as_posix()}"
        for path in checksum_paths
    ]
    _atomic_text(root / "SHA256SUMS", "\n".join(checksums) + "\n")
    return status

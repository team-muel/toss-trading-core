from __future__ import annotations

import html
import hashlib
import json
import math
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


STRATEGY_METRIC_KEYS = (
    "total_return",
    "cagr",
    "annualized_volatility",
    "sharpe_zero_rate",
    "max_drawdown",
    "calmar",
    "turnover",
    "trading_days",
)


def _read_object(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"expected a JSON object: {path}")
    return payload


def _finite_number(value: Any, *, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"strategy metric is not numeric: {field}")
    result = float(value)
    if not math.isfinite(result):
        raise ValueError(f"strategy metric is not finite: {field}")
    return result


def _collection_failure_snapshot(
    failures: Any,
    *,
    field: str,
) -> list[dict[str, Any]]:
    if not isinstance(failures, list):
        raise ValueError(f"{field} must be a list")
    result: list[dict[str, Any]] = []
    for index, failure in enumerate(failures):
        if not isinstance(failure, dict):
            raise ValueError(f"{field}[{index}] must be an object")
        symbol = failure.get("symbol")
        status_code = failure.get("status_code")
        code = failure.get("code")
        reason = failure.get("reason")
        if (
            not isinstance(symbol, str)
            or not symbol.strip()
            or isinstance(status_code, bool)
            or not isinstance(status_code, int)
            or not isinstance(code, str)
            or not code.strip()
            or not isinstance(reason, str)
            or not reason.strip()
        ):
            raise ValueError(f"{field}[{index}] lacks normalized failure fields")
        result.append(
            {
                "symbol": symbol.strip().upper(),
                "status_code": status_code,
                "code": code.strip(),
                "reason": reason.strip(),
            }
        )
    return sorted(
        result,
        key=lambda item: (
            item["symbol"],
            item["status_code"],
            item["code"],
            item["reason"],
        ),
    )


def strategy_snapshot(
    experiment_path: str | Path | None,
    *,
    expected_code_revision: str | None = None,
    available_manifest_ids: set[str] | None = None,
) -> dict[str, Any]:
    if experiment_path is None:
        return {
            "state": "not_available",
            "reason": "verified_total_return_history_not_available",
            "artifact_state": "not_available",
            "methodology_state": "not_evaluated",
            "methodology_reason": "strategy_artifact_not_available",
            "benchmark_state": "not_evaluated",
            "benchmark_reason": "strategy_artifact_not_available",
            "promotion_state": "blocked",
            "promotion_reason": "strategy_artifact_not_available",
            "primary_benchmark": None,
            "prospective_state": None,
            "prospective_observed_days": None,
            "prospective_required_days": None,
            "strategy": None,
            "experiment_id": None,
            "code_revision": None,
            "metrics": {key: None for key in STRATEGY_METRIC_KEYS},
            "benchmark_metrics": {
                key: None for key in STRATEGY_METRIC_KEYS
            },
        }

    path = Path(experiment_path)
    payload = _read_object(path)
    strategy = payload.get("strategy")
    metrics = payload.get("metrics")
    if not isinstance(strategy, str) or not strategy.strip():
        raise ValueError(f"strategy experiment has no strategy name: {path}")
    if metrics is not None and not isinstance(metrics, dict):
        raise ValueError(f"strategy experiment has no metrics object: {path}")
    if isinstance(metrics, dict):
        missing = [key for key in STRATEGY_METRIC_KEYS if key not in metrics]
        if missing:
            raise ValueError(f"strategy experiment lacks metrics: {missing}")
    code_revision = payload.get("code_revision")
    if (
        not isinstance(code_revision, str)
        or not code_revision.strip()
        or code_revision.strip().lower() == "unknown"
    ):
        raise ValueError(f"strategy experiment has no immutable code revision: {path}")
    if (
        expected_code_revision is not None
        and code_revision != expected_code_revision
    ):
        raise ValueError(f"strategy experiment code revision mismatch: {path}")
    manifest_ids = payload.get("data_manifest_ids")
    if (
        not isinstance(manifest_ids, list)
        or not manifest_ids
        or any(not isinstance(item, str) or not item.strip() for item in manifest_ids)
    ):
        raise ValueError(f"strategy experiment has no data manifest lineage: {path}")
    if available_manifest_ids is not None:
        unknown = sorted(set(manifest_ids) - available_manifest_ids)
        if unknown:
            raise ValueError(
                f"strategy experiment references unknown manifests: {unknown}"
            )
    if payload.get("input_adjustment") != "total_return":
        raise ValueError(f"strategy experiment is not total-return based: {path}")
    for field in ("config", "benchmark_names", "rebalances", "equity_curve"):
        expected_type = dict if field == "config" else list
        if not isinstance(payload.get(field), expected_type):
            raise ValueError(f"strategy experiment has invalid {field}: {path}")
    canonical = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    digest = hashlib.sha256(canonical).hexdigest()
    expected_experiment_id = str(
        uuid.uuid5(uuid.NAMESPACE_URL, f"experiment:{digest}")
    )
    if path.stem != expected_experiment_id:
        raise ValueError(f"strategy experiment content address mismatch: {path}")
    normalized_metrics: dict[str, float | None]
    if metrics is None:
        normalized_metrics = {
            key: None for key in STRATEGY_METRIC_KEYS
        }
    else:
        normalized_metrics = {
            key: _finite_number(metrics[key], field=key)
            for key in STRATEGY_METRIC_KEYS
        }
        if normalized_metrics["trading_days"] < 1:
            raise ValueError("strategy trading_days must be positive")
        if normalized_metrics["turnover"] < 0:
            raise ValueError("strategy turnover must be nonnegative")
        if normalized_metrics["annualized_volatility"] < 0:
            raise ValueError("strategy annualized_volatility must be nonnegative")
        if not -1 <= normalized_metrics["max_drawdown"] <= 0:
            raise ValueError("strategy max_drawdown must be between -1 and 0")
    validation_protocol = payload.get("validation_protocol")
    primary_benchmark = (
        validation_protocol.get("primary_benchmark", "SPY buy-and-hold")
        if isinstance(validation_protocol, dict)
        else "SPY buy-and-hold"
    )
    if not isinstance(primary_benchmark, str) or not primary_benchmark:
        raise ValueError("strategy experiment has invalid primary benchmark")
    benchmark_payload = payload.get("benchmark_metrics")
    normalized_benchmark_metrics = {
        key: None for key in STRATEGY_METRIC_KEYS
    }
    benchmark_state = "not_evaluated"
    benchmark_reason = "primary_benchmark_not_available"
    if metrics is not None and isinstance(benchmark_payload, dict) and isinstance(
        benchmark_payload.get(primary_benchmark),
        dict,
    ):
        primary_metrics = benchmark_payload[primary_benchmark]
        benchmark_missing = [
            key for key in STRATEGY_METRIC_KEYS if key not in primary_metrics
        ]
        if benchmark_missing:
            raise ValueError(
                f"primary benchmark lacks metrics: {benchmark_missing}"
            )
        normalized_benchmark_metrics = {
            key: _finite_number(primary_metrics[key], field=f"benchmark.{key}")
            for key in STRATEGY_METRIC_KEYS
        }
        benchmark_passed = (
            normalized_metrics["cagr"] > normalized_benchmark_metrics["cagr"]
            and normalized_metrics["sharpe_zero_rate"]
            > normalized_benchmark_metrics["sharpe_zero_rate"]
            and normalized_metrics["max_drawdown"]
            >= normalized_benchmark_metrics["max_drawdown"]
        )
        benchmark_state = "passed" if benchmark_passed else "failed"
        benchmark_reason = (
            None
            if benchmark_passed
            else "strategy_did_not_beat_primary_benchmark_gate"
        )

    walk_forward_folds = payload.get("walk_forward_folds")
    prospective_holdout = payload.get("prospective_holdout")
    prospective_state = None
    prospective_observed_days = None
    prospective_required_days = None
    if isinstance(prospective_holdout, dict):
        prospective_state = prospective_holdout.get("state")
        prospective_observed_days = prospective_holdout.get(
            "observed_trading_days"
        )
        prospective_required_days = prospective_holdout.get(
            "minimum_trading_days"
        )
        if prospective_state not in {
            "collecting",
            "completed",
            "invalid_data_gap",
        }:
            raise ValueError("strategy experiment has invalid prospective state")
        for value, field in (
            (prospective_observed_days, "observed_trading_days"),
            (prospective_required_days, "minimum_trading_days"),
        ):
            if (
                isinstance(value, bool)
                or not isinstance(value, int)
                or value < 0
            ):
                raise ValueError(
                    f"strategy experiment has invalid prospective {field}"
                )
        if prospective_required_days < 63:
            raise ValueError("prospective minimum_trading_days must be at least 63")
        if prospective_state == "completed" and (
            prospective_observed_days < prospective_required_days
            or prospective_holdout.get("metrics_revealed") is not True
        ):
            raise ValueError("completed prospective holdout is inconsistent")
        if prospective_state in {"collecting", "invalid_data_gap"} and (
            prospective_holdout.get("metrics_revealed") is not False
            or metrics is not None
        ):
            raise ValueError("unsealed prospective holdout leaked metrics")
    prospective_protocol = (
        isinstance(validation_protocol, dict)
        and validation_protocol.get("untouched_holdout") is True
        and validation_protocol.get("headline_metrics_scope")
        == "prospective_holdout"
        and validation_protocol.get("parameter_selection")
        == "pre_registered_no_fit"
        and isinstance(walk_forward_folds, list)
    )
    if prospective_protocol and prospective_state == "collecting":
        methodology_state = "collecting"
        methodology_reason = "prospective_holdout_insufficient_observations"
        benchmark_reason = "prospective_holdout_not_complete"
    elif prospective_protocol and prospective_state == "invalid_data_gap":
        methodology_state = "failed"
        methodology_reason = "prospective_collection_continuity_failed"
        benchmark_reason = "prospective_holdout_invalid"
    elif (
        prospective_protocol
        and prospective_state == "completed"
        and metrics is not None
    ):
        methodology_state = "passed"
        methodology_reason = None
    else:
        methodology_state = "incomplete"
        methodology_reason = "true_out_of_sample_protocol_not_implemented"
    promotion_eligible = (
        methodology_state == "passed" and benchmark_state == "passed"
    )
    promotion_state = "eligible" if promotion_eligible else "blocked"
    if promotion_eligible:
        promotion_reason = None
    elif methodology_state != "passed":
        promotion_reason = methodology_reason
    else:
        promotion_reason = benchmark_reason
    return {
        "state": "available",
        "reason": None,
        "artifact_state": "available",
        "methodology_state": methodology_state,
        "methodology_reason": methodology_reason,
        "benchmark_state": benchmark_state,
        "benchmark_reason": benchmark_reason,
        "promotion_state": promotion_state,
        "promotion_reason": promotion_reason,
        "primary_benchmark": primary_benchmark,
        "prospective_state": prospective_state,
        "prospective_observed_days": prospective_observed_days,
        "prospective_required_days": prospective_required_days,
        "strategy": strategy.strip(),
        "experiment_id": path.stem,
        "code_revision": code_revision,
        "data_manifest_ids": sorted(set(manifest_ids)),
        "metrics": normalized_metrics,
        "benchmark_metrics": normalized_benchmark_metrics,
    }


def autonomous_research_snapshot(
    plan_path: str | Path | None,
    evaluation_path: str | Path | None = None,
) -> dict[str, Any]:
    if plan_path is None:
        snapshot = {
            "state": "not_scheduled",
            "created_count": 0,
            "reused_count": 0,
            "registered_count": None,
            "model": None,
            "failure_reason_type": None,
        }
    else:
        payload = json.loads(Path(plan_path).read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("hypothesis plan result must be an object")
        state = payload.get("state")
        if state not in {
            "completed",
            "failed",
            "disabled",
            "capacity_reached",
            "weekly_limit_reached",
            "cadence_audit",
        }:
            raise ValueError("hypothesis plan result has invalid state")
        created = payload.get("created", [])
        reused = payload.get("reused", [])
        if (
            not isinstance(created, list)
            or not isinstance(reused, list)
            or any(not isinstance(value, str) for value in [*created, *reused])
        ):
            raise ValueError("hypothesis plan result has invalid hypothesis ids")
        registered_count = payload.get("registered_count")
        if registered_count is not None and (
            isinstance(registered_count, bool)
            or not isinstance(registered_count, int)
            or registered_count < 0
        ):
            raise ValueError("hypothesis plan registered_count is invalid")
        snapshot = {
            "state": state,
            "created_count": len(created),
            "reused_count": len(reused),
            "registered_count": registered_count,
            "model": payload.get("model"),
            "failure_reason_type": payload.get("failure_reason_type"),
            "target_strategy_families": payload.get(
                "target_strategy_families", []
            ),
            "created_families": payload.get("created_families", {}),
            "near_duplicate_rejection_count": len(
                payload.get("rejected_near_duplicates", [])
            ),
            "invalid_proposal_rejection_count": len(
                payload.get("rejected_invalid", [])
            ),
        }
    snapshot.update(
        {
            "evaluation_state": "not_scheduled",
            "evaluated_count": 0,
            "historically_qualified_count": 0,
            "evaluation_failed_count": 0,
            "carried_forward_count": 0,
            "promotion_authorized": False,
            "execution_authorized": False,
        }
    )
    if evaluation_path is None:
        return snapshot
    evaluation = json.loads(Path(evaluation_path).read_text(encoding="utf-8"))
    if (
        not isinstance(evaluation, dict)
        or evaluation.get("schema_version")
        != "autonomous-candidate-evaluation-summary-v1"
        or evaluation.get("state") != "completed"
    ):
        raise ValueError("candidate evaluation summary is invalid")
    lists = {}
    for field in (
        "evaluated",
        "reused",
        "carried_forward",
        "historically_qualified",
        "evaluation_failed",
    ):
        value = evaluation.get(field)
        if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
            raise ValueError(f"candidate evaluation {field} is invalid")
        lists[field] = value
    candidate_results = evaluation.get("candidate_results")
    if not isinstance(candidate_results, list):
        raise ValueError("candidate evaluation candidate_results is invalid")
    normalized_candidates = []
    for item in candidate_results:
        if (
            not isinstance(item, dict)
            or not isinstance(item.get("hypothesis_id"), str)
            or not isinstance(item.get("state"), str)
            or not isinstance(item.get("failed_gates"), list)
            or any(not isinstance(gate, str) for gate in item["failed_gates"])
        ):
            raise ValueError("candidate evaluation candidate result is invalid")
        normalized_candidates.append(item)
    if evaluation.get("promotion_authorized") is not False or evaluation.get(
        "execution_authorized"
    ) is not False:
        raise ValueError("historical candidate evaluation cannot authorize promotion")
    snapshot.update(
        {
            "evaluation_state": "completed",
            "evaluated_count": len(lists["evaluated"]),
            "historically_qualified_count": len(lists["historically_qualified"]),
            "evaluation_failed_count": len(lists["evaluation_failed"]),
            "carried_forward_count": len(lists["carried_forward"]),
            "candidate_results": sorted(
                normalized_candidates, key=lambda item: item["hypothesis_id"]
            ),
            "family_counts": {
                family: sum(
                    1
                    for item in normalized_candidates
                    if item.get("strategy_family") == family
                )
                for family in sorted(
                    {
                        str(item.get("strategy_family", "unknown"))
                        for item in normalized_candidates
                    }
                )
            },
        }
    )
    return snapshot


def data_progress_snapshot(
    tiingo_collection: str | Path | None,
) -> dict[str, Any]:
    if tiingo_collection is None:
        return {
            "state": "not_available",
            "history_start_date": None,
            "requested_through_date": None,
            "complete_through_date": None,
            "normalized_rows_collected": 0,
            "total_return_rows_collected": 0,
            "symbol_count": 0,
        }
    payload = _read_object(Path(tiingo_collection))
    symbols = payload.get("symbols")
    if (
        not isinstance(symbols, list)
        or any(not isinstance(symbol, str) or not symbol.strip() for symbol in symbols)
    ):
        raise ValueError("Tiingo collection symbols are invalid")
    normalized_rows = payload.get("rows")
    total_return_rows = payload.get("total_return_rows")
    if (
        isinstance(normalized_rows, bool)
        or not isinstance(normalized_rows, int)
        or normalized_rows <= 0
    ):
        raise ValueError("Tiingo collection row count is invalid")
    if total_return_rows is None:
        total_return_rows = normalized_rows // 2
    if (
        isinstance(total_return_rows, bool)
        or not isinstance(total_return_rows, int)
        or total_return_rows <= 0
        or total_return_rows > normalized_rows
    ):
        raise ValueError("Tiingo total-return row count is invalid")
    date_fields = {}
    for field in (
        "history_start_date",
        "requested_through_date",
        "complete_through_date",
    ):
        value = payload.get(field)
        if not isinstance(value, str) or len(value) != 10:
            raise ValueError(f"Tiingo collection {field} is invalid")
        try:
            datetime.fromisoformat(value)
        except ValueError as exc:
            raise ValueError(f"Tiingo collection {field} is invalid") from exc
        date_fields[field] = value
    return {
        "state": "collected",
        **date_fields,
        "normalized_rows_collected": normalized_rows,
        "total_return_rows_collected": total_return_rows,
        "symbol_count": len(set(symbols)),
    }


def build_research_summary(
    *,
    run_id: str,
    verified_at: str,
    mode: str,
    code_revision: str,
    provider_states: dict[str, str],
    toss: dict[str, Any],
    quality: dict[str, Any],
    artifacts: dict[str, Any],
    strategy_experiment: str | Path | None = None,
    hypothesis_plan: str | Path | None = None,
    hypothesis_evaluation: str | Path | None = None,
    tiingo_collection: str | Path | None = None,
    available_manifest_ids: set[str] | None = None,
) -> dict[str, Any]:
    symbols = quality.get("symbols")
    if (
        not isinstance(symbols, list)
        or any(not isinstance(symbol, str) or not symbol.strip() for symbol in symbols)
    ):
        raise ValueError("quality symbols must be a list of non-empty strings")
    normalized_symbols = sorted({symbol.strip().upper() for symbol in symbols})
    quality_error_rows = sum(
        int(quality[key])
        for key in (
            "duplicate_rows",
            "invalid_rows",
            "coverage_mismatch_rows",
        )
    )
    raw_failures = _collection_failure_snapshot(
        toss["raw_failures"],
        field="toss.raw_failures",
    )
    adjusted_failures = _collection_failure_snapshot(
        toss["adjusted_failures"],
        field="toss.adjusted_failures",
    )
    summary = {
        "schema_version": "research-visual-report-v1",
        "run_id": run_id,
        "verified_at": verified_at,
        "mode": mode,
        "code_revision": code_revision,
        "ready_for_upload": True,
        "provider_states": dict(sorted(provider_states.items())),
        "toss": {
            "symbols_requested": int(toss["symbols_requested"]),
            "raw_pages": int(toss["raw_pages"]),
            "adjusted_pages": int(toss["adjusted_pages"]),
            "raw_failure_count": len(raw_failures),
            "adjusted_failure_count": len(adjusted_failures),
            "raw_failures": raw_failures,
            "adjusted_failures": adjusted_failures,
        },
        "quality": {
            "adjustments": sorted(quality["adjustments"]),
            "duplicate_rows": int(quality["duplicate_rows"]),
            "invalid_rows": int(quality["invalid_rows"]),
            "coverage_mismatch_rows": int(
                quality["coverage_mismatch_rows"]
            ),
            "error_rows": quality_error_rows,
            "symbol_count": len(normalized_symbols),
            "symbols": normalized_symbols,
        },
        "artifacts": {
            "source_files": int(artifacts["source_files"]),
            "source_bytes": int(artifacts["source_bytes"]),
            "manifests": int(artifacts["manifests"]),
            "parquet_files": int(artifacts["parquet_files"]),
        },
        "strategy": strategy_snapshot(
            strategy_experiment,
            expected_code_revision=code_revision,
            available_manifest_ids=available_manifest_ids,
        ),
        "autonomous_research": autonomous_research_snapshot(
            hypothesis_plan, hypothesis_evaluation
        ),
        "data_progress": data_progress_snapshot(tiingo_collection),
    }
    return summary


def summary_to_bigquery_row(
    summary: dict[str, Any],
    *,
    ingested_at: str | None = None,
) -> dict[str, Any]:
    providers = summary["provider_states"]
    toss = summary["toss"]
    quality = summary["quality"]
    artifacts = summary["artifacts"]
    strategy = summary["strategy"]
    autonomous = summary.get("autonomous_research", {})
    metrics = strategy["metrics"]
    row = {
        "schema_version": summary["schema_version"],
        "run_id": summary["run_id"],
        "verified_at": summary["verified_at"],
        "ingested_at": ingested_at
        or datetime.now(timezone.utc).isoformat(),
        "mode": summary["mode"],
        "code_revision": summary["code_revision"],
        "ready_for_upload": summary["ready_for_upload"],
        "provider_toss": providers.get("toss"),
        "provider_tiingo": providers.get("tiingo"),
        "provider_fred_alfred": providers.get("fred-alfred"),
        "provider_sec_edgar": providers.get("sec-edgar"),
        "toss_symbols_requested": toss["symbols_requested"],
        "toss_raw_pages": toss["raw_pages"],
        "toss_adjusted_pages": toss["adjusted_pages"],
        "toss_raw_failure_count": toss["raw_failure_count"],
        "toss_adjusted_failure_count": toss["adjusted_failure_count"],
        "quality_adjustments": ",".join(quality["adjustments"]),
        "quality_duplicate_rows": quality["duplicate_rows"],
        "quality_invalid_rows": quality["invalid_rows"],
        "quality_coverage_mismatch_rows": quality[
            "coverage_mismatch_rows"
        ],
        "quality_error_rows": quality["error_rows"],
        "quality_symbol_count": quality["symbol_count"],
        "artifact_source_files": artifacts["source_files"],
        "artifact_source_bytes": artifacts["source_bytes"],
        "artifact_manifests": artifacts["manifests"],
        "artifact_parquet_files": artifacts["parquet_files"],
        "strategy_state": strategy["state"],
        "strategy_reason": strategy["reason"],
        "strategy_artifact_state": strategy["artifact_state"],
        "strategy_methodology_state": strategy["methodology_state"],
        "strategy_methodology_reason": strategy["methodology_reason"],
        "strategy_benchmark_state": strategy["benchmark_state"],
        "strategy_benchmark_reason": strategy["benchmark_reason"],
        "strategy_promotion_state": strategy["promotion_state"],
        "strategy_promotion_reason": strategy["promotion_reason"],
        "strategy_primary_benchmark": strategy["primary_benchmark"],
        "strategy_prospective_state": strategy["prospective_state"],
        "strategy_prospective_observed_days": strategy[
            "prospective_observed_days"
        ],
        "strategy_prospective_required_days": strategy[
            "prospective_required_days"
        ],
        "strategy_name": strategy["strategy"],
        "strategy_experiment_id": strategy["experiment_id"],
        "strategy_code_revision": strategy["code_revision"],
        "autonomous_research_state": autonomous.get("state"),
        "autonomous_hypotheses_created": autonomous.get("created_count"),
        "autonomous_hypotheses_reused": autonomous.get("reused_count"),
        "autonomous_hypotheses_registered": autonomous.get("registered_count"),
        "autonomous_research_model": autonomous.get("model"),
        "autonomous_evaluation_state": autonomous.get("evaluation_state"),
        "autonomous_hypotheses_evaluated": autonomous.get("evaluated_count"),
        "autonomous_hypotheses_historically_qualified": autonomous.get(
            "historically_qualified_count"
        ),
        "autonomous_hypotheses_evaluation_failed": autonomous.get(
            "evaluation_failed_count"
        ),
        "autonomous_promotion_authorized": autonomous.get(
            "promotion_authorized", False
        ),
    }
    for key in STRATEGY_METRIC_KEYS:
        row[f"strategy_{key}"] = metrics[key]
        row[f"benchmark_{key}"] = strategy["benchmark_metrics"][key]
    return row


def build_monitoring_event(summary: dict[str, Any]) -> dict[str, Any]:
    row = summary_to_bigquery_row(
        summary,
        ingested_at=summary["verified_at"],
    )
    event = {
        "ts": summary["verified_at"],
        "event": "research_reporting_summary",
        "run_id": summary["run_id"],
        "mode": summary["mode"],
        "code_revision": summary["code_revision"],
        "ready_for_upload": summary["ready_for_upload"],
        "quality_error_rows": row["quality_error_rows"],
        "quality_symbol_count": row["quality_symbol_count"],
        "toss_collection_failure_count": (
            row["toss_raw_failure_count"]
            + row["toss_adjusted_failure_count"]
        ),
        "toss_page_count": (
            row["toss_raw_pages"] + row["toss_adjusted_pages"]
        ),
        "artifact_source_bytes": row["artifact_source_bytes"],
        "strategy_state": row["strategy_state"],
        "strategy_artifact_state": row["strategy_artifact_state"],
        "strategy_methodology_state": row["strategy_methodology_state"],
        "strategy_benchmark_state": row["strategy_benchmark_state"],
        "strategy_promotion_state": row["strategy_promotion_state"],
        "strategy_prospective_state": row["strategy_prospective_state"],
        "strategy_prospective_observed_days": row[
            "strategy_prospective_observed_days"
        ],
        "strategy_prospective_required_days": row[
            "strategy_prospective_required_days"
        ],
    }
    if (
        row["strategy_state"] == "available"
        and row["strategy_total_return"] is not None
    ):
        for key in STRATEGY_METRIC_KEYS:
            event[f"strategy_{key}"] = row[f"strategy_{key}"]
            event[f"benchmark_{key}"] = row[f"benchmark_{key}"]
        event["strategy_name"] = row["strategy_name"]
        event["strategy_experiment_id"] = row["strategy_experiment_id"]
        event["strategy_primary_benchmark"] = row[
            "strategy_primary_benchmark"
        ]
    else:
        event["strategy_reason"] = row["strategy_reason"]
    if row["strategy_promotion_state"] == "blocked":
        event["strategy_promotion_reason"] = row[
            "strategy_promotion_reason"
        ]
    return event


def render_visual_report(summary: dict[str, Any]) -> str:
    def escaped(value: Any) -> str:
        return html.escape(str(value), quote=True)

    def number(value: Any) -> str:
        if value is None:
            return "미측정"
        return f"{float(value):,.4f}"

    quality = summary["quality"]
    toss = summary["toss"]
    strategy = summary["strategy"]
    metrics = strategy["metrics"]
    collection_failure_count = (
        toss["raw_failure_count"] + toss["adjusted_failure_count"]
    )
    providers = "".join(
        (
            '<div class="provider"><span>'
            f"{escaped(name)}</span><strong>{escaped(state)}</strong></div>"
        )
        for name, state in summary["provider_states"].items()
    )
    quality_state = (
        "PASS"
        if quality["error_rows"] == 0 and collection_failure_count == 0
        else "REVIEW"
    )
    collection_state = (
        "정상" if collection_failure_count == 0 else "부분 성공"
    )
    strategy_state = (
        (
            f"{escaped(strategy['strategy'])} ? "
            f"?? {escaped(strategy['promotion_state'])}"
        )
        if strategy["state"] == "available"
        else "총수익률 데이터 확보 전 · 미측정"
    )
    strategy_cards = "".join(
        (
            '<div class="metric strategy-metric"><span>'
            f"{escaped(label)}</span><strong>{number(metrics[key])}</strong></div>"
        )
        for key, label in (
            ("total_return", "총수익률"),
            ("cagr", "CAGR"),
            ("sharpe_zero_rate", "Sharpe (Rf=0)"),
            ("max_drawdown", "최대 낙폭"),
        )
    )
    return f"""<!doctype html>
<html lang="ko">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Toss Trading 통합 보고서</title>
<style>
:root{{--bg:#07111f;--panel:#0f1d2e;--line:#203249;--text:#eef5ff;
--muted:#93a7bf;--good:#36d399;--warn:#fbbf24;--accent:#5ca9ff}}
*{{box-sizing:border-box}} body{{margin:0;background:linear-gradient(145deg,#07111f,
#0a1728);color:var(--text);font-family:Inter,"Noto Sans KR",system-ui,sans-serif}}
main{{max-width:1120px;margin:auto;padding:40px 24px 64px}} h1{{margin:0 0 8px;
font-size:clamp(28px,4vw,44px)}} .sub{{color:var(--muted);margin-bottom:28px}}
.grid{{display:grid;grid-template-columns:repeat(12,1fr);gap:16px}}
.panel{{grid-column:span 12;background:rgba(15,29,46,.94);border:1px solid
var(--line);border-radius:18px;padding:20px;box-shadow:0 18px 45px #0005}}
.third{{grid-column:span 4}} .half{{grid-column:span 6}} .strategy-metric{{
grid-column:span 3;display:block}} .strategy-metric strong{{display:block;
font-size:24px;margin-top:8px}} h2{{font-size:17px;
margin:0 0 16px;color:#cfe4ff}} .hero{{font-size:34px;font-weight:800}}
.ok{{color:var(--good)}} .metric,.provider{{display:flex;align-items:center;
justify-content:space-between;padding:11px 0;border-bottom:1px solid var(--line)}}
.metric:last-child,.provider:last-child{{border:0}} span{{color:var(--muted)}}
strong{{font-variant-numeric:tabular-nums}} .note{{color:var(--muted);
line-height:1.7;margin:0}} code{{color:#b9d9ff}} footer{{color:var(--muted);
margin-top:24px;font-size:13px}} @media(max-width:760px){{.third,.half{{
grid-column:span 12}}.strategy-metric{{grid-column:span 6}}}}
</style>
</head>
<body><main>
<h1>Toss Trading 통합 보고서</h1>
<div class="sub">{escaped(summary["verified_at"])} · {escaped(summary["run_id"])}</div>
<div class="grid">
  <section class="panel third"><h2>수집 실행</h2>
    <div class="hero {'ok' if collection_failure_count == 0 else ''}">{collection_state}</div>
    <p class="note">{escaped(summary["mode"])} · {escaped(summary["code_revision"])}
    · 수집 실패 요청 {collection_failure_count:,}</p>
  </section>
  <section class="panel third"><h2>데이터 품질</h2>
    <div class="hero {'ok' if quality_state == 'PASS' else ''}">{quality_state}</div>
    <p class="note">오류 행 {quality["error_rows"]:,} · 수집 실패 요청
    {collection_failure_count:,} · 종목 {quality["symbol_count"]:,}</p>
  </section>
  <section class="panel third"><h2>전략 성과</h2>
    <div class="hero" style="font-size:20px">{strategy_state}</div>
    <p class="note">산출물·방법론·벤치마크를 분리해 판정하며, 승격 eligible 전에는
    연구 후보로만 취급합니다.</p>
  </section>
  <section class="panel half"><h2>공급자 상태</h2>{providers}</section>
  <section class="panel half"><h2>품질 세부 지표</h2>
    <div class="metric"><span>Toss raw 수집 실패</span><strong>{toss["raw_failure_count"]:,}</strong></div>
    <div class="metric"><span>Toss adjusted 수집 실패</span><strong>{toss["adjusted_failure_count"]:,}</strong></div>
    <div class="metric"><span>중복 행</span><strong>{quality["duplicate_rows"]:,}</strong></div>
    <div class="metric"><span>유효성 오류 행</span><strong>{quality["invalid_rows"]:,}</strong></div>
    <div class="metric"><span>coverage 불일치 행</span><strong>{quality["coverage_mismatch_rows"]:,}</strong></div>
    <div class="metric"><span>조정 유형</span><strong>{escaped(", ".join(quality["adjustments"]))}</strong></div>
  </section>
  <section class="panel"><h2>전략 기준선 성과</h2>
    <div class="grid">{strategy_cards}</div>
    <p class="note">표시된 값은 재현 가능한 산출물의 관측치입니다. 실전 적합성은
    방법론 {escaped(strategy["methodology_state"])}, 벤치마크
    {escaped(strategy["benchmark_state"])}, 승격
    {escaped(strategy["promotion_state"])} 상태를 함께 확인해야 합니다.</p>
  </section>
</div>
<footer>이 HTML은 실행 산출물의 서명 대상입니다. 장기 이력은 BigQuery,
운영·품질·성과 추이는 Cloud Monitoring 대시보드에서 확인합니다.</footer>
</main></body></html>
"""

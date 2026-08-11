from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from toss_trading.cli.research_backtest import _total_return_manifest_ids
from toss_trading.research import PricePoint
from toss_trading.research.costs import ExecutionCostModel, load_execution_cost_model
from toss_trading.research.candidate_evaluation import (
    evaluate_hypothesis,
    evaluate_prospective_hypothesis,
)
from toss_trading.research.hypotheses import HypothesisLedger, load_research_policy
from toss_trading.data.universe import load_instrument_mappings
from toss_trading.research.instruments import (
    build_instrument_lifetime_index,
    observation_within_instrument_lifetime,
    validate_point_in_time_dates,
)


def _candidate_summary(
    hypothesis: dict[str, Any],
    result: dict[str, Any],
    *,
    activity: str = "evaluated",
) -> dict[str, Any]:
    gates = result.get("gates") if isinstance(result.get("gates"), dict) else {}
    statistical = (
        result.get("statistical_test")
        if isinstance(result.get("statistical_test"), dict)
        else {}
    )
    stress = (
        result.get("cost_stress")
        if isinstance(result.get("cost_stress"), dict)
        else {}
    )
    config = hypothesis.get("config") if isinstance(hypothesis.get("config"), dict) else {}
    prospective = (
        result.get("prospective_observation")
        if isinstance(result.get("prospective_observation"), dict)
        else {}
    )
    return {
        "hypothesis_id": str(hypothesis["hypothesis_id"]),
        "activity": activity,
        "thesis": str(hypothesis.get("thesis", "")),
        "state": result.get("state"),
        "historical_screen_passed": result.get("historical_screen_passed") is True,
        "failed_gates": sorted(
            name for name, passed in gates.items() if passed is not True
        ),
        "annualized_mean_excess": statistical.get("annualized_mean_excess"),
        "adjusted_p_value": statistical.get("bonferroni_adjusted_p_value"),
        "cost_stress_annualized_mean_excess": stress.get(
            "annualized_mean_excess"
        ),
        "prospective_state": prospective.get("state"),
        "config": {
            "candidate_symbols": config.get("candidate_symbols"),
            "lookback_trading_days": config.get("lookback_trading_days"),
            "skip_recent_trading_days": config.get("skip_recent_trading_days"),
            "top_k": config.get("top_k"),
            "minimum_absolute_momentum": config.get(
                "minimum_absolute_momentum"
            ),
        },
    }


def _load_points(parquet: list[str]) -> tuple[list[PricePoint], set[str]]:
    try:
        import duckdb
    except ImportError as exc:
        raise RuntimeError(
            "DuckDB is required; install toss-trading[research]"
        ) from exc
    connection = duckdb.connect()
    try:
        rows = connection.execute(
            """
            SELECT
              CAST(exchange_local_date AS VARCHAR) AS date,
              symbol,
              CAST(close AS VARCHAR) AS total_return_index,
              CAST(available_at AS VARCHAR) AS available_at,
              filename
            FROM read_parquet(?, union_by_name = true, filename = true)
            WHERE adjustment = 'total_return'
            ORDER BY exchange_local_date, symbol
            """,
            [parquet],
        ).fetchall()
    finally:
        connection.close()
    return (
        [
            PricePoint(
                date=str(row[0]),
                symbol=str(row[1]),
                total_return_index=str(row[2]),
                available_at=str(row[3]),
            )
            for row in rows
        ],
        {str(row[4]) for row in rows},
    )


def evaluate_registered_hypotheses(
    *,
    policy_path: str | Path,
    ledger_dir: str | Path,
    output_dir: str | Path,
    run_id: str,
    code_revision: str,
    data_manifest_ids: list[str],
    points: list[PricePoint],
    execution_cost_model: ExecutionCostModel,
    evaluation_cadence: str = "weekly",
) -> dict[str, Any]:
    if not code_revision.strip() or code_revision.lower() == "unknown":
        raise ValueError("an immutable code revision is required")
    policy = load_research_policy(policy_path)
    if evaluation_cadence not in {"daily", "weekly"}:
        raise ValueError("evaluation_cadence must be daily or weekly")
    ledger = HypothesisLedger(ledger_dir)
    registered = ledger.registered()
    family_size = max(1, len(registered))
    evaluated: list[str] = []
    reused: list[str] = []
    carried_forward: list[str] = []
    qualified: list[str] = []
    failed: list[str] = []
    candidate_results: list[dict[str, Any]] = []
    evaluated_at = datetime.now(timezone.utc).isoformat()
    for hypothesis in registered:
        hypothesis_id = str(hypothesis["hypothesis_id"])
        destination = ledger.evaluations / hypothesis_id / f"{run_id}.json"
        if destination.is_file():
            reused.append(hypothesis_id)
            existing = json.loads(destination.read_text(encoding="utf-8"))
            candidate_results.append(
                _candidate_summary(hypothesis, existing, activity="reused")
            )
            if existing.get("historical_screen_passed") is True:
                qualified.append(hypothesis_id)
            continue
        protocol = ledger.prospective_protocol(hypothesis_id)
        if evaluation_cadence == "daily" and protocol is None:
            prior_paths = sorted(
                ledger.evaluations.joinpath(hypothesis_id).glob("*.json"),
                key=lambda path: path.name,
                reverse=True,
            )
            if prior_paths:
                prior = json.loads(prior_paths[0].read_text(encoding="utf-8"))
                if prior.get("historical_screen_passed") is not True:
                    carried_forward.append(hypothesis_id)
                    candidate_results.append(
                        _candidate_summary(
                            hypothesis,
                            prior,
                            activity="carried_forward",
                        )
                    )
                    continue
        try:
            if protocol is None:
                result = evaluate_hypothesis(
                    hypothesis,
                    points=points,
                    policy=policy,
                    family_size=family_size,
                    data_manifest_ids=data_manifest_ids,
                    code_revision=code_revision,
                    run_id=run_id,
                    evaluated_at=evaluated_at,
                    execution_cost_model=execution_cost_model,
                )
                if result.get("historical_screen_passed") is True:
                    ledger.register_prospective_protocol(
                        hypothesis=hypothesis,
                        registered_at=evaluated_at,
                        historical_cutoff=str(result["historical_cutoff"]),
                        policy=policy,
                        output_dir=(
                            Path(output_dir).parent / "prospective_protocols"
                        ),
                    )
                    result["prospective_observation"] = {
                        "state": "registered",
                        "observed_trading_days": 0,
                        "minimum_trading_days": int(
                            policy["minimum_prospective_trading_days"]
                        ),
                        "observed_rebalances": 0,
                        "minimum_rebalances": int(
                            policy["minimum_prospective_rebalances"]
                        ),
                        "metrics_revealed": False,
                    }
                    result["paper_stage"] = {
                        "state": "not_started",
                        "reason": "prospective_oos_not_complete",
                    }
                    result["shadow_stage"] = {"state": "not_started"}
            else:
                result = evaluate_prospective_hypothesis(
                    hypothesis,
                    protocol=protocol,
                    points=points,
                    policy=policy,
                    family_size=family_size,
                    data_manifest_ids=data_manifest_ids,
                    code_revision=code_revision,
                    run_id=run_id,
                    evaluated_at=evaluated_at,
                    execution_cost_model=execution_cost_model,
                )
        except Exception as exc:
            result = {
                "evaluation_schema": "historical-candidate-evaluation-v1",
                "evaluated_at": evaluated_at,
                "state": "evaluation_failed",
                "historical_screen_passed": False,
                "promotion_authorized": False,
                "execution_authorized": False,
                "failure_reason_type": type(exc).__name__,
                "data_manifest_ids": sorted(set(data_manifest_ids)),
                "code_revision": code_revision,
            }
            failed.append(hypothesis_id)
        ledger.record_evaluation(
            hypothesis_id=hypothesis_id,
            run_id=run_id,
            result=result,
            output_dir=output_dir,
        )
        candidate_results.append(
            _candidate_summary(hypothesis, result, activity="evaluated")
        )
        evaluated.append(hypothesis_id)
        if result.get("historical_screen_passed") is True:
            qualified.append(hypothesis_id)
    return {
        "schema_version": "autonomous-candidate-evaluation-summary-v1",
        "state": "completed",
        "registered_count": len(registered),
        "evaluated": evaluated,
        "reused": reused,
        "carried_forward": carried_forward,
        "historically_qualified": qualified,
        "evaluation_failed": failed,
        "candidate_results": sorted(
            candidate_results, key=lambda item: item["hypothesis_id"]
        ),
        "promotion_authorized": False,
        "execution_authorized": False,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Evaluate all immutable strategy hypotheses with multiple-testing and "
            "cost-stress gates."
        )
    )
    parser.add_argument("--policy", required=True)
    parser.add_argument("--ledger-dir", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--parquet", action="append", required=True)
    parser.add_argument("--manifest-root", required=True)
    parser.add_argument(
        "--code-revision",
        default=os.environ.get("FOUNDATION_CODE_REVISION", "unknown"),
    )
    parser.add_argument("--result", required=True)
    parser.add_argument("--cost-calibration", required=True)
    parser.add_argument("--portfolio-notional-usd", type=float)
    parser.add_argument("--instrument-master")
    parser.add_argument(
        "--cadence",
        choices=("daily", "weekly"),
        default="weekly",
        help=(
            "Daily evaluates newly registered and prospective candidates; weekly "
            "rechecks the complete registered family."
        ),
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    points, used_parquet_files = _load_points(args.parquet)
    excluded_lifetime_points: list[PricePoint] = []
    if args.instrument_master:
        mappings = load_instrument_mappings(args.instrument_master)
        lifetimes = build_instrument_lifetime_index(mappings)
        eligible_points = []
        for point in points:
            if observation_within_instrument_lifetime(
                lifetimes,
                point.symbol,
                point.date,
            ):
                eligible_points.append(point)
            else:
                excluded_lifetime_points.append(point)
        points = eligible_points
        validate_point_in_time_dates(
            mappings,
            ((point.symbol, point.date) for point in points),
        )
    manifest_ids = _total_return_manifest_ids(
        args.manifest_root,
        used_parquet_files=used_parquet_files,
    )
    result = evaluate_registered_hypotheses(
        policy_path=args.policy,
        ledger_dir=args.ledger_dir,
        output_dir=args.output_dir,
        run_id=args.run_id,
        code_revision=args.code_revision,
        data_manifest_ids=manifest_ids,
        points=points,
        execution_cost_model=load_execution_cost_model(
            args.cost_calibration,
            portfolio_notional_usd=args.portfolio_notional_usd,
        ),
        evaluation_cadence=args.cadence,
    )
    result["excluded_outside_instrument_lifetime_rows"] = len(
        excluded_lifetime_points
    )
    result["excluded_outside_instrument_lifetime_symbols"] = sorted(
        {point.symbol for point in excluded_lifetime_points}
    )
    destination = Path(args.result)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + ".tmp")
    temporary.write_text(
        json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(destination)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

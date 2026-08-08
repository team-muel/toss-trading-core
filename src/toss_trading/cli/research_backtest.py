from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from toss_trading.research import (
    DualMomentumConfig,
    PricePoint,
    run_dual_momentum_backtest,
)
from toss_trading.research.backtest import write_experiment_record
from toss_trading.research.prospective import load_collection_observations
from toss_trading.research.costs import (
    load_execution_cost_model,
)
from toss_trading.data.universe import load_instrument_mappings
from toss_trading.research.instruments import (
    build_instrument_lifetime_index,
    observation_within_instrument_lifetime,
    validate_point_in_time_dates,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run the pre-registered broad-ETF dual-momentum baseline."
    )
    parser.add_argument("--parquet", action="append", required=True)
    parser.add_argument("--candidate", action="append", required=True)
    parser.add_argument("--cash-symbol", default="SGOV")
    parser.add_argument("--lookback-days", type=int, default=252)
    parser.add_argument("--skip-days", type=int, default=21)
    parser.add_argument("--top-k", type=int, default=1)
    parser.add_argument("--minimum-momentum", type=float, default=0.0)
    parser.add_argument("--portfolio-notional-usd", type=float)
    parser.add_argument("--instrument-master")
    parser.add_argument(
        "--cost-calibration",
        required=True,
        help="Sanitized Toss commission snapshot plus notional slippage tiers.",
    )
    parser.add_argument("--walk-forward-train-days", type=int, default=504)
    parser.add_argument("--walk-forward-test-days", type=int, default=126)
    parser.add_argument("--data-manifest-id", action="append", default=[])
    parser.add_argument(
        "--manifest-root",
        help=(
            "Discover silver total-return manifest IDs from this immutable "
            "run catalog."
        ),
    )
    parser.add_argument(
        "--align-common-history",
        action="store_true",
        help=(
            "Use only dates where every candidate and the cash symbol have "
            "verified total-return observations."
        ),
    )
    parser.add_argument("--benchmark", action="append", default=["SPY buy-and-hold"])
    parser.add_argument(
        "--validation-protocol",
        help=(
            "Optional pre-registered prospective OOS protocol. Without it, "
            "the experiment remains a historical diagnostic."
        ),
    )
    parser.add_argument("--output-root", default="research_data")
    parser.add_argument(
        "--prospective-observation-ledger",
        action="append",
        default=[],
        help="Append-only collection evidence used to validate prospective continuity.",
    )
    parser.add_argument(
        "--code-revision",
        default=os.environ.get("FOUNDATION_CODE_REVISION", "unknown"),
    )
    return parser


def _total_return_manifest_ids(
    root: str | None,
    *,
    used_parquet_files: set[str],
) -> list[str]:
    if root is None:
        return []
    manifest_root = Path(root)
    lake_root = manifest_root.parent.parent.resolve()
    used_relative_paths = set()
    for value in used_parquet_files:
        resolved = Path(value).resolve()
        try:
            used_relative_paths.add(resolved.relative_to(lake_root).as_posix())
        except ValueError as exc:
            raise ValueError(
                f"backtest input is outside the manifest lake: {resolved}"
            ) from exc
    manifest_ids = []
    matched_relative_paths = set()
    for path in sorted(manifest_root.glob("*.json")):
        payload = json.loads(path.read_text(encoding="utf-8"))
        relative_path = str(payload.get("relative_path", "")).replace("\\", "/")
        manifest_id = payload.get("manifest_id")
        if (
            payload.get("layer") == "silver"
            and "/adjustment=total_return/" in f"/{relative_path}"
            and relative_path in used_relative_paths
            and isinstance(manifest_id, str)
            and manifest_id.strip()
        ):
            manifest_ids.append(manifest_id.strip())
            matched_relative_paths.add(relative_path)
    unmatched = sorted(used_relative_paths - matched_relative_paths)
    if unmatched:
        raise ValueError(
            f"backtest inputs lack exact silver manifests: {unmatched}"
        )
    return sorted(set(manifest_ids))


def _align_common_history(
    points: list[PricePoint],
    *,
    required_symbols: set[str],
) -> list[PricePoint]:
    symbols_by_date: dict[str, set[str]] = {}
    for point in points:
        symbols_by_date.setdefault(point.date, set()).add(point.symbol)
    complete_dates = {
        date
        for date, symbols in symbols_by_date.items()
        if required_symbols <= symbols
    }
    if not complete_dates:
        raise ValueError("no common total-return history for configured symbols")
    return [point for point in points if point.date in complete_dates]


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
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
            [args.parquet],
        ).fetchall()
    finally:
        connection.close()
    excluded_lifetime_rows: list[tuple] = []
    if args.instrument_master:
        mappings = load_instrument_mappings(args.instrument_master)
        lifetimes = build_instrument_lifetime_index(mappings)
        eligible_rows = []
        for row in rows:
            if observation_within_instrument_lifetime(
                lifetimes,
                str(row[1]),
                str(row[0]),
            ):
                eligible_rows.append(row)
            else:
                excluded_lifetime_rows.append(row)
        rows = eligible_rows
        validate_point_in_time_dates(
            mappings,
            ((str(row[1]), str(row[0])) for row in rows),
        )
    points = [
        PricePoint(
            date=str(row[0]),
            symbol=str(row[1]),
            total_return_index=str(row[2]),
            available_at=str(row[3]),
        )
        for row in rows
    ]
    used_parquet_files = {str(row[4]) for row in rows}
    candidate_symbols = tuple(
        dict.fromkeys(symbol.strip().upper() for symbol in args.candidate)
    )
    cash_symbol = args.cash_symbol.strip().upper()
    if args.align_common_history:
        points = _align_common_history(
            points,
            required_symbols=set(candidate_symbols) | {cash_symbol},
        )
    manifest_ids = sorted(
        {
            *(item.strip() for item in args.data_manifest_id if item.strip()),
            *_total_return_manifest_ids(
                args.manifest_root,
                used_parquet_files=used_parquet_files,
            ),
        }
    )
    if not manifest_ids:
        raise ValueError(
            "at least one total-return data manifest ID is required"
        )
    config = DualMomentumConfig(
            candidate_symbols=candidate_symbols,
            cash_symbol=cash_symbol,
            lookback_trading_days=args.lookback_days,
            skip_recent_trading_days=args.skip_days,
            top_k=args.top_k,
            minimum_absolute_momentum=args.minimum_momentum,
            walk_forward_train_days=args.walk_forward_train_days,
            walk_forward_test_days=args.walk_forward_test_days,
        )
    cost_model = load_execution_cost_model(
        args.cost_calibration,
        portfolio_notional_usd=args.portfolio_notional_usd,
    )
    result = run_dual_momentum_backtest(
        points,
        config,
        execution_cost_model=cost_model,
    )
    validation_protocol = None
    if args.validation_protocol:
        validation_protocol = json.loads(
            Path(args.validation_protocol).read_text(encoding="utf-8")
        )
    record = write_experiment_record(
        result,
        output_root=args.output_root,
        data_manifest_ids=manifest_ids,
        code_revision=args.code_revision,
        benchmark_names=args.benchmark,
        validation_protocol=validation_protocol,
        prospective_observations=load_collection_observations(
            args.prospective_observation_ledger
        ),
    )
    print(
        json.dumps(
            {
                "experiment_record": str(record),
                "metrics": result.metrics,
                "benchmark_metrics": result.benchmark_metrics,
                "common_history_start": result.equity_curve[0][0],
                "common_history_end": result.equity_curve[-1][0],
                "walk_forward_folds": len(result.walk_forward_folds),
                "validation_protocol": (
                    validation_protocol.get("schema_version")
                    if validation_protocol
                    else None
                ),
                "rebalances": len(result.rebalances),
                "excluded_outside_instrument_lifetime_rows": len(
                    excluded_lifetime_rows
                ),
                "excluded_outside_instrument_lifetime_symbols": sorted(
                    {str(row[1]) for row in excluded_lifetime_rows}
                ),
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

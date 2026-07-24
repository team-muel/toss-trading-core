from __future__ import annotations

import argparse
import json
import os

from toss_trading.research import (
    DualMomentumConfig,
    PricePoint,
    run_dual_momentum_backtest,
)
from toss_trading.research.backtest import write_experiment_record


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
    parser.add_argument("--commission-bps", type=float, default=1.5)
    parser.add_argument("--slippage-bps", type=float, default=2.0)
    parser.add_argument("--data-manifest-id", action="append", required=True)
    parser.add_argument("--benchmark", action="append", default=["SPY buy-and-hold"])
    parser.add_argument("--output-root", default="research_data")
    parser.add_argument(
        "--code-revision",
        default=os.environ.get("FOUNDATION_CODE_REVISION", "unknown"),
    )
    return parser


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
              CAST(available_at AS VARCHAR) AS available_at
            FROM read_parquet(?, union_by_name = true)
            WHERE adjustment = 'total_return'
            ORDER BY exchange_local_date, symbol
            """,
            [args.parquet],
        ).fetchall()
    finally:
        connection.close()
    points = [
        PricePoint(
            date=str(row[0]),
            symbol=str(row[1]),
            total_return_index=str(row[2]),
            available_at=str(row[3]),
        )
        for row in rows
    ]
    result = run_dual_momentum_backtest(
        points,
        DualMomentumConfig(
            candidate_symbols=tuple(
                dict.fromkeys(symbol.strip().upper() for symbol in args.candidate)
            ),
            cash_symbol=args.cash_symbol.strip().upper(),
            lookback_trading_days=args.lookback_days,
            skip_recent_trading_days=args.skip_days,
            top_k=args.top_k,
            minimum_absolute_momentum=args.minimum_momentum,
            commission_bps=args.commission_bps,
            slippage_bps=args.slippage_bps,
        ),
    )
    record = write_experiment_record(
        result,
        output_root=args.output_root,
        data_manifest_ids=args.data_manifest_id,
        code_revision=args.code_revision,
        benchmark_names=args.benchmark,
    )
    print(
        json.dumps(
            {
                "experiment_record": str(record),
                "metrics": result.metrics,
                "rebalances": len(result.rebalances),
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

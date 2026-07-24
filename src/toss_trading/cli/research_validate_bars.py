from __future__ import annotations

import argparse
import json


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Validate normalized Parquet market bars before backtesting."
    )
    parser.add_argument(
        "--parquet",
        required=True,
        help="A DuckDB-compatible Parquet path or glob.",
    )
    parser.add_argument("--expected-symbol", action="append", default=[])
    parser.add_argument("--require-adjustment", action="append", default=[])
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        import duckdb
    except ImportError as exc:
        raise RuntimeError(
            "DuckDB is required for Parquet validation; install toss-trading[research]"
        ) from exc

    connection = duckdb.connect()
    try:
        connection.read_parquet(
            args.parquet,
            union_by_name=True,
        ).create_view("bars")
        summary_rows = connection.execute(
            """
            SELECT
              source,
              adjustment,
              quality_flag,
              COUNT(*) AS rows,
              COUNT(DISTINCT symbol) AS symbols,
              MIN(exchange_local_date)::VARCHAR AS first_date,
              MAX(exchange_local_date)::VARCHAR AS last_date
            FROM bars
            GROUP BY source, adjustment, quality_flag
            ORDER BY source, adjustment, quality_flag
            """
        ).fetchall()
        duplicate_count = connection.execute(
            """
            SELECT COALESCE(SUM(row_count - 1), 0)
            FROM (
              SELECT COUNT(*) AS row_count
              FROM bars
              GROUP BY symbol, event_time_utc, interval, source, adjustment
              HAVING COUNT(*) > 1
            )
            """
        ).fetchone()[0]
        invalid_count = connection.execute(
            """
            SELECT COUNT(*)
            FROM bars
            WHERE open <= 0
               OR high <= 0
               OR low <= 0
               OR close <= 0
               OR volume < 0
               OR high < GREATEST(open, low, close)
               OR low > LEAST(open, high, close)
               OR available_at < event_time_utc
               OR (adjustment = 'total_return' AND quality_flag <> 'ok')
            """
        ).fetchone()[0]
        symbols = {
            row[0]
            for row in connection.execute(
                "SELECT DISTINCT symbol FROM bars ORDER BY symbol"
            ).fetchall()
        }
        adjustments = {
            row[0]
            for row in connection.execute(
                "SELECT DISTINCT adjustment FROM bars ORDER BY adjustment"
            ).fetchall()
        }
    finally:
        connection.close()

    expected_symbols = {symbol.strip().upper() for symbol in args.expected_symbol}
    required_adjustments = {
        adjustment.strip() for adjustment in args.require_adjustment
    }
    missing_symbols = sorted(expected_symbols - symbols)
    missing_adjustments = sorted(required_adjustments - adjustments)
    ok = not (
        duplicate_count
        or invalid_count
        or missing_symbols
        or missing_adjustments
    )
    result = {
        "adjustments": sorted(adjustments),
        "duplicate_rows": duplicate_count,
        "invalid_rows": invalid_count,
        "missing_adjustments": missing_adjustments,
        "missing_symbols": missing_symbols,
        "ok": ok,
        "summary": [
            {
                "source": source,
                "adjustment": adjustment,
                "quality_flag": quality_flag,
                "rows": rows,
                "symbols": symbol_count,
                "first_date": first_date,
                "last_date": last_date,
            }
            for (
                source,
                adjustment,
                quality_flag,
                rows,
                symbol_count,
                first_date,
                last_date,
            ) in summary_rows
        ],
        "symbols": sorted(symbols),
    }
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())

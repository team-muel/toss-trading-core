from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Iterable

import yaml


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
    parser.add_argument(
        "--cross-provider-source",
        action="append",
        default=[],
        help="Supply exactly two raw-bar sources that must have auditable overlap.",
    )
    parser.add_argument("--data-source-policy")
    parser.add_argument("--max-cross-provider-close-error-bps", type=float)
    parser.add_argument("--volume-warning-ratio", type=float)
    return parser


def _cross_provider_policy(path: str | None) -> tuple[float, float]:
    if path is None:
        return 100.0, 0.20
    payload = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    try:
        policy = payload["providers"]["toss_candles"]["automation"][
            "raw_cross_validation"
        ]
        return (
            float(policy["maximum_close_error_bps"]),
            float(policy["volume_difference_warning_ratio"]),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("data-source policy lacks raw cross-validation limits") from exc


def validate_parquet(
    parquet: str | list[str],
    *,
    expected_symbols: Iterable[str] = (),
    required_adjustments: Iterable[str] = (),
    cross_provider_sources: Iterable[str] = (),
    max_cross_provider_close_error_bps: float = 100.0,
    volume_warning_ratio: float = 0.20,
) -> dict:
    try:
        import duckdb
    except ImportError as exc:
        raise RuntimeError(
            "DuckDB is required for Parquet validation; install toss-trading[research]"
        ) from exc

    provider_pair = tuple(item.strip() for item in cross_provider_sources if item.strip())
    if provider_pair and len(provider_pair) != 2:
        raise ValueError("cross-provider validation requires exactly two sources")
    if max_cross_provider_close_error_bps < 0 or volume_warning_ratio < 0:
        raise ValueError("cross-provider tolerances must be nonnegative")

    connection = duckdb.connect()
    try:
        connection.read_parquet(
            parquet,
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
            WHERE symbol IS NULL
               OR TRIM(symbol) = ''
               OR event_time_utc IS NULL
               OR available_at IS NULL
               OR exchange_local_date IS NULL
               OR interval IS NULL
               OR source IS NULL
               OR TRIM(source) = ''
               OR adjustment IS NULL
               OR currency IS NULL
               OR TRIM(currency) = ''
               OR quality_flag IS NULL
               OR raw_manifest_id IS NULL
               OR TRIM(raw_manifest_id) = ''
               OR open IS NULL
               OR high IS NULL
               OR low IS NULL
               OR close IS NULL
               OR volume IS NULL
               OR interval NOT IN ('1d', '1h', '1m')
               OR adjustment NOT IN ('raw', 'split_adjusted', 'total_return')
               OR quality_flag NOT IN ('ok', 'estimated', 'stale', 'blocked')
               OR open <= 0
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
        coverage_rows = connection.execute(
            """
            SELECT
              candidate.source,
              candidate.adjustment,
              (
                SELECT COUNT(*)
                FROM bars adjusted
                WHERE adjusted.source = candidate.source
                  AND adjusted.adjustment = candidate.adjustment
                  AND NOT EXISTS (
                    SELECT 1
                    FROM bars raw
                    WHERE raw.source = adjusted.source
                      AND raw.adjustment = 'raw'
                      AND raw.symbol = adjusted.symbol
                      AND raw.event_time_utc = adjusted.event_time_utc
                      AND raw.interval = adjusted.interval
                  )
              ) AS missing_raw,
              (
                SELECT COUNT(*)
                FROM bars raw
                WHERE raw.source = candidate.source
                  AND raw.adjustment = 'raw'
                  AND NOT EXISTS (
                    SELECT 1
                    FROM bars adjusted
                    WHERE adjusted.source = raw.source
                      AND adjusted.adjustment = candidate.adjustment
                      AND adjusted.symbol = raw.symbol
                      AND adjusted.event_time_utc = raw.event_time_utc
                      AND adjusted.interval = raw.interval
                  )
              ) AS missing_adjusted
            FROM (
              SELECT DISTINCT source, adjustment
              FROM bars
              WHERE adjustment <> 'raw'
            ) AS candidate
            ORDER BY candidate.source, candidate.adjustment
            """
        ).fetchall()
        symbol_rows = connection.execute(
            """
            SELECT
              source,
              adjustment,
              symbol,
              COUNT(*) AS rows,
              MIN(exchange_local_date)::VARCHAR AS first_date,
              MAX(exchange_local_date)::VARCHAR AS last_date
            FROM bars
            GROUP BY source, adjustment, symbol
            ORDER BY source, adjustment, symbol
            """
        ).fetchall()
        provider_cross_check = None
        if provider_pair:
            source_a, source_b = provider_pair
            cross_rows = connection.execute(
                """
                SELECT
                  a.symbol,
                  COUNT(*) AS overlap_rows,
                  MAX(ABS(a.close / b.close - 1.0) * 10000.0) AS max_close_error_bps,
                  AVG(ABS(a.close / b.close - 1.0) * 10000.0) AS mean_close_error_bps,
                  SUM(CASE WHEN ABS(a.close / b.close - 1.0) * 10000.0 > ? THEN 1 ELSE 0 END)
                    AS close_outlier_rows,
                  SUM(
                    CASE
                      WHEN GREATEST(a.volume, b.volume) > 0
                       AND ABS(a.volume - b.volume) / GREATEST(a.volume, b.volume) > ?
                      THEN 1 ELSE 0
                    END
                  ) AS volume_warning_rows
                FROM bars a
                JOIN bars b
                  ON b.symbol = a.symbol
                 AND b.exchange_local_date = a.exchange_local_date
                 AND b.interval = a.interval
                 AND b.adjustment = 'raw'
                 AND b.source = ?
                WHERE a.adjustment = 'raw'
                  AND a.source = ?
                GROUP BY a.symbol
                ORDER BY a.symbol
                """,
                [
                    max_cross_provider_close_error_bps,
                    volume_warning_ratio,
                    source_b,
                    source_a,
                ],
            ).fetchall()
            overlap_rows = sum(int(row[1]) for row in cross_rows)
            close_outlier_rows = sum(int(row[4]) for row in cross_rows)
            volume_warning_rows = sum(int(row[5]) for row in cross_rows)
            provider_cross_check = {
                "sources": [source_a, source_b],
                "state": (
                    "missing_overlap"
                    if not overlap_rows
                    else "failed"
                    if close_outlier_rows
                    else "warning"
                    if volume_warning_rows
                    else "ok"
                ),
                "overlap_rows": overlap_rows,
                "overlap_symbols": len(cross_rows),
                "close_outlier_rows": close_outlier_rows,
                "volume_warning_rows": volume_warning_rows,
                "max_close_error_bps_allowed": max_cross_provider_close_error_bps,
                "volume_warning_ratio": volume_warning_ratio,
                "symbol_summary": [
                    {
                        "symbol": row[0],
                        "overlap_rows": int(row[1]),
                        "max_close_error_bps": float(row[2]),
                        "mean_close_error_bps": float(row[3]),
                        "close_outlier_rows": int(row[4]),
                        "volume_warning_rows": int(row[5]),
                    }
                    for row in cross_rows
                ],
            }
    finally:
        connection.close()

    expected_symbol_set = {
        symbol.strip().upper() for symbol in expected_symbols
    }
    required_adjustment_set = {
        adjustment.strip() for adjustment in required_adjustments
    }
    missing_symbols = sorted(expected_symbol_set - symbols)
    missing_adjustments = sorted(required_adjustment_set - adjustments)
    coverage_mismatch_rows = sum(
        missing_raw + missing_adjusted
        for _, _, missing_raw, missing_adjusted in coverage_rows
    )
    ok = not (
        duplicate_count
        or invalid_count
        or coverage_mismatch_rows
        or missing_symbols
        or missing_adjustments
        or (
            provider_cross_check is not None
            and provider_cross_check["state"] in {"missing_overlap", "failed"}
        )
    )
    result = {
        "adjustments": sorted(adjustments),
        "coverage": [
            {
                "source": source,
                "adjustment": adjustment,
                "missing_raw": missing_raw,
                "missing_adjusted": missing_adjusted,
            }
            for source, adjustment, missing_raw, missing_adjusted in coverage_rows
        ],
        "coverage_mismatch_rows": coverage_mismatch_rows,
        "duplicate_rows": duplicate_count,
        "invalid_rows": invalid_count,
        "missing_adjustments": missing_adjustments,
        "missing_symbols": missing_symbols,
        "ok": ok,
        "provider_cross_check": provider_cross_check,
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
        "symbol_summary": [
            {
                "source": source,
                "adjustment": adjustment,
                "symbol": symbol,
                "rows": rows,
                "first_date": first_date,
                "last_date": last_date,
            }
            for source, adjustment, symbol, rows, first_date, last_date in symbol_rows
        ],
        "symbols": sorted(symbols),
    }
    return result


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    policy_close_bps, policy_volume_ratio = _cross_provider_policy(
        args.data_source_policy
    )
    result = validate_parquet(
        args.parquet,
        expected_symbols=args.expected_symbol,
        required_adjustments=args.require_adjustment,
        cross_provider_sources=args.cross_provider_source,
        max_cross_provider_close_error_bps=(
            args.max_cross_provider_close_error_bps
            if args.max_cross_provider_close_error_bps is not None
            else policy_close_bps
        ),
        volume_warning_ratio=(
            args.volume_warning_ratio
            if args.volume_warning_ratio is not None
            else policy_volume_ratio
        ),
    )
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

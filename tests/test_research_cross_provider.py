from __future__ import annotations

import importlib.util
import tempfile
import unittest
from pathlib import Path

from toss_trading.cli.research_validate_bars import validate_parquet


@unittest.skipUnless(
    importlib.util.find_spec("duckdb") is not None,
    "research extra is not installed",
)
class ResearchCrossProviderTests(unittest.TestCase):
    def _parquet(
        self,
        root: Path,
        *,
        second_close: float,
        historical_second_close: float | None = None,
    ) -> Path:
        import duckdb

        path = root / "bars.parquet"
        connection = duckdb.connect()
        try:
            connection.execute(
                """
                CREATE TABLE bars AS SELECT * FROM (VALUES
                    ('SPYM', TIMESTAMPTZ '2025-01-02 21:00:00+00', DATE '2025-01-02', '1d',
                     200.0, 206.0, 198.0, 200.0, 900.0, 'USD', 'toss-openapi', 'raw',
                     TIMESTAMPTZ '2025-01-02 22:00:00+00', 'ok', 'm0'),
                    ('SPYM', TIMESTAMPTZ '2025-01-02 21:00:00+00', DATE '2025-01-02', '1d',
                     200.0, 206.0, 99.0, ?, 950.0, 'USD', 'tiingo-eod', 'raw',
                     TIMESTAMPTZ '2025-01-02 22:00:00+00', 'ok', 'm0b'),
                    ('SPYM', TIMESTAMPTZ '2026-08-07 20:00:00+00', DATE '2026-08-07', '1d',
                     100.0, 103.0, 99.0, 100.0, 1000.0, 'USD', 'toss-openapi', 'raw',
                     TIMESTAMPTZ '2026-08-07 21:00:00+00', 'ok', 'm1'),
                    ('SPYM', TIMESTAMPTZ '2026-08-07 20:00:00+00', DATE '2026-08-07', '1d',
                     100.0, 103.0, 99.0, ?, 1100.0, 'USD', 'tiingo-eod', 'raw',
                     TIMESTAMPTZ '2026-08-07 21:00:00+00', 'ok', 'm2')
                  ) AS t(symbol,event_time_utc,exchange_local_date,interval,open,high,low,close,volume,currency,source,adjustment,available_at,quality_flag,raw_manifest_id)
                """,
                [
                    historical_second_close
                    if historical_second_close is not None
                    else 200.0,
                    second_close,
                ],
            )
            escaped_path = path.as_posix().replace("'", "''")
            connection.execute(f"COPY bars TO '{escaped_path}' (FORMAT PARQUET)")
        finally:
            connection.close()
        return path

    def test_matching_raw_prices_pass_with_volume_warning(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            result = validate_parquet(
                str(self._parquet(Path(tmp), second_close=100.0)),
                required_adjustments=["raw"],
                cross_provider_sources=["toss-openapi", "tiingo-eod"],
                volume_warning_ratio=0.05,
            )
            self.assertTrue(result["ok"])
            self.assertEqual(result["provider_cross_check"]["state"], "warning")
            self.assertEqual(result["provider_cross_check"]["overlap_rows"], 2)

    def test_recent_window_ignores_historical_split_regime_difference(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            result = validate_parquet(
                str(
                    self._parquet(
                        Path(tmp),
                        second_close=100.0,
                        historical_second_close=100.0,
                    )
                ),
                required_adjustments=["raw"],
                cross_provider_sources=["toss-openapi", "tiingo-eod"],
                cross_provider_lookback_calendar_days=45,
            )
            self.assertTrue(result["ok"])
            cross_check = result["provider_cross_check"]
            self.assertEqual(cross_check["overlap_rows"], 1)
            self.assertEqual(cross_check["comparison_first_date"], "2026-08-07")
            self.assertEqual(cross_check["comparison_last_date"], "2026-08-07")

    def test_large_close_disagreement_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            result = validate_parquet(
                str(self._parquet(Path(tmp), second_close=102.0)),
                required_adjustments=["raw"],
                cross_provider_sources=["toss-openapi", "tiingo-eod"],
            )
            self.assertFalse(result["ok"])
            self.assertEqual(result["provider_cross_check"]["state"], "failed")


if __name__ == "__main__":
    unittest.main()

import csv
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import date, timedelta
from io import StringIO
from pathlib import Path

from toss_trading.cli.research_backtest import main as backtest_main
from toss_trading.cli.research_ingest_bars import main as ingest_main


class ResearchCliTest(unittest.TestCase):
    def test_ingest_and_backtest_end_to_end(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            csv_path = root / "bars.csv"
            fieldnames = [
                "symbol",
                "event_time_utc",
                "available_at",
                "exchange_local_date",
                "interval",
                "open",
                "high",
                "low",
                "close",
                "volume",
                "currency",
                "session",
                "adjustment",
                "source_revision",
            ]
            rows = []
            current = date(2025, 1, 1)
            trading_day = 0
            while trading_day < 90:
                current += timedelta(days=1)
                if current.weekday() >= 5:
                    continue
                trading_day += 1
                values = {
                    "SPY": 100 * (1.001 ** trading_day),
                    "TLT": 100 * (0.999 ** trading_day),
                    "SGOV": 100 * (1.0001 ** trading_day),
                }
                for symbol, close in values.items():
                    rows.append(
                        {
                            "symbol": symbol,
                            "event_time_utc": f"{current.isoformat()}T21:00:00+00:00",
                            "available_at": f"{current.isoformat()}T21:05:00+00:00",
                            "exchange_local_date": current.isoformat(),
                            "interval": "1d",
                            "open": f"{close:.12f}",
                            "high": f"{close:.12f}",
                            "low": f"{close:.12f}",
                            "close": f"{close:.12f}",
                            "volume": "1000000",
                            "currency": "USD",
                            "session": "regular",
                            "adjustment": "total_return",
                            "source_revision": "test-v1",
                        }
                    )
            with csv_path.open("w", encoding="utf-8", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=fieldnames)
                writer.writeheader()
                writer.writerows(rows)

            ingest_output = StringIO()
            with redirect_stdout(ingest_output):
                self.assertEqual(
                    ingest_main(
                        [
                            "--input",
                            str(csv_path),
                            "--source",
                            "test-provider",
                            "--license-tag",
                            "test-only",
                            "--available-at",
                            "2025-05-31T00:00:00+00:00",
                            "--output-root",
                            str(root / "lake"),
                            "--code-revision",
                            "abc123",
                        ]
                    ),
                    0,
                )
            ingest_result = json.loads(ingest_output.getvalue())
            parquet = next((root / "lake" / "silver").rglob("*.parquet"))

            backtest_output = StringIO()
            with redirect_stdout(backtest_output):
                self.assertEqual(
                    backtest_main(
                        [
                            "--parquet",
                            str(parquet),
                            "--candidate",
                            "SPY",
                            "--candidate",
                            "TLT",
                            "--cash-symbol",
                            "SGOV",
                            "--lookback-days",
                            "20",
                            "--skip-days",
                            "2",
                            "--data-manifest-id",
                            ingest_result["normalized_manifest_ids"][0],
                            "--output-root",
                            str(root / "lake"),
                            "--code-revision",
                            "abc123",
                        ]
                    ),
                    0,
                )
            result = json.loads(backtest_output.getvalue())
            self.assertGreater(result["rebalances"], 0)
            self.assertGreater(result["metrics"]["total_return"], 0)
            self.assertTrue(Path(result["experiment_record"]).is_file())


if __name__ == "__main__":
    unittest.main()

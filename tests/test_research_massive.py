import json
import tempfile
import unittest
from pathlib import Path

from toss_trading.research.massive import (
    collect_common_stock_reference,
    collect_grouped_daily_history,
    normalize_grouped_daily,
)


class MassiveResearchTest(unittest.TestCase):
    def test_reference_paginates_and_never_returns_key(self):
        seen = []

        def fetch(url):
            seen.append(url)
            if len(seen) == 1:
                return {
                    "status": "OK",
                    "results": [
                        {"ticker": "AAA", "active": True, "type": "CS"}
                    ],
                    "next_url": "https://api.massive.com/v3/reference/tickers?cursor=next",
                }
            return {
                "status": "OK",
                "results": [
                    {"ticker": "BBB", "active": True, "type": "CS"}
                ],
            }

        result = collect_common_stock_reference("secret-key", fetch_json=fetch)
        self.assertEqual(result["symbol_count"], 2)
        self.assertEqual(result["page_count"], 2)
        self.assertNotIn("secret-key", json.dumps(result))
        self.assertTrue(all("apiKey=secret-key" in url for url in seen))

    def test_grouped_normalization_filters_non_common_stock_symbols(self):
        rows = normalize_grouped_daily(
            {
                "results": [
                    {"T": "AAA", "o": 10, "h": 12, "l": 9, "c": 11, "v": 100},
                    {"T": "ETF", "o": 20, "h": 21, "l": 19, "c": 20, "v": 200},
                ]
            },
            market_date="2026-08-10",
            allowed_symbols={"AAA"},
            retrieved_at="2026-08-11T00:00:00+00:00",
        )
        self.assertEqual([row["symbol"] for row in rows], ["AAA"])
        self.assertEqual(rows[0]["adjustment"], "split_adjusted")

    def test_grouped_collection_reuses_immutable_raw_date(self):
        with tempfile.TemporaryDirectory() as tmp:
            calls = []

            def fetch(url):
                calls.append(url)
                return {
                    "status": "OK",
                    "results": [
                        {"T": "AAA", "o": 10, "h": 12, "l": 9, "c": 11, "v": 100}
                    ],
                }

            rows, first = collect_grouped_daily_history(
                "secret-key",
                start_date="2026-08-10",
                through_date="2026-08-10",
                allowed_symbols={"AAA"},
                raw_directory=tmp,
                request_interval_seconds=0,
                fetch_json=fetch,
            )
            self.assertEqual(len(rows), 1)
            self.assertEqual(first["requested_date_count"], 1)
            raw = Path(tmp) / "2026-08-10.json"
            self.assertTrue(raw.exists())
            self.assertNotIn("secret-key", raw.read_text(encoding="utf-8"))

            _, second = collect_grouped_daily_history(
                "secret-key",
                start_date="2026-08-10",
                through_date="2026-08-10",
                allowed_symbols={"AAA"},
                raw_directory=tmp,
                request_interval_seconds=0,
                fetch_json=lambda _: self.fail("raw date should be reused"),
            )
            self.assertEqual(second["reused_date_count"], 1)


if __name__ == "__main__":
    unittest.main()

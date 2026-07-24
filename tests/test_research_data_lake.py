import importlib.util
import tempfile
import unittest
from pathlib import Path

from toss_trading.research import DataLake, MarketBar, validate_market_bars


def market_bar(
    *,
    symbol: str = "SPY",
    event_time: str = "2026-01-02T21:00:00+00:00",
    local_date: str = "2026-01-02",
    raw_manifest_id: str = "raw-1",
) -> MarketBar:
    return MarketBar(
        symbol=symbol,
        event_time_utc=event_time,
        available_at="2026-01-02T21:05:00+00:00",
        exchange_local_date=local_date,
        interval="1d",
        open="100",
        high="102",
        low="99",
        close="101",
        volume="1000000",
        currency="USD",
        session="regular",
        adjustment="total_return",
        source="test-provider",
        source_revision="v1",
        raw_manifest_id=raw_manifest_id,
    )


class ResearchDataLakeTest(unittest.TestCase):
    def test_raw_objects_and_manifests_are_content_addressed(self):
        with tempfile.TemporaryDirectory() as tmp:
            lake = DataLake(tmp)
            first = lake.store_raw(
                source="provider",
                dataset="daily-bars",
                body={"rows": [{"symbol": "SPY"}]},
                media_type="application/json",
                schema_version="v1",
                available_at="2026-01-02T21:05:00+00:00",
                request={"symbol": "SPY"},
                license_tag="test-only",
                code_revision="abc123",
                retrieved_at="2026-01-02T21:06:00+00:00",
            )
            second = lake.store_raw(
                source="provider",
                dataset="daily-bars",
                body={"rows": [{"symbol": "SPY"}]},
                media_type="application/json",
                schema_version="v1",
                available_at="2026-01-02T21:05:00+00:00",
                request={"symbol": "SPY"},
                license_tag="test-only",
                code_revision="abc123",
                retrieved_at="2026-01-02T21:06:00+00:00",
            )

            self.assertEqual(first.manifest_id, second.manifest_id)
            self.assertEqual(lake.manifests(layer="bronze"), [first])
            self.assertTrue((Path(tmp) / first.relative_path).is_file())

    def test_market_bar_quality_gates_reject_duplicates_and_bad_ohlc(self):
        row = market_bar()
        with self.assertRaisesRegex(ValueError, "duplicate"):
            validate_market_bars([row, row])
        invalid = MarketBar(**{**row.__dict__, "high": "98"})
        with self.assertRaisesRegex(ValueError, "high price"):
            validate_market_bars([invalid])

    @unittest.skipUnless(
        importlib.util.find_spec("duckdb") is not None,
        "research extra is not installed",
    )
    def test_normalized_market_bars_are_written_as_partitioned_parquet(self):
        with tempfile.TemporaryDirectory() as tmp:
            lake = DataLake(tmp)
            manifests = lake.write_market_bars(
                [market_bar()],
                code_revision="abc123",
                license_tag="test-only",
            )

            self.assertEqual(len(manifests), 1)
            manifest = manifests[0]
            self.assertEqual(manifest.layer, "silver")
            self.assertIn("interval=1d", manifest.relative_path)
            self.assertIn("adjustment=total_return", manifest.relative_path)
            self.assertTrue((Path(tmp) / manifest.relative_path).is_file())


if __name__ == "__main__":
    unittest.main()

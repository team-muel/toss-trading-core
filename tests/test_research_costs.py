from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from toss_trading.cli.research_export_cost_model import (
    build_cost_calibration,
    load_execution_cost_policy,
)
from toss_trading.research.costs import load_execution_cost_model


class ResearchCostTests(unittest.TestCase):
    def _calibration(self, ledger: Path, *, as_of: str = "2026-08-08") -> dict:
        policy = load_execution_cost_policy("config/research_validation_protocol.json")
        slippage = policy["slippage"]
        return build_cost_calibration(
            ledger,
            as_of=as_of,
            portfolio_notional_usd=float(policy["portfolio_notional_usd"]),
            slippage_source=str(slippage["source"]),
            slippage_tiers=list(slippage["tiers"]),
        )

    def _ledger(self, root: Path) -> Path:
        path = root / "ledger.sqlite3"
        connection = sqlite3.connect(path)
        try:
            connection.executescript(
                """
                CREATE TABLE commission_rate_schedule_snapshot (
                  ts TEXT, market_country TEXT, commission_rate_decimal TEXT,
                  start_date TEXT, end_date TEXT
                );
                CREATE TABLE execution_snapshot_log (
                  ts TEXT, order_id TEXT, snapshot_seq INTEGER,
                  cumulative_filled_amount_decimal TEXT,
                  cumulative_commission_decimal TEXT
                );
                INSERT INTO commission_rate_schedule_snapshot VALUES
                  ('2026-08-08T00:00:00Z', 'US', '0.1', '2026-01-01', '2026-12-31');
                INSERT INTO execution_snapshot_log VALUES
                  ('2026-08-08T00:00:00Z', 'order-1', 1, '1.0', '0'),
                  ('2026-08-08T00:01:00Z', 'order-1', 2, '1.6', '0');
                """
            )
            connection.commit()
        finally:
            connection.close()
        return path

    def test_account_schedule_is_normalized_and_trade_scale_changes_slippage(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            calibration = self._calibration(self._ledger(root))
            self.assertEqual(calibration["commission"]["normalized_bps"], 10.0)
            self.assertEqual(calibration["commission"]["execution_sample_count"], 1)
            self.assertNotIn("account_seq", json.dumps(calibration))
            self.assertNotIn("order_id", json.dumps(calibration))
            path = root / "calibration.json"
            path.write_text(json.dumps(calibration), encoding="utf-8")
            small = load_execution_cost_model(
                path, portfolio_notional_usd=10_000, as_of="2026-08-08"
            )
            large = load_execution_cost_model(
                path, portfolio_notional_usd=20_000, as_of="2026-08-08"
            )
            small_cost = small.estimate_rebalance(
                {"SGOV": 1.0}, {"SPY": 1.0}, equity_multiple=1.0
            )
            large_cost = large.estimate_rebalance(
                {"SGOV": 1.0}, {"SPY": 1.0}, equity_multiple=1.0
            )
            self.assertAlmostEqual(small_cost["total_fraction"], 0.0026)
            self.assertAlmostEqual(large_cost["total_fraction"], 0.0030)

    def test_expired_commission_schedule_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            calibration = self._calibration(self._ledger(root))
            calibration["commission"]["valid_through"] = "2026-08-08"
            path = root / "calibration.json"
            path.write_text(json.dumps(calibration), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "expired"):
                load_execution_cost_model(path, as_of="2026-08-09")


if __name__ == "__main__":
    unittest.main()

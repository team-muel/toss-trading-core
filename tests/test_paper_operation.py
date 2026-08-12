import tempfile
import unittest
from decimal import Decimal
from pathlib import Path

from toss_trading.paper import load_latest_raw_prices, run_paper_operation
from toss_trading.research.costs import (
    ExecutionCostModel,
    SlippageTier,
)


def artifact() -> dict:
    return {
        "strategy": "broad_etf_dual_momentum_v1",
        "code_revision": "a" * 40,
        "prospective_holdout": {
            "state": "collecting",
            "metrics_revealed": False,
        },
        "rebalances": [
            {
                "signal_date": "2026-07-31",
                "effective_date": "2026-08-03",
                "target_weights": {"GLD": 1.0},
            }
        ],
    }


def summary(run_id: str, market_date: str) -> dict:
    return {
        "run_id": run_id,
        "data_progress": {
            "state": "collected",
            "complete_through_date": market_date,
        },
        "strategy": {"code_revision": "a" * 40},
    }


def costs() -> ExecutionCostModel:
    return ExecutionCostModel(
        schema_version="execution-cost-model-v1",
        commission_bps=10,
        minimum_commission_usd=0,
        portfolio_notional_usd=10_000,
        slippage_tiers=(
            SlippageTier(1_000, 2),
            SlippageTier(10_000, 3),
            SlippageTier(None, 5),
        ),
        commission_source="test_schedule",
        slippage_source="test_policy",
        valid_through="2026-08-31",
    )


class PersistentPaperOperationTest(unittest.TestCase):
    def test_loads_latest_raw_price_at_cutoff(self):
        import duckdb

        with tempfile.TemporaryDirectory() as tmp:
            directory = (
                Path(tmp)
                / "silver/market_bars/source=tiingo-eod/interval=1d"
                / "adjustment=raw/year=2026"
            )
            directory.mkdir(parents=True)
            path = (directory / "part-test.parquet").as_posix()
            duckdb.connect().execute(
                """
                COPY (
                  SELECT * FROM (VALUES
                    ('GLD', '2026-08-10', '2026-08-11T00:00:00+00:00', '100', 'ok'),
                    ('GLD', '2026-08-11', '2026-08-12T00:00:00+00:00', '101', 'ok')
                  ) AS bars(symbol, exchange_local_date, available_at, close, quality_flag)
                ) TO ? (FORMAT PARQUET)
                """,
                [path],
            )
            prices = load_latest_raw_prices(
                tmp,
                through_date="2026-08-10",
                symbols={"GLD"},
            )
            self.assertEqual(prices, {"GLD": Decimal("100")})

    def test_applies_published_target_once_and_marks_future_runs(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            first = run_paper_operation(
                artifact=artifact(),
                source_summary=summary("daily-1", "2026-08-10"),
                prices={"GLD": Decimal("100")},
                paper_db_path=root / "paper.sqlite",
                planner_ledger_path=root / "planner.sqlite",
                cost_model=costs(),
            )
            self.assertFalse(first["live_orders_enabled"])
            self.assertFalse(
                first["evidence"]["eligible_for_strategy_promotion"]
            )
            self.assertEqual(first["evidence"]["reason"], "infrastructure_validation_only")
            self.assertTrue(first["rebalance"]["applied_this_run"])
            self.assertEqual(len(first["orders"]), 1)
            self.assertEqual(first["orders"][0]["filled"]["status"], "paper_filled")
            self.assertEqual(first["reconciliation"]["status"], "ok")

            replay = run_paper_operation(
                artifact=artifact(),
                source_summary=summary("daily-1", "2026-08-10"),
                prices={"GLD": Decimal("100")},
                paper_db_path=root / "paper.sqlite",
                planner_ledger_path=root / "planner.sqlite",
                cost_model=costs(),
            )
            self.assertEqual(replay, first)

            next_day = run_paper_operation(
                artifact=artifact(),
                source_summary=summary("daily-2", "2026-08-11"),
                prices={"GLD": Decimal("101")},
                paper_db_path=root / "paper.sqlite",
                planner_ledger_path=root / "planner.sqlite",
                cost_model=costs(),
            )
            self.assertFalse(next_day["rebalance"]["applied_this_run"])
            self.assertEqual(next_day["orders"], [])
            self.assertGreater(
                Decimal(next_day["after"]["nav_decimal"]),
                Decimal(first["after"]["nav_decimal"]),
            )

    def test_rejects_revision_mismatch(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = summary("daily-1", "2026-08-10")
            source["strategy"]["code_revision"] = "b" * 40
            with self.assertRaisesRegex(ValueError, "revision"):
                run_paper_operation(
                    artifact=artifact(),
                    source_summary=source,
                    prices={"GLD": Decimal("100")},
                    paper_db_path=Path(tmp) / "paper.sqlite",
                    planner_ledger_path=Path(tmp) / "planner.sqlite",
                    cost_model=costs(),
                )


if __name__ == "__main__":
    unittest.main()

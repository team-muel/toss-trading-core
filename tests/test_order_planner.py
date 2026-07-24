import unittest

from toss_trading.account import AccountLedger
from toss_trading.engines import Signal
from toss_trading.execution import OrderPlanner
from toss_trading.risk import RiskDecision


def signal(symbol: str = "SPY") -> Signal:
    return Signal(
        engine="broad_momentum",
        symbol_or_pair=symbol,
        side="BUY",
        raw_score=0.1,
        adjusted_score=0.1,
        target_weight=1.0,
        expected_max_loss=10.0,
        reason_code="top_positive_momentum",
    )


class OrderPlannerTest(unittest.TestCase):
    def setUp(self):
        self.ledger = AccountLedger()
        self.ledger.init_schema()
        self.planner = OrderPlanner()

    def tearDown(self):
        self.ledger.close()

    def test_plan_requires_an_approved_risk_decision(self):
        with self.assertRaisesRegex(ValueError, "risk decision rejected"):
            self.planner.create_plan(
                signal(),
                {"client_order_id": "cid-1", "qty": 1},
                risk_decision=RiskDecision(False, "reconciliation blocked"),
                ledger=self.ledger,
                account_seq="1",
                allowed_symbols={"SPY"},
            )

    def test_plan_requires_approved_universe_and_reserves_id(self):
        with self.assertRaisesRegex(ValueError, "approved universe"):
            self.planner.create_plan(
                signal("QQQ"),
                {"client_order_id": "cid-2", "qty": 1},
                risk_decision=RiskDecision(True, "approved"),
                ledger=self.ledger,
                account_seq="1",
                allowed_symbols={"SPY"},
            )
        plan = self.planner.create_plan(
            signal(),
            {"client_order_id": "cid-3", "qty": 1},
            risk_decision=RiskDecision(True, "approved"),
            ledger=self.ledger,
            account_seq="1",
            allowed_symbols={"SPY"},
        )
        self.assertEqual(plan.client_order_id, "cid-3")
        count = self.ledger.conn.execute(
            "SELECT COUNT(*) FROM client_order_id_registry"
        ).fetchone()[0]
        self.assertEqual(count, 1)

    def test_client_order_id_conflict_across_accounts_is_explicit(self):
        self.planner.create_plan(
            signal(),
            {"client_order_id": "global-id", "qty": 1},
            risk_decision=RiskDecision(True, "approved"),
            ledger=self.ledger,
            account_seq="1",
            allowed_symbols={"SPY"},
        )
        with self.assertRaisesRegex(ValueError, "different account"):
            self.planner.create_plan(
                signal(),
                {"client_order_id": "global-id", "qty": 1},
                risk_decision=RiskDecision(True, "approved"),
                ledger=self.ledger,
                account_seq="2",
                allowed_symbols={"SPY"},
            )


if __name__ == "__main__":
    unittest.main()

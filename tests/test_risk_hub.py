import unittest

from toss_trading.engines import Signal
from toss_trading.policy import load_policy
from toss_trading.risk import RiskHub


def signal() -> Signal:
    return Signal(
        engine="broad_momentum",
        symbol_or_pair="SPY",
        side="BUY",
        raw_score=0.1,
        adjusted_score=0.1,
        target_weight=0.01,
        expected_max_loss=5,
        reason_code="baseline",
    )


def state() -> dict:
    return {
        "kill_switch_state": "NORMAL",
        "reconciliation_ok": True,
        "source_health_ok": True,
        "rate_limit_ok": True,
        "open_orders_count": 0,
        "nav": 10_000,
        "drawdown_pct": 0.0,
        "proposed_order_notional": 5,
        "available_cash": 100,
        "allowed_symbols": {"SPY"},
    }


class RiskHubTest(unittest.TestCase):
    def setUp(self):
        policy, _ = load_policy()
        self.hub = RiskHub(policy)

    def test_approves_only_when_cash_notional_and_universe_pass(self):
        self.assertTrue(self.hub.evaluate_signal(signal(), state()).approved)

    def test_rejects_unapproved_symbol(self):
        portfolio = state()
        portfolio["allowed_symbols"] = {"QQQ"}
        decision = self.hub.evaluate_signal(signal(), portfolio)
        self.assertFalse(decision.approved)
        self.assertIn("approved universe", decision.reason)

    def test_rejects_notional_and_cash_breaches(self):
        portfolio = state()
        portfolio["proposed_order_notional"] = 20
        decision = self.hub.evaluate_signal(signal(), portfolio)
        self.assertFalse(decision.approved)
        self.assertIn("notional limit", decision.reason)

        portfolio = state()
        portfolio["available_cash"] = 1
        decision = self.hub.evaluate_signal(signal(), portfolio)
        self.assertFalse(decision.approved)
        self.assertIn("cash", decision.reason)

    def test_rejects_drawdown_kill_switch(self):
        portfolio = state()
        portfolio["drawdown_pct"] = 2.0
        decision = self.hub.evaluate_signal(signal(), portfolio)
        self.assertFalse(decision.approved)
        self.assertIn("drawdown", decision.reason)


if __name__ == "__main__":
    unittest.main()

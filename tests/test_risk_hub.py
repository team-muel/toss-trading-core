import unittest

from toss_trading.engines import Signal
from toss_trading.policy import load_policy
from toss_trading.risk import OrderIntent, RiskHub


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
        "account_seq": "1",
        "snapshot_run_id": "run-1",
        "policy_hash": "policy-1",
        "nav": 10_000,
        "drawdown_pct": 0.0,
        "available_cash": 100,
        "allowed_symbols": {"SPY"},
    }


def intent(*, amount: float = 5, symbol: str = "SPY") -> OrderIntent:
    proposed_signal = signal()
    if symbol != proposed_signal.symbol_or_pair:
        proposed_signal = Signal(
            engine=proposed_signal.engine,
            symbol_or_pair=symbol,
            side=proposed_signal.side,
            raw_score=proposed_signal.raw_score,
            adjusted_score=proposed_signal.adjusted_score,
            target_weight=proposed_signal.target_weight,
            expected_max_loss=proposed_signal.expected_max_loss,
            reason_code=proposed_signal.reason_code,
        )
    return OrderIntent.create(
        proposed_signal,
        {"client_order_id": "cid-1", "order_amount": amount},
        account_seq="1",
        snapshot_run_id="run-1",
        policy_hash="policy-1",
        currency="USD",
        reference_price=None,
    )


class RiskHubTest(unittest.TestCase):
    def setUp(self):
        policy, _ = load_policy()
        self.hub = RiskHub(policy)

    def test_approves_only_when_cash_notional_and_universe_pass(self):
        decision = self.hub.evaluate_signal(
            signal(), state(), order_intent=intent()
        )
        self.assertTrue(decision.approved)
        self.assertEqual(decision.intent_hash, intent().intent_hash)

    def test_rejects_unapproved_symbol(self):
        portfolio = state()
        portfolio["allowed_symbols"] = {"QQQ"}
        decision = self.hub.evaluate_signal(
            signal(), portfolio, order_intent=intent()
        )
        self.assertFalse(decision.approved)
        self.assertIn("approved universe", decision.reason)

    def test_rejects_notional_and_cash_breaches(self):
        portfolio = state()
        decision = self.hub.evaluate_signal(
            signal(), portfolio, order_intent=intent(amount=20)
        )
        self.assertFalse(decision.approved)
        self.assertIn("max loss limit", decision.reason)

        portfolio = state()
        portfolio["available_cash"] = 1
        decision = self.hub.evaluate_signal(
            signal(), portfolio, order_intent=intent()
        )
        self.assertFalse(decision.approved)
        self.assertIn("cash", decision.reason)

    def test_rejects_drawdown_kill_switch(self):
        portfolio = state()
        portfolio["drawdown_pct"] = 2.0
        decision = self.hub.evaluate_signal(
            signal(), portfolio, order_intent=intent()
        )
        self.assertFalse(decision.approved)
        self.assertIn("drawdown", decision.reason)

    def test_rejects_missing_or_stale_order_intent(self):
        missing = self.hub.evaluate_signal(signal(), state())
        self.assertFalse(missing.approved)
        self.assertIn("exact order intent", missing.reason)

        portfolio = state()
        portfolio["snapshot_run_id"] = "run-2"
        stale = self.hub.evaluate_signal(
            signal(), portfolio, order_intent=intent()
        )
        self.assertFalse(stale.approved)
        self.assertIn("snapshot", stale.reason)

    def test_paper_profile_does_not_relax_starter_live_limits(self):
        proposed = signal()
        paper_intent = intent(amount=9_000)
        portfolio = state()
        portfolio["available_cash"] = 10_000
        starter = self.hub.evaluate_signal(
            proposed, portfolio, order_intent=paper_intent
        )
        paper = self.hub.evaluate_signal(
            proposed,
            portfolio,
            order_intent=paper_intent,
            guardrail_profile="paper_guardrails",
        )
        self.assertFalse(starter.approved)
        self.assertTrue(paper.approved)


if __name__ == "__main__":
    unittest.main()

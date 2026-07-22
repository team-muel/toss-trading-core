import unittest

from toss_trading.alpha import (
    Alpha,
    SimulationSettings,
    simulate_cross_section,
    to_signals,
)
from toss_trading.alpha import operators as ops
from toss_trading.risk.hub import RiskHub


def momentum_alpha() -> Alpha:
    # rank of a precomputed momentum datafield -> long the strongest names
    return Alpha(
        name="broad_etf_dual_momentum",
        expression=lambda ctx: ops.rank(ctx["momentum"]),
        description="cross-sectional momentum rank",
    )


class ExpressionTest(unittest.TestCase):
    def setUp(self):
        self.settings = SimulationSettings(
            universe="KOR_ETF_TOP20",
            book_size=1.0,
            neutralization="market",
            truncation=0.4,
            long_only=True,
            stop_loss_frac=0.15,
        )
        self.context = {"momentum": {"SPY": 0.10, "QQQ": 0.20, "TLT": -0.05, "GLD": 0.01}}

    def test_long_only_positions_are_non_negative_and_capped(self):
        positions = simulate_cross_section(momentum_alpha(), self.context, self.settings)
        self.assertTrue(all(w >= 0 for w in positions.weights.values()))
        gross = sum(abs(w) for w in positions.weights.values())
        # gross never exceeds the book, and truncation is never violated
        self.assertLessEqual(gross, self.settings.book_size + 1e-9)
        self.assertLessEqual(
            max(positions.weights.values()),
            self.settings.truncation * self.settings.book_size + 1e-9,
        )

    def test_invalid_neutralization_rejected(self):
        with self.assertRaises(ValueError):
            SimulationSettings(universe="u", book_size=1.0, neutralization="wat")

    def test_group_neutralization_requires_groups(self):
        settings = SimulationSettings(universe="u", book_size=1.0, neutralization="group")
        with self.assertRaises(ValueError):
            simulate_cross_section(momentum_alpha(), self.context, settings)

    def test_to_signals_emits_repo_signal_objects(self):
        positions = simulate_cross_section(momentum_alpha(), self.context, self.settings)
        signals = to_signals(positions)
        self.assertTrue(signals)
        top = signals[0]
        self.assertEqual(top.side, "BUY")
        self.assertEqual(top.engine, "broad_etf_dual_momentum")
        self.assertGreaterEqual(top.expected_max_loss, 0.0)

    def test_alpha_signal_passes_risk_hub_paper_gates(self):
        positions = simulate_cross_section(momentum_alpha(), self.context, self.settings)
        signal = to_signals(positions)[0]
        policy = {
            "runtime": {"live_trading_enabled": False},
            "starter_guardrails": {
                "max_open_orders": 10,
                "single_trade_max_loss_nav_pct": 10.0,
            },
        }
        portfolio_state = {
            "kill_switch_state": "NORMAL",
            "reconciliation_ok": True,
            "source_health_ok": True,
            "rate_limit_ok": True,
            "open_orders_count": 0,
            "nav": 1.0,
        }
        decision = RiskHub(policy).evaluate_signal(signal, portfolio_state)
        self.assertTrue(decision.approved, decision.reason)

    def test_alpha_signal_still_blocked_when_source_health_bad(self):
        positions = simulate_cross_section(momentum_alpha(), self.context, self.settings)
        signal = to_signals(positions)[0]
        policy = {
            "runtime": {"live_trading_enabled": False},
            "starter_guardrails": {"max_open_orders": 10, "single_trade_max_loss_nav_pct": 10.0},
        }
        portfolio_state = {
            "kill_switch_state": "NORMAL",
            "reconciliation_ok": True,
            "source_health_ok": False,
            "rate_limit_ok": True,
            "open_orders_count": 0,
            "nav": 1.0,
        }
        decision = RiskHub(policy).evaluate_signal(signal, portfolio_state)
        self.assertFalse(decision.approved)


if __name__ == "__main__":
    unittest.main()

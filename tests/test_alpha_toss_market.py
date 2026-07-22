import unittest

from toss_trading.alpha import (
    Alpha,
    SimulationSettings,
    metrics,
    simulate_cross_section,
    to_signals,
)
from toss_trading.alpha import operators as ops
from toss_trading.alpha.datafields import (
    TossCandleReader,
    close_panel,
    extract_candles,
    forward_returns_panel,
    momentum_datafield,
)
from toss_trading.risk.hub import RiskHub


def candle(ts, close):
    return {"timestamp": ts, "openPrice": close, "highPrice": close,
            "lowPrice": close, "closePrice": close, "volume": 1000, "currency": "KRW"}


def make_reader(series_by_symbol):
    def reader(symbol):
        return [candle(ts, close) for ts, close in series_by_symbol[symbol]]

    return reader


class TossMarketDatafieldTest(unittest.TestCase):
    def test_extract_candles_unwraps_result_and_sorts(self):
        body = {
            "result": {
                "candles": [
                    {"timestamp": "2026-07-03", "closePrice": 12.0},
                    {"timestamp": "2026-07-01", "closePrice": 10.0},
                ]
            }
        }
        out = extract_candles(body)
        self.assertEqual([c["timestamp"] for c in out], ["2026-07-01", "2026-07-03"])

    def test_momentum_datafield_computes_trailing_return(self):
        reader = make_reader(
            {
                "AAA": [("d1", 100.0), ("d2", 110.0), ("d3", 121.0)],
                "BBB": [("d1", 100.0), ("d2", 90.0), ("d3", 81.0)],
            }
        )
        field = momentum_datafield(["AAA", "BBB"], reader, lookback=2)
        self.assertAlmostEqual(field["AAA"], 0.21)
        self.assertAlmostEqual(field["BBB"], -0.19)

    def test_momentum_skips_thin_history(self):
        reader = make_reader({"AAA": [("d1", 100.0)]})
        self.assertEqual(momentum_datafield(["AAA"], reader, lookback=2), {})

    def test_toss_candle_reader_uses_adapter_body(self):
        class FakeResult:
            body = {"result": {"candles": [{"timestamp": "d1", "closePrice": 5.0}]}}

        class FakeAdapter:
            def get_candles(self, symbol, *, interval, count):
                assert interval == "1d" and count == 120
                return FakeResult()

        reader = TossCandleReader(FakeAdapter())
        self.assertEqual([c["closePrice"] for c in reader("AAA")], [5.0])

    def test_end_to_end_toss_momentum_alpha_passes_paper_gates(self):
        reader = make_reader(
            {
                "SPY": [("d1", 100.0), ("d2", 104.0), ("d3", 110.0)],
                "QQQ": [("d1", 100.0), ("d2", 108.0), ("d3", 120.0)],
                "TLT": [("d1", 100.0), ("d2", 99.0), ("d3", 95.0)],
                "GLD": [("d1", 100.0), ("d2", 101.0), ("d3", 101.0)],
            }
        )
        momentum = momentum_datafield(["SPY", "QQQ", "TLT", "GLD"], reader, lookback=2)
        alpha = Alpha(name="toss_momentum", expression=lambda ctx: ops.rank(ctx["momentum"]))
        settings = SimulationSettings(
            universe="TOSS_KR_US_ETF", book_size=1.0, neutralization="market", truncation=0.4
        )
        positions = simulate_cross_section(alpha, {"momentum": momentum}, settings)
        signals = to_signals(positions)
        self.assertTrue(signals)
        self.assertEqual(signals[0].engine, "toss_momentum")

        policy = {
            "runtime": {"live_trading_enabled": False},
            "starter_guardrails": {"max_open_orders": 10, "single_trade_max_loss_nav_pct": 10.0},
        }
        state = {
            "kill_switch_state": "NORMAL",
            "reconciliation_ok": True,
            "source_health_ok": True,
            "rate_limit_ok": True,
            "open_orders_count": 0,
            "nav": 1.0,
        }
        decision = RiskHub(policy).evaluate_signal(signals[0], state)
        self.assertTrue(decision.approved, decision.reason)

    def test_backtest_panel_produces_metrics(self):
        reader = make_reader(
            {
                "AAA": [("d1", 100.0), ("d2", 101.0), ("d3", 103.0), ("d4", 102.0)],
                "BBB": [("d1", 100.0), ("d2", 99.0), ("d3", 100.0), ("d4", 101.0)],
            }
        )
        panel = close_panel(["AAA", "BBB"], reader)
        forward = forward_returns_panel(panel)
        # static equal-weight book as a trivial position panel
        positions = {sym: [0.5] * len(series) for sym, series in panel.items()}
        result = metrics.evaluate(positions, forward, book_size=1.0)
        self.assertEqual(result.periods, 4)
        self.assertIn("sharpe", result.summary())


if __name__ == "__main__":
    unittest.main()

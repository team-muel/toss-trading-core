from __future__ import annotations

import math
import unittest
from datetime import date, timedelta

from toss_trading.research import PricePoint, QuantFactorConfig, run_quant_factor_backtest
from toss_trading.research.costs import ExecutionCostModel, SlippageTier


def _points(days: int = 900) -> list[PricePoint]:
    result: list[PricePoint] = []
    current = date(2019, 1, 1)
    values = {"SPY": 100.0, "QQQ": 100.0, "TLT": 100.0, "GLD": 100.0, "SGOV": 100.0}
    index = 0
    while index < days:
        current += timedelta(days=1)
        if current.weekday() >= 5:
            continue
        index += 1
        values["SPY"] *= 1.0005 if index % 2 else 1.0002
        values["QQQ"] *= 1.001 if index % 2 else 0.9994
        values["TLT"] *= 1.00012
        values["GLD"] *= 1.0003 if index % 5 else 0.9992
        values["SGOV"] *= 1.00005
        for symbol, value in values.items():
            result.append(
                PricePoint(
                    date=current.isoformat(),
                    symbol=symbol,
                    total_return_index=f"{value:.12f}",
                    available_at=f"{current.isoformat()}T22:00:00+00:00",
                )
            )
    return result


def _cost_model() -> ExecutionCostModel:
    return ExecutionCostModel(
        schema_version="execution-cost-model-v1",
        commission_bps=1.0,
        minimum_commission_usd=0.0,
        portfolio_notional_usd=10_000.0,
        slippage_tiers=(SlippageTier(None, 1.0),),
        commission_source="test",
        slippage_source="test",
    )


def _config(factor: str = "low_volatility") -> QuantFactorConfig:
    weights = {
        "momentum": 0.0,
        "risk_adjusted_momentum": 0.0,
        "short_term_reversal": 0.0,
        "low_volatility": 0.0,
        "trend_acceleration": 0.0,
    }
    weights[factor] = 1.0
    return QuantFactorConfig(
        candidate_symbols=("SPY", "QQQ", "TLT", "GLD"),
        cash_symbol="SGOV",
        factor_weights=tuple(sorted(weights.items())),
        long_lookback_trading_days=126,
        short_lookback_trading_days=21,
        volatility_window_trading_days=63,
        skip_recent_trading_days=0,
        top_k=1,
        weighting="equal",
        rebalance_frequency="monthly",
        regime_filter="none",
        minimum_composite_score=0.0,
        walk_forward_train_days=504,
        walk_forward_test_days=126,
    )


class QuantFactorBacktestTests(unittest.TestCase):
    def test_low_volatility_family_selects_stable_asset_with_next_day_execution(self) -> None:
        result = run_quant_factor_backtest(
            _points(),
            _config(),
            execution_cost_model=_cost_model(),
        )

        self.assertTrue(result.rebalances)
        self.assertTrue(all(item.signal_date < item.effective_date for item in result.rebalances))
        self.assertEqual(set(result.rebalances[-1].target_weights), {"TLT"})
        self.assertTrue(math.isfinite(result.metrics["sharpe_zero_rate"]))
        self.assertIn("SPY buy-and-hold", result.benchmark_daily_returns)
        self.assertGreaterEqual(len(result.walk_forward_folds), 2)

    def test_factor_dsl_rejects_empty_factor_combination(self) -> None:
        config = _config()
        empty = QuantFactorConfig(
            **{
                **config.__dict__,
                "factor_weights": tuple((name, 0.0) for name, _ in config.factor_weights),
            }
        )
        with self.assertRaisesRegex(ValueError, "at least one factor"):
            empty.validate()


if __name__ == "__main__":
    unittest.main()

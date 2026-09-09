from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass, replace
from datetime import date
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class SlippageTier:
    maximum_order_notional_usd: float | None
    slippage_bps: float


@dataclass(frozen=True)
class ExecutionCostModel:
    schema_version: str
    commission_bps: float
    minimum_commission_usd: float
    portfolio_notional_usd: float
    slippage_tiers: tuple[SlippageTier, ...]
    commission_source: str
    slippage_source: str
    observed_at: str | None = None
    valid_through: str | None = None

    def validate(self) -> None:
        if self.schema_version != "execution-cost-model-v1":
            raise ValueError("unsupported execution cost model schema")
        if min(
            self.commission_bps,
            self.minimum_commission_usd,
            self.portfolio_notional_usd,
        ) < 0 or self.portfolio_notional_usd == 0:
            raise ValueError("execution cost values must be nonnegative and funded")
        if not self.slippage_tiers:
            raise ValueError("execution cost model requires slippage tiers")
        previous = 0.0
        for index, tier in enumerate(self.slippage_tiers):
            if tier.slippage_bps < 0:
                raise ValueError("slippage bps must be nonnegative")
            maximum = tier.maximum_order_notional_usd
            if maximum is None:
                if index != len(self.slippage_tiers) - 1:
                    raise ValueError("open-ended slippage tier must be last")
                continue
            if maximum <= previous:
                raise ValueError("slippage tier limits must increase")
            previous = maximum
        if self.slippage_tiers[-1].maximum_order_notional_usd is not None:
            raise ValueError("slippage tiers must end with an open tier")

    def slippage_bps_for(self, order_notional_usd: float) -> float:
        self.validate()
        if order_notional_usd < 0 or not math.isfinite(order_notional_usd):
            raise ValueError("order notional must be finite and nonnegative")
        for tier in self.slippage_tiers:
            if (
                tier.maximum_order_notional_usd is None
                or order_notional_usd <= tier.maximum_order_notional_usd
            ):
                return tier.slippage_bps
        raise AssertionError("unreachable slippage tier")

    def estimate_rebalance(
        self,
        previous_weights: dict[str, float],
        target_weights: dict[str, float],
        *,
        equity_multiple: float,
    ) -> dict[str, float]:
        self.validate()
        portfolio_value = self.portfolio_notional_usd * equity_multiple
        if portfolio_value <= 0 or not math.isfinite(portfolio_value):
            raise ValueError("portfolio value must be finite and positive")
        commission_usd = 0.0
        slippage_usd = 0.0
        gross_order_notional = 0.0
        symbols = set(previous_weights) | set(target_weights)
        for symbol in symbols:
            order_notional = abs(
                target_weights.get(symbol, 0.0) - previous_weights.get(symbol, 0.0)
            ) * portfolio_value
            if order_notional <= 0:
                continue
            gross_order_notional += order_notional
            commission_usd += max(
                self.minimum_commission_usd,
                order_notional * self.commission_bps / 10_000.0,
            )
            slippage_usd += (
                order_notional * self.slippage_bps_for(order_notional) / 10_000.0
            )
        return {
            "gross_order_notional_usd": gross_order_notional,
            "commission_usd": commission_usd,
            "slippage_usd": slippage_usd,
            "commission_fraction": commission_usd / portfolio_value,
            "slippage_fraction": slippage_usd / portfolio_value,
            "total_fraction": (commission_usd + slippage_usd) / portfolio_value,
        }

    def stressed(self, multiplier: float) -> "ExecutionCostModel":
        if multiplier <= 0:
            raise ValueError("cost stress multiplier must be positive")
        return replace(
            self,
            commission_bps=self.commission_bps * multiplier,
            minimum_commission_usd=self.minimum_commission_usd * multiplier,
            slippage_tiers=tuple(
                replace(item, slippage_bps=item.slippage_bps * multiplier)
                for item in self.slippage_tiers
            ),
        )

    def as_record(self) -> dict[str, Any]:
        return asdict(self)


def load_execution_cost_model(
    path: str | Path,
    *,
    portfolio_notional_usd: float | None = None,
    as_of: str | None = None,
) -> ExecutionCostModel:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if payload.get("schema_version") != "execution-cost-calibration-v1":
        raise ValueError("unsupported execution cost calibration schema")
    commission = payload.get("commission")
    slippage = payload.get("slippage")
    if not isinstance(commission, dict) or not isinstance(slippage, dict):
        raise ValueError("execution cost calibration is incomplete")
    valid_through = commission.get("valid_through")
    comparison_date = date.fromisoformat(as_of) if as_of else date.today()
    if valid_through and comparison_date > date.fromisoformat(str(valid_through)):
        raise ValueError("execution commission calibration has expired")
    tiers = tuple(
        SlippageTier(
            (
                float(item["maximum_order_notional_usd"])
                if item.get("maximum_order_notional_usd") is not None
                else None
            ),
            float(item["slippage_bps"]),
        )
        for item in slippage.get("tiers", [])
    )
    calibrated_notional = (
        portfolio_notional_usd
        if portfolio_notional_usd is not None
        else payload.get("portfolio_notional_usd")
    )
    if calibrated_notional is None:
        raise ValueError("execution cost calibration lacks portfolio notional")
    model = ExecutionCostModel(
        schema_version="execution-cost-model-v1",
        commission_bps=float(commission["normalized_bps"]),
        minimum_commission_usd=float(commission.get("minimum_commission_usd", 0.0)),
        portfolio_notional_usd=float(calibrated_notional),
        slippage_tiers=tiers,
        commission_source=str(commission["source"]),
        slippage_source=str(slippage["source"]),
        observed_at=str(commission.get("observed_at") or "") or None,
        valid_through=str(valid_through) if valid_through else None,
    )
    model.validate()
    return model

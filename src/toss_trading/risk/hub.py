from dataclasses import dataclass

from toss_trading.engines import Signal


@dataclass(frozen=True)
class RiskDecision:
    approved: bool
    reason: str
    adjusted_score: float | None = None


class RiskHub:
    """Applies portfolio-level gates before any order plan is created."""

    def __init__(self, policy: dict) -> None:
        self.policy = policy

    def evaluate_signal(self, signal: Signal, portfolio_state: dict) -> RiskDecision:
        if self.policy.get("runtime", {}).get("live_trading_enabled") is True:
            return RiskDecision(False, "live trading requires a reviewed live adapter")

        nav = float(portfolio_state.get("nav", 0) or 0)
        if nav <= 0:
            return RiskDecision(False, "missing portfolio NAV")

        max_loss_pct = signal.expected_max_loss / nav * 100
        limit = self.policy.get("portfolio_limits", {}).get("single_trade_max_loss_nav_pct")
        if limit is None:
            limit = self.policy["starter_guardrails"]["single_trade_max_loss_nav_pct"]
        if max_loss_pct > limit:
            return RiskDecision(False, "single trade max loss limit exceeded")

        return RiskDecision(True, "approved_for_paper")

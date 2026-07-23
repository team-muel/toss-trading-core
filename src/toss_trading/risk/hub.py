from dataclasses import dataclass
from math import isfinite

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
        runtime = self.policy.get("runtime", {})
        guardrails = self.policy.get("starter_guardrails", {})
        if runtime.get("live_trading_enabled") is True:
            return RiskDecision(False, "live trading is disabled until a reviewed live adapter exists")
        if portfolio_state.get("kill_switch_state", "HARD_FREEZE") != "NORMAL":
            return RiskDecision(False, "kill switch is not NORMAL")
        if not portfolio_state.get("reconciliation_ok", False):
            return RiskDecision(False, "reconciliation is not current and clean")
        if not portfolio_state.get("source_health_ok", False):
            return RiskDecision(False, "required source health is not ok")
        if not portfolio_state.get("rate_limit_ok", False):
            return RiskDecision(False, "rate limit is degraded")
        max_open_orders = int(guardrails.get("max_open_orders", 0))
        if int(portfolio_state.get("open_orders_count", 0)) >= max_open_orders:
            return RiskDecision(False, "max open orders reached")
        if signal.side not in {"BUY", "SELL"}:
            return RiskDecision(False, "invalid signal side")
        allowed_symbols = set(portfolio_state.get("allowed_symbols") or ())
        if not allowed_symbols or signal.symbol_or_pair not in allowed_symbols:
            return RiskDecision(False, "signal symbol is outside the approved universe")
        if not isfinite(signal.target_weight) or not 0 <= signal.target_weight <= 1:
            return RiskDecision(False, "target weight must be between zero and one")
        if not isfinite(signal.expected_max_loss) or signal.expected_max_loss < 0:
            return RiskDecision(False, "expected max loss must be finite and nonnegative")

        nav = float(portfolio_state.get("nav", 0) or 0)
        if not isfinite(nav) or nav <= 0:
            return RiskDecision(False, "missing portfolio NAV")

        max_loss_pct = signal.expected_max_loss / nav * 100
        limit = self.policy.get("portfolio_limits", {}).get("single_trade_max_loss_nav_pct")
        if limit is None:
            limit = self.policy["starter_guardrails"]["single_trade_max_loss_nav_pct"]
        if max_loss_pct > limit:
            return RiskDecision(False, "single trade max loss limit exceeded")

        drawdown_pct = float(portfolio_state.get("drawdown_pct", 0) or 0)
        drawdown_limit = self.policy.get("portfolio_limits", {}).get(
            "max_drawdown_kill_switch_pct"
        )
        if drawdown_limit is None:
            drawdown_limit = guardrails.get("max_drawdown_kill_switch_pct")
        if not isfinite(drawdown_pct) or drawdown_pct < 0:
            return RiskDecision(False, "drawdown must be finite and nonnegative")
        if drawdown_limit is not None and drawdown_pct >= float(drawdown_limit):
            return RiskDecision(False, "drawdown kill switch limit reached")

        proposed_notional = float(portfolio_state.get("proposed_order_notional", 0) or 0)
        if not isfinite(proposed_notional) or proposed_notional <= 0:
            return RiskDecision(False, "proposed order notional is missing")
        notional_limit = self.policy.get("portfolio_limits", {}).get(
            "single_live_order_notional_nav_pct"
        )
        if notional_limit is None:
            notional_limit = guardrails.get("single_live_order_notional_nav_pct")
        if proposed_notional / nav * 100 > float(notional_limit):
            return RiskDecision(False, "single order notional limit exceeded")
        if signal.side == "BUY":
            available_cash = float(portfolio_state.get("available_cash", 0) or 0)
            if not isfinite(available_cash) or available_cash < proposed_notional:
                return RiskDecision(False, "available cash is insufficient")

        return RiskDecision(True, "approved_for_paper_after_all_gates")

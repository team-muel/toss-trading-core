from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from math import isfinite

from toss_trading.engines import Signal
from toss_trading.risk.intent import OrderIntent


@dataclass(frozen=True)
class RiskDecision:
    approved: bool
    reason: str
    adjusted_score: float | None = None
    intent_hash: str | None = None
    account_seq: str | None = None
    snapshot_run_id: str | None = None
    policy_hash: str | None = None
    approved_notional_decimal: str | None = None
    expires_at: str | None = None


class RiskHub:
    """Applies portfolio-level gates before any order plan is created."""

    def __init__(self, policy: dict) -> None:
        self.policy = policy

    def evaluate_signal(
        self,
        signal: Signal,
        portfolio_state: dict,
        *,
        order_intent: OrderIntent | None = None,
        guardrail_profile: str = "starter_guardrails",
    ) -> RiskDecision:
        runtime = self.policy.get("runtime", {})
        if guardrail_profile not in {"starter_guardrails", "paper_guardrails"}:
            return RiskDecision(False, "unsupported guardrail profile")
        guardrails = self.policy.get(guardrail_profile, {})
        if guardrail_profile == "paper_guardrails" and not guardrails.get(
            "enabled", False
        ):
            return RiskDecision(False, "paper guardrails are disabled")
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
        if order_intent is None:
            return RiskDecision(False, "exact order intent is required")
        if (
            order_intent.symbol != signal.symbol_or_pair.strip().upper()
            or order_intent.side != signal.side
        ):
            return RiskDecision(False, "order intent does not match signal")
        if order_intent.account_seq != str(portfolio_state.get("account_seq") or ""):
            return RiskDecision(False, "order intent account does not match current state")
        if order_intent.snapshot_run_id != str(
            portfolio_state.get("snapshot_run_id") or ""
        ):
            return RiskDecision(False, "order intent snapshot is not current")
        if order_intent.policy_hash != str(portfolio_state.get("policy_hash") or ""):
            return RiskDecision(False, "order intent policy is not current")
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

        try:
            proposed_notional_decimal = Decimal(order_intent.notional_decimal)
        except InvalidOperation:
            return RiskDecision(False, "order intent notional is invalid")
        proposed_notional = float(proposed_notional_decimal)
        # A long cash position can lose its entire principal. Do not trust an
        # engine-supplied stop-loss estimate as the portfolio loss boundary.
        independent_max_loss = (
            proposed_notional if signal.side == "BUY" else 0.0
        )
        max_loss_pct = independent_max_loss / nav * 100
        limit = self.policy.get("portfolio_limits", {}).get("single_trade_max_loss_nav_pct")
        if limit is None or guardrail_profile == "paper_guardrails":
            limit = self.policy["starter_guardrails"]["single_trade_max_loss_nav_pct"]
            if guardrail_profile == "paper_guardrails":
                limit = guardrails.get("single_trade_max_loss_nav_pct")
        if limit is None:
            return RiskDecision(False, "single trade max loss limit is missing")
        if max_loss_pct > limit:
            return RiskDecision(False, "single trade max loss limit exceeded")

        drawdown_pct = float(portfolio_state.get("drawdown_pct", 0) or 0)
        drawdown_limit = self.policy.get("portfolio_limits", {}).get(
            "max_drawdown_kill_switch_pct"
        )
        if drawdown_limit is None or guardrail_profile == "paper_guardrails":
            drawdown_limit = guardrails.get("max_drawdown_kill_switch_pct")
        if not isfinite(drawdown_pct) or drawdown_pct < 0:
            return RiskDecision(False, "drawdown must be finite and nonnegative")
        if drawdown_limit is not None and drawdown_pct >= float(drawdown_limit):
            return RiskDecision(False, "drawdown kill switch limit reached")

        if not isfinite(proposed_notional) or proposed_notional <= 0:
            return RiskDecision(False, "proposed order notional is missing")
        notional_limit = self.policy.get("portfolio_limits", {}).get(
            "single_live_order_notional_nav_pct"
        )
        if notional_limit is None or guardrail_profile == "paper_guardrails":
            notional_limit = guardrails.get("single_live_order_notional_nav_pct")
        if notional_limit is None:
            return RiskDecision(False, "single order notional limit is missing")
        if proposed_notional / nav * 100 > float(notional_limit):
            return RiskDecision(False, "single order notional limit exceeded")
        if signal.side == "BUY":
            available_cash = float(portfolio_state.get("available_cash", 0) or 0)
            if not isfinite(available_cash) or available_cash < proposed_notional:
                return RiskDecision(False, "available cash is insufficient")
        else:
            if order_intent.quantity_decimal is None:
                return RiskDecision(False, "sell order must use an exact quantity")
            sellable_quantity = float(portfolio_state.get("sellable_quantity", 0) or 0)
            if (
                not isfinite(sellable_quantity)
                or sellable_quantity < float(order_intent.quantity_decimal)
            ):
                return RiskDecision(False, "sellable quantity is insufficient")

        expires_at = datetime.now(timezone.utc) + timedelta(seconds=30)
        return RiskDecision(
            True,
            "approved_for_paper_after_all_gates",
            intent_hash=order_intent.intent_hash,
            account_seq=order_intent.account_seq,
            snapshot_run_id=order_intent.snapshot_run_id,
            policy_hash=order_intent.policy_hash,
            approved_notional_decimal=order_intent.notional_decimal,
            expires_at=expires_at.isoformat(),
        )

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from toss_trading.account.ledger import AccountLedger
from toss_trading.engines import Signal
from toss_trading.risk import OrderIntent, RiskDecision


@dataclass(frozen=True)
class OrderPlan:
    client_order_id: str
    symbol: str
    side: str
    order_basis: str
    qty: float | None
    order_amount: float | None
    limit_px: float | None
    quantity_decimal: str | None
    order_amount_decimal: str | None
    limit_price_decimal: str | None
    notional_decimal: str
    currency: str
    snapshot_run_id: str
    policy_hash: str
    risk_intent_hash: str
    source_engine: str
    source_reason: str


class OrderPlanner:
    """Converts approved signals into paper order plans."""

    def create_plan(
        self,
        signal: Signal,
        sizing: dict[str, Any],
        *,
        risk_decision: RiskDecision,
        ledger: AccountLedger,
        account_seq: str,
        allowed_symbols: set[str] | frozenset[str],
        snapshot_run_id: str,
        policy_hash: str,
        currency: str,
        reference_price: Any | None = None,
    ) -> OrderPlan:
        if not risk_decision.approved:
            raise ValueError(f"risk decision rejected order plan: {risk_decision.reason}")
        if signal.symbol_or_pair not in allowed_symbols:
            raise ValueError("signal symbol is outside the approved universe")
        intent = OrderIntent.create(
            signal,
            sizing,
            account_seq=account_seq,
            snapshot_run_id=snapshot_run_id,
            policy_hash=policy_hash,
            currency=currency,
            reference_price=reference_price,
        )
        expected_binding = (
            risk_decision.intent_hash == intent.intent_hash
            and risk_decision.account_seq == intent.account_seq
            and risk_decision.snapshot_run_id == intent.snapshot_run_id
            and risk_decision.policy_hash == intent.policy_hash
            and risk_decision.approved_notional_decimal == intent.notional_decimal
        )
        if not expected_binding:
            raise ValueError("risk decision does not bind this exact order intent")
        try:
            expires_at = datetime.fromisoformat(risk_decision.expires_at or "")
        except ValueError as exc:
            raise ValueError("risk decision expiry is missing or invalid") from exc
        if expires_at.tzinfo is None:
            raise ValueError("risk decision expiry must include a timezone")
        if expires_at <= datetime.now(timezone.utc):
            raise ValueError("risk decision has expired")

        ledger.reserve_client_order_id(
            account_seq=intent.account_seq,
            client_order_id=intent.client_order_id,
            request_payload={
                "symbol": intent.symbol,
                "side": intent.side,
                "currency": intent.currency,
                "quantity_decimal": intent.quantity_decimal,
                "order_amount_decimal": intent.order_amount_decimal,
                "limit_price_decimal": intent.limit_price_decimal,
                "reference_price_decimal": intent.reference_price_decimal,
                "notional_decimal": intent.notional_decimal,
                "snapshot_run_id": intent.snapshot_run_id,
                "policy_hash": intent.policy_hash,
                "risk_intent_hash": intent.intent_hash,
            },
        )

        return OrderPlan(
            client_order_id=intent.client_order_id,
            symbol=intent.symbol,
            side=intent.side,
            order_basis="quantity" if intent.quantity_decimal is not None else "amount",
            qty=(
                float(Decimal(intent.quantity_decimal))
                if intent.quantity_decimal is not None
                else None
            ),
            order_amount=(
                float(Decimal(intent.order_amount_decimal))
                if intent.order_amount_decimal is not None
                else None
            ),
            limit_px=(
                float(Decimal(intent.limit_price_decimal))
                if intent.limit_price_decimal is not None
                else None
            ),
            quantity_decimal=intent.quantity_decimal,
            order_amount_decimal=intent.order_amount_decimal,
            limit_price_decimal=intent.limit_price_decimal,
            notional_decimal=intent.notional_decimal,
            currency=intent.currency,
            snapshot_run_id=intent.snapshot_run_id,
            policy_hash=intent.policy_hash,
            risk_intent_hash=intent.intent_hash,
            source_engine=signal.engine,
            source_reason=signal.reason_code,
        )

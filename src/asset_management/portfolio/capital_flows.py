"""Planned capital flows and conservative investable-capital calculation."""
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from enum import StrEnum

from asset_management.domain.errors import DataQualityError
from asset_management.domain.scalars import Currency


class RecognitionStatus(StrEnum):
    RECOGNIZED_IN_NAV = "RECOGNIZED_IN_NAV"
    NOT_RECOGNIZED = "NOT_RECOGNIZED"
    UNKNOWN = "UNKNOWN"


class CapitalFlowKind(StrEnum):
    PLANNED_DEPOSIT = "PLANNED_DEPOSIT"
    PLANNED_WITHDRAWAL = "PLANNED_WITHDRAWAL"
    CONTRACTUAL_OUTFLOW = "CONTRACTUAL_OUTFLOW"
    MINIMUM_LIQUIDITY = "MINIMUM_LIQUIDITY"


@dataclass(frozen=True)
class CapitalFlow:
    flow_id: str
    kind: CapitalFlowKind
    amount: Decimal
    currency: Currency
    due_at: datetime
    recognition_status: RecognitionStatus
    evidence_id: str
    priority: int
    effective_at: datetime
    expires_at: datetime | None = None
    supersedes_flow_id: str | None = None

    def __post_init__(self) -> None:
        times = (self.due_at, self.effective_at, self.expires_at)
        if (not self.flow_id.strip() or not isinstance(self.kind, CapitalFlowKind) or
                not isinstance(self.currency, Currency) or not self.amount.is_finite() or self.amount < 0 or
                not isinstance(self.recognition_status, RecognitionStatus) or not self.evidence_id.strip() or
                self.priority < 0 or any(value is not None and (value.tzinfo is None or value.utcoffset() is None) for value in times) or
                (self.supersedes_flow_id is not None and (not isinstance(self.supersedes_flow_id, str) or
                                                          not self.supersedes_flow_id.strip() or
                                                          self.supersedes_flow_id == self.flow_id)) or
                (self.expires_at is not None and self.expires_at <= self.effective_at)):
            raise DataQualityError("CAPITAL_FLOW_INVALID")
        for name in ("due_at", "effective_at", "expires_at"):
            value = getattr(self, name)
            if value is not None: object.__setattr__(self, name, value.astimezone(timezone.utc))


@dataclass(frozen=True)
class CapitalReserveAssessment:
    nav: Decimal
    currency: Currency
    liquidity_reserve: Decimal
    planned_outflows_not_yet_recognized: Decimal
    planned_deposits_not_yet_recognized: Decimal
    risk_capital: Decimal
    as_of: datetime
    evidence_ids: tuple[str, ...]

    def payload(self) -> dict[str, object]:
        return {"nav": str(self.nav), "currency": self.currency.value,
                "liquidity_reserve": str(self.liquidity_reserve),
                "planned_outflows_not_yet_recognized": str(self.planned_outflows_not_yet_recognized),
                "planned_deposits_not_yet_recognized": str(self.planned_deposits_not_yet_recognized),
                "risk_capital": str(self.risk_capital), "as_of": self.as_of.isoformat(),
                "evidence_ids": list(self.evidence_ids)}


def assess_capital_reserve(*, nav: Decimal, currency: Currency, flows: tuple[CapitalFlow, ...],
                           as_of: datetime) -> CapitalReserveAssessment:
    if (not isinstance(nav, Decimal) or not nav.is_finite() or nav < 0 or not isinstance(currency, Currency) or
            as_of.tzinfo is None or as_of.utcoffset() is None or
            any(not isinstance(flow, CapitalFlow) for flow in flows) or
            len({flow.flow_id for flow in flows}) != len(flows)):
        raise DataQualityError("CAPITAL_RESERVE_INPUT_INVALID")
    by_id = {flow.flow_id: flow for flow in flows}
    active = [flow for flow in flows if flow.currency is currency and flow.effective_at <= as_of and
              (flow.expires_at is None or as_of < flow.expires_at)]
    superseded_ids: set[str] = set()
    for flow in active:
        if flow.supersedes_flow_id is None:
            continue
        previous = by_id.get(flow.supersedes_flow_id)
        if (previous is None or previous.currency is not flow.currency or
                flow.effective_at < previous.effective_at or previous.flow_id in superseded_ids):
            raise DataQualityError("CAPITAL_FLOW_SUPERSESSION_INVALID")
        superseded_ids.add(previous.flow_id)
    active = [flow for flow in active if flow.flow_id not in superseded_ids]
    if any(flow.recognition_status is RecognitionStatus.UNKNOWN for flow in active):
        raise DataQualityError("CAPITAL_FLOW_RECOGNITION_UNKNOWN")
    reserve = sum((flow.amount for flow in active if flow.kind is CapitalFlowKind.MINIMUM_LIQUIDITY and
                   flow.recognition_status is RecognitionStatus.NOT_RECOGNIZED), Decimal(0))
    outflows = sum((flow.amount for flow in active if flow.kind in (CapitalFlowKind.PLANNED_WITHDRAWAL, CapitalFlowKind.CONTRACTUAL_OUTFLOW) and
                    flow.recognition_status is RecognitionStatus.NOT_RECOGNIZED), Decimal(0))
    deposits = sum((flow.amount for flow in active if flow.kind is CapitalFlowKind.PLANNED_DEPOSIT and
                    flow.recognition_status is RecognitionStatus.NOT_RECOGNIZED), Decimal(0))
    risk = nav - reserve - outflows
    if risk < 0: raise DataQualityError("CAPITAL_RESERVE_EXCEEDS_NAV")
    return CapitalReserveAssessment(nav, currency, reserve, outflows, deposits, risk,
        as_of.astimezone(timezone.utc), tuple(sorted(flow.evidence_id for flow in active)))

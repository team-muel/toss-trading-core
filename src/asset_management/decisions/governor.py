from __future__ import annotations

from dataclasses import dataclass, fields
from decimal import Decimal
from enum import StrEnum
from hashlib import sha256
import json
from typing import Mapping

from asset_management.domain.decimal import exact_decimal
from asset_management.domain.enums import DataStatus, DecisionAction
from asset_management.domain.errors import InvariantViolation, NoTrade

from .reason_codes import ReasonCode


class DecisionState(StrEnum):
    ALLOW = "ALLOW"
    REDUCE = "REDUCE"
    BLOCK = "BLOCK"
    ABSTAIN = "ABSTAIN"
    DEFER = "DEFER"


HARD_BLOCKS: tuple[tuple[str, ReasonCode], ...] = (
    ("reconciliation_failed", ReasonCode.RECONCILIATION_FAILED),
    ("order_state_unknown", ReasonCode.ORDER_STATE_UNKNOWN),
    ("initial_cash_unconfirmed", ReasonCode.INITIAL_CASH_UNCONFIRMED),
    ("same_run_account_snapshot_missing", ReasonCode.SAME_RUN_ACCOUNT_SNAPSHOT_MISSING),
    ("clock_risk", ReasonCode.CLOCK_RISK),
    ("execution_price_stale", ReasonCode.EXECUTION_PRICE_STALE),
    ("data_conflict", ReasonCode.DATA_CONFLICT),
    ("risk_model_failed", ReasonCode.RISK_MODEL_FAILED),
    ("optimizer_infeasible", ReasonCode.OPTIMIZER_INFEASIBLE),
    ("policy_mismatch", ReasonCode.POLICY_MISMATCH),
    ("duplicate_order_intent", ReasonCode.DUPLICATE_ORDER_INTENT),
    ("kill_switch_active", ReasonCode.KILL_SWITCH_ACTIVE),
    ("runtime_mode_unauthorized", ReasonCode.RUNTIME_MODE_UNAUTHORIZED),
)

SOFT_REDUCTIONS: tuple[tuple[str, ReasonCode], ...] = (
    ("volatility_high", ReasonCode.VOLATILITY_HIGH),
    ("low_confidence", ReasonCode.MODEL_UNCERTAIN),
    ("event_risk_high", ReasonCode.EVENT_RISK_HIGH),
    ("sector_concentrated", ReasonCode.SECTOR_LIMIT),
    ("factor_concentrated", ReasonCode.FACTOR_LIMIT),
    ("spread_high", ReasonCode.SPREAD_HIGH),
    ("turnover_high", ReasonCode.TURNOVER_HIGH),
    ("regime_uncertain", ReasonCode.REGIME_UNCERTAIN),
    ("risk_estimate_uncertain", ReasonCode.RISK_ESTIMATE_UNCERTAIN),
)


@dataclass(frozen=True, slots=True)
class RiskGovernorPolicy:
    policy_version: str
    reduction_multipliers: Mapping[ReasonCode, Decimal]

    def __post_init__(self) -> None:
        if not self.policy_version.strip():
            raise InvariantViolation("risk governor policy_version cannot be blank")
        expected = {reason for _, reason in SOFT_REDUCTIONS}
        supplied = set(self.reduction_multipliers)
        if supplied != expected:
            raise InvariantViolation("every soft condition requires one explicit policy multiplier")
        normalized = {key: exact_decimal(value) for key, value in self.reduction_multipliers.items()}
        if any(value <= 0 or value >= 1 for value in normalized.values()):
            raise InvariantViolation("reduction multipliers must be strictly between zero and one")
        object.__setattr__(self, "reduction_multipliers", normalized)


@dataclass(frozen=True, slots=True)
class RiskInputs:
    runtime_run_id: str
    portfolio_target_id: str
    portfolio_target_hash: str
    policy_version: str
    as_of_utc: str
    evidence_ids: tuple[str, ...]
    reconciliation_failed: bool = False
    order_state_unknown: bool = False
    initial_cash_unconfirmed: bool = False
    same_run_account_snapshot_missing: bool = False
    clock_risk: bool = False
    execution_price_stale: bool = False
    data_conflict: bool = False
    risk_model_failed: bool = False
    optimizer_infeasible: bool = False
    policy_mismatch: bool = False
    duplicate_order_intent: bool = False
    kill_switch_active: bool = False
    runtime_mode_unauthorized: bool = False
    volatility_high: bool = False
    low_confidence: bool = False
    event_risk_high: bool = False
    sector_concentrated: bool = False
    factor_concentrated: bool = False
    spread_high: bool = False
    turnover_high: bool = False
    regime_uncertain: bool = False
    risk_estimate_uncertain: bool = False
    evidence_insufficient: bool = False
    data_stale: bool = False
    insufficient_cash: bool = False
    cost_exceeds_benefit: bool = False
    defer_execution: bool = False

    def __post_init__(self) -> None:
        for field in fields(self):
            if field.type == "bool" and type(getattr(self, field.name)) is not bool:
                raise NoTrade(f"RISK_INPUT_INVALID: {field.name} must be an explicit boolean")
        identity = (self.runtime_run_id, self.portfolio_target_id, self.portfolio_target_hash,
                    self.policy_version, self.as_of_utc)
        if any(not value.strip() for value in identity):
            raise InvariantViolation("risk inputs require complete runtime, target, policy and time identity")
        if not self.evidence_ids or any(not value.strip() for value in self.evidence_ids):
            raise InvariantViolation("risk inputs require non-empty evidence lineage")
        if len(set(self.evidence_ids)) != len(self.evidence_ids):
            raise InvariantViolation("risk input evidence ids must be unique")

    def canonical(self) -> dict[str, object]:
        return {field.name: (sorted(getattr(self, field.name)) if field.name == "evidence_ids"
                             else getattr(self, field.name)) for field in fields(self)}


@dataclass(frozen=True, slots=True)
class ApprovedRiskDecision:
    risk_decision_id: str
    state: DecisionState
    exposure_multiplier: Decimal
    runtime_run_id: str
    portfolio_target_id: str
    portfolio_target_hash: str
    policy_version: str
    content_hash: str

    def __post_init__(self) -> None:
        if self.state not in (DecisionState.ALLOW, DecisionState.REDUCE):
            raise InvariantViolation("only ALLOW or REDUCE is an approved risk decision")


@dataclass(frozen=True, slots=True)
class RiskDecision:
    risk_decision_id: str
    state: DecisionState
    exposure_multiplier: Decimal
    reason_codes: tuple[ReasonCode, ...]
    runtime_run_id: str
    portfolio_target_id: str
    portfolio_target_hash: str
    policy_version: str
    as_of_utc: str
    evidence_ids: tuple[str, ...]
    content_hash: str

    @property
    def approved(self) -> bool:
        return self.state in (DecisionState.ALLOW, DecisionState.REDUCE)

    def authorize(self) -> ApprovedRiskDecision:
        if not self.approved:
            reasons = ",".join(code.value for code in self.reason_codes)
            raise NoTrade(f"risk decision {self.state.value} cannot authorize an order: {reasons}")
        return ApprovedRiskDecision(
            self.risk_decision_id, self.state, self.exposure_multiplier,
            self.runtime_run_id, self.portfolio_target_id, self.portfolio_target_hash,
            self.policy_version, self.content_hash,
        )

    def apply_to_target(self, weights: Mapping[str, Decimal], *,
                        cash_instrument_id: str) -> dict[str, Decimal]:
        """Apply the approved exposure cap to risky weights and move residual to cash."""
        if not self.approved:
            raise NoTrade(f"{self.state.value} decision cannot produce executable weights")
        normalized = {key: exact_decimal(value) for key, value in weights.items()}
        if not cash_instrument_id.strip() or cash_instrument_id not in normalized:
            raise InvariantViolation("approved target requires an explicit cash instrument")
        if any(not key.strip() or value < 0 for key, value in normalized.items()):
            raise InvariantViolation("target weights require named instruments and non-negative values")
        if sum(normalized.values(), Decimal("0")) != Decimal("1"):
            raise InvariantViolation("approved target weights must sum to one")
        result = {
            key: (value if key == cash_instrument_id else value * self.exposure_multiplier)
            for key, value in normalized.items()
        }
        result[cash_instrument_id] = Decimal("1") - sum(
            (value for key, value in result.items() if key != cash_instrument_id), Decimal("0")
        )
        return result


@dataclass(frozen=True, slots=True)
class GovernanceDecision:
    action: DecisionAction
    reasons: tuple[ReasonCode, ...]


class RiskGovernor:
    def __init__(self, policy: RiskGovernorPolicy | None = None) -> None:
        self.policy = policy

    def decide(self, inputs: RiskInputs) -> RiskDecision:
        if self.policy is None:
            raise InvariantViolation("versioned risk governor policy is required")
        hard = tuple(reason for name, reason in HARD_BLOCKS if getattr(inputs, name))
        if inputs.policy_version != self.policy.policy_version and ReasonCode.POLICY_MISMATCH not in hard:
            hard += (ReasonCode.POLICY_MISMATCH,)
        if hard:
            return self._build(inputs, DecisionState.BLOCK, Decimal("0"), hard)
        abstain: list[ReasonCode] = []
        for active, reason in (
            (inputs.evidence_insufficient, ReasonCode.EVIDENCE_INSUFFICIENT),
            (inputs.data_stale, ReasonCode.DATA_STALE),
            (inputs.insufficient_cash, ReasonCode.INSUFFICIENT_CASH),
            (inputs.cost_exceeds_benefit, ReasonCode.COST_EXCEEDS_BENEFIT),
        ):
            if active:
                abstain.append(reason)
        if abstain:
            return self._build(inputs, DecisionState.ABSTAIN, Decimal("0"), tuple(abstain))
        if inputs.defer_execution:
            reasons = (ReasonCode.EXECUTION_DEFERRED,)
            if inputs.event_risk_high:
                reasons += (ReasonCode.EVENT_RISK_HIGH,)
            return self._build(inputs, DecisionState.DEFER, Decimal("0"), reasons)
        soft = tuple(reason for name, reason in SOFT_REDUCTIONS if getattr(inputs, name))
        if soft:
            multiplier = min(self.policy.reduction_multipliers[reason] for reason in soft)
            return self._build(inputs, DecisionState.REDUCE, multiplier, soft)
        return self._build(inputs, DecisionState.ALLOW, Decimal("1"), ())

    @staticmethod
    def _build(inputs: RiskInputs, state: DecisionState, multiplier: Decimal,
               reasons: tuple[ReasonCode, ...]) -> RiskDecision:
        payload = inputs.canonical() | {
            "state": state.value, "exposure_multiplier": str(multiplier),
            "reason_codes": [reason.value for reason in reasons],
        }
        encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        digest = sha256(encoded.encode("utf-8")).hexdigest()
        return RiskDecision(
            f"risk-{digest}", state, multiplier, reasons, inputs.runtime_run_id,
            inputs.portfolio_target_id, inputs.portfolio_target_hash, inputs.policy_version,
            inputs.as_of_utc, tuple(sorted(inputs.evidence_ids)), digest,
        )

    def evaluate(self, *, statuses: tuple[DataStatus, ...], reconciled: bool,
                 limit_breached: bool) -> GovernanceDecision:
        reasons: list[ReasonCode] = []
        if any(status is not DataStatus.KNOWN for status in statuses):
            reasons.append(ReasonCode.DATA_NOT_KNOWN)
        if not reconciled:
            reasons.append(ReasonCode.RECONCILIATION_REQUIRED)
        if limit_breached:
            reasons.append(ReasonCode.LIMIT_BREACH)
        return (GovernanceDecision(DecisionAction.BLOCK, tuple(reasons)) if reasons else
                GovernanceDecision(DecisionAction.ALLOW, (ReasonCode.PASSED,)))

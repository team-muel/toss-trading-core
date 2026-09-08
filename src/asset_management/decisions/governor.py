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


def target_weight_hash(weights: Mapping[str, Decimal]) -> str:
    """Canonical hash of a complete portfolio target weight map."""
    if not isinstance(weights, Mapping) or not weights:
        raise InvariantViolation("portfolio target weights are required")
    normalized: dict[str, Decimal] = {}
    for key, value in weights.items():
        if not isinstance(key, str) or not key.strip():
            raise InvariantViolation("portfolio target weights require named instruments")
        normalized[key] = exact_decimal(value)
    if (any(value < 0 for value in normalized.values()) or
            sum(normalized.values(), Decimal("0")) != Decimal("1")):
        raise InvariantViolation("portfolio target weights must be non-negative and sum to one")
    payload = {key: str(value) for key, value in sorted(normalized.items())}
    return sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


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


_APPROVAL_ISSUER = object()


@dataclass(frozen=True, slots=True, init=False)
class ApprovedRiskDecision:
    """Governor-issued order authority; callers cannot mint approvals directly."""

    risk_decision_id: str
    state: DecisionState
    exposure_multiplier: Decimal
    runtime_run_id: str
    portfolio_target_id: str
    portfolio_target_hash: str
    policy_version: str
    content_hash: str
    approved_target_hash: str | None = None

    def __init__(self, risk_decision_id: str, state: DecisionState, exposure_multiplier: Decimal,
                 runtime_run_id: str, portfolio_target_id: str, portfolio_target_hash: str,
                 policy_version: str, content_hash: str, approved_target_hash: str | None = None,
                 *, _issuer=None) -> None:
        if _issuer is not _APPROVAL_ISSUER:
            raise InvariantViolation("approved risk decisions must be issued by the risk governor")
        values = {
            "risk_decision_id": risk_decision_id, "state": state,
            "exposure_multiplier": exposure_multiplier, "runtime_run_id": runtime_run_id,
            "portfolio_target_id": portfolio_target_id, "portfolio_target_hash": portfolio_target_hash,
            "policy_version": policy_version, "content_hash": content_hash,
            "approved_target_hash": approved_target_hash,
        }
        for name, value in values.items():
            object.__setattr__(self, name, value)
        if self.state not in (DecisionState.ALLOW, DecisionState.REDUCE):
            raise InvariantViolation("only ALLOW or REDUCE is an approved risk decision")
        if self.approved_target_hash is not None and len(self.approved_target_hash) != 64:
            raise InvariantViolation("approved target hash is invalid")

    @classmethod
    def _issue(cls, risk_decision_id: str, state: DecisionState, exposure_multiplier: Decimal,
               runtime_run_id: str, portfolio_target_id: str, portfolio_target_hash: str,
               policy_version: str, content_hash: str, approved_target_hash: str | None = None
               ) -> "ApprovedRiskDecision":
        return cls(
            risk_decision_id, state, exposure_multiplier, runtime_run_id, portfolio_target_id,
            portfolio_target_hash, policy_version, content_hash, approved_target_hash,
            _issuer=_APPROVAL_ISSUER,
        )

    def bind_target(self, weights: Mapping[str, Decimal], *, cash_instrument_id: str
                    ) -> tuple["ApprovedRiskDecision", dict[str, Decimal]]:
        normalized = {key: exact_decimal(value) for key, value in weights.items()}
        if target_weight_hash(normalized) != self.portfolio_target_hash:
            raise InvariantViolation("portfolio target weights do not match risk-approved target hash")
        if not cash_instrument_id.strip() or cash_instrument_id not in normalized:
            raise InvariantViolation("approved target requires an explicit cash instrument")
        result = {
            key: (value if key == cash_instrument_id else value * self.exposure_multiplier)
            for key, value in normalized.items()
        }
        result[cash_instrument_id] = Decimal("1") - sum(
            (value for key, value in result.items() if key != cash_instrument_id), Decimal("0")
        )
        approved_hash = target_weight_hash(result)
        bound = ApprovedRiskDecision._issue(
            self.risk_decision_id, self.state, self.exposure_multiplier,
            self.runtime_run_id, self.portfolio_target_id, self.portfolio_target_hash,
            self.policy_version, self.content_hash, approved_hash,
        )
        return bound, result


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
        raise InvariantViolation("risk decision authorization requires the issuing risk governor")

    def authorize_target(self, weights: Mapping[str, Decimal], *, cash_instrument_id: str):
        raise InvariantViolation("risk decision authorization requires the issuing risk governor")

    def apply_to_target(self, weights: Mapping[str, Decimal], *, cash_instrument_id: str):
        raise InvariantViolation("risk decision authorization requires the issuing risk governor")


@dataclass(frozen=True, slots=True)
class GovernanceDecision:
    action: DecisionAction
    reasons: tuple[ReasonCode, ...]


class RiskGovernor:
    def __init__(self, policy: RiskGovernorPolicy | None = None) -> None:
        self.policy = policy
        self._issued_decisions: dict[str, RiskDecision] = {}

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

    def _build(self, inputs: RiskInputs, state: DecisionState, multiplier: Decimal,
               reasons: tuple[ReasonCode, ...]) -> RiskDecision:
        payload = inputs.canonical() | {
            "state": state.value, "exposure_multiplier": str(multiplier),
            "reason_codes": [reason.value for reason in reasons],
        }
        encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        digest_value = sha256(encoded.encode("utf-8")).hexdigest()
        decision = RiskDecision(
            f"risk-{digest_value}", state, multiplier, reasons, inputs.runtime_run_id,
            inputs.portfolio_target_id, inputs.portfolio_target_hash, inputs.policy_version,
            inputs.as_of_utc, tuple(sorted(inputs.evidence_ids)), digest_value,
        )
        previous = self._issued_decisions.get(digest_value)
        if previous is not None and previous != decision:
            raise InvariantViolation("risk governor decision hash collision")
        self._issued_decisions[digest_value] = decision
        return decision

    def authorize(self, decision: RiskDecision) -> ApprovedRiskDecision:
        if not isinstance(decision, RiskDecision):
            raise InvariantViolation("risk governor authorization requires a RiskDecision")
        issued = self._issued_decisions.get(decision.content_hash)
        if issued != decision:
            raise InvariantViolation("risk decision was not issued by this risk governor")
        if self.policy is None or decision.policy_version != self.policy.policy_version:
            raise InvariantViolation("risk decision policy is not current for this governor")
        if not decision.approved:
            reasons = ",".join(code.value for code in decision.reason_codes)
            raise NoTrade(f"risk decision {decision.state.value} cannot authorize an order: {reasons}")
        return ApprovedRiskDecision._issue(
            decision.risk_decision_id, decision.state, decision.exposure_multiplier,
            decision.runtime_run_id, decision.portfolio_target_id, decision.portfolio_target_hash,
            decision.policy_version, decision.content_hash,
        )

    def authorize_target(self, decision: RiskDecision, weights: Mapping[str, Decimal], *,
                         cash_instrument_id: str) -> tuple[ApprovedRiskDecision, dict[str, Decimal]]:
        return self.authorize(decision).bind_target(weights, cash_instrument_id=cash_instrument_id)

    def apply_to_target(self, decision: RiskDecision, weights: Mapping[str, Decimal], *,
                        cash_instrument_id: str) -> dict[str, Decimal]:
        _, result = self.authorize_target(decision, weights, cash_instrument_id=cash_instrument_id)
        return result

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

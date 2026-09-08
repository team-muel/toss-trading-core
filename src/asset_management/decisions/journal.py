"""Immutable decision journal and lineage through reconciliation."""

from dataclasses import dataclass
from decimal import Decimal
import json
from pathlib import Path

from asset_management.domain.errors import InvariantViolation

from .governor import DecisionState, RiskDecision
from .reason_codes import ReasonCode


class DecisionJournal:
    """Append-only JSONL journal; replay accepts exact duplicates only."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def append(self, decision: RiskDecision) -> None:
        existing = {item.risk_decision_id: item for item in self.load()}
        previous = existing.get(decision.risk_decision_id)
        if previous is not None:
            if previous != decision:
                raise InvariantViolation("risk decision id cannot be overwritten")
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8", newline="\n") as stream:
            stream.write(json.dumps(self._record(decision), sort_keys=True, separators=(",", ":")))
            stream.write("\n")

    def load(self) -> tuple[RiskDecision, ...]:
        if not self.path.exists():
            return ()
        result: list[RiskDecision] = []
        seen: set[str] = set()
        for line in self.path.read_text(encoding="utf-8").splitlines():
            raw = json.loads(line)
            decision = RiskDecision(
                raw["risk_decision_id"], DecisionState(raw["state"]),
                Decimal(raw["exposure_multiplier"]),
                tuple(ReasonCode(value) for value in raw["reason_codes"]),
                raw["runtime_run_id"], raw["portfolio_target_id"],
                raw["portfolio_target_hash"], raw["policy_version"], raw["as_of_utc"],
                tuple(raw["evidence_ids"]), raw["content_hash"],
            )
            if decision.risk_decision_id != f"risk-{decision.content_hash}":
                raise InvariantViolation("risk decision journal identity hash is invalid")
            if decision.risk_decision_id in seen:
                raise InvariantViolation("risk decision journal contains a duplicate id")
            seen.add(decision.risk_decision_id)
            result.append(decision)
        return tuple(result)

    @staticmethod
    def _record(decision: RiskDecision) -> dict[str, object]:
        return {
            "risk_decision_id": decision.risk_decision_id,
            "state": decision.state.value,
            "exposure_multiplier": str(decision.exposure_multiplier),
            "reason_codes": [code.value for code in decision.reason_codes],
            "runtime_run_id": decision.runtime_run_id,
            "portfolio_target_id": decision.portfolio_target_id,
            "portfolio_target_hash": decision.portfolio_target_hash,
            "policy_version": decision.policy_version,
            "as_of_utc": decision.as_of_utc,
            "evidence_ids": list(decision.evidence_ids),
            "content_hash": decision.content_hash,
        }


@dataclass(frozen=True, slots=True)
class DecisionLineage:
    runtime_run_id: str
    ingestion_run_id: str
    dataset_manifest_id: str
    feature_run_id: str
    state_run_id: str
    pricing_run_id: str
    expectation_run_id: str
    risk_model_run_id: str
    portfolio_target_id: str
    risk_decision_id: str
    order_intent_id: str | None = None
    client_order_id: str | None = None
    broker_order_id: str | None = None
    execution_id: str | None = None
    reconciliation_run_id: str | None = None

    def decision_chain(self) -> tuple[str, ...]:
        return tuple(
            value
            for value in (
                self.runtime_run_id,
                self.ingestion_run_id,
                self.dataset_manifest_id,
                self.feature_run_id,
                self.state_run_id,
                self.pricing_run_id,
                self.expectation_run_id,
                self.risk_model_run_id,
                self.portfolio_target_id,
                self.risk_decision_id,
                self.order_intent_id,
                self.client_order_id,
                self.broker_order_id,
                self.execution_id,
                self.reconciliation_run_id,
            )
            if value is not None
        )

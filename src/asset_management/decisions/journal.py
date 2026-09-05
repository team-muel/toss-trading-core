"""Immutable journal lineage from source data through reconciliation."""

from dataclasses import dataclass


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

"""Persistent, artifact-verified runtime gates for the investment pipeline."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
from enum import IntEnum
from hashlib import sha256
import json
import sqlite3
from typing import Mapping

from asset_management.config.versions import content_hash
from asset_management.domain.errors import InvariantViolation, NoTrade


class PipelineStage(IntEnum):
    INVESTMENT_POLICY = 1
    ACCOUNT_TRUTH = 2
    TIME_TRUTH = 3
    DATA_TRUTH = 4
    FINANCIAL_CALCULATION = 5
    TARGET_PORTFOLIO = 6
    RISK_CONTROL = 7
    ORDER = 8


STAGE_ORDER = tuple(PipelineStage)


@dataclass(frozen=True, slots=True)
class StageEvidence:
    stage: PipelineStage
    evidence_id: str
    content_hash: str

    def __post_init__(self) -> None:
        if not self.evidence_id.strip() or not self.content_hash.strip():
            raise InvariantViolation("stage evidence requires an id and content hash")


class PipelineEvidenceRepository:
    """Records evidence only after resolving it to the named runtime artifact."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn
        self._conn.execute("PRAGMA foreign_keys=ON")

    def load(self, runtime_run_id: str) -> tuple[StageEvidence, ...]:
        rows = self._conn.execute(
            """SELECT stage_name, evidence_id, content_hash
               FROM am_pipeline_stage_evidence WHERE runtime_run_id=?
               ORDER BY stage_no""",
            (runtime_run_id,),
        ).fetchall()
        result = tuple(
            StageEvidence(PipelineStage[str(row[0])], str(row[1]), str(row[2]))
            for row in rows
        )
        if tuple(item.stage for item in result) != STAGE_ORDER[:len(result)]:
            raise InvariantViolation("persisted pipeline evidence is reordered or incomplete")
        return result

    def record(self, runtime_run_id: str, evidence: StageEvidence) -> None:
        completed = self.load(runtime_run_id)
        expected = STAGE_ORDER[len(completed)] if len(completed) < len(STAGE_ORDER) else None
        if evidence.stage is not expected:
            name = expected.name if expected else "NONE"
            raise InvariantViolation(
                f"cannot complete {evidence.stage.name}; required next stage is {name}"
            )
        actual_hash = self._resolve_hash(runtime_run_id, evidence.stage, evidence.evidence_id)
        if actual_hash != evidence.content_hash:
            raise InvariantViolation(
                f"{evidence.stage.name} evidence hash does not match persisted artifact"
            )
        try:
            with self._conn:
                self._conn.execute(
                    """INSERT INTO am_pipeline_stage_evidence
                       (runtime_run_id, stage_no, stage_name, evidence_id, content_hash)
                       VALUES (?, ?, ?, ?, ?)""",
                    (runtime_run_id, int(evidence.stage), evidence.stage.name,
                     evidence.evidence_id, evidence.content_hash),
                )
        except sqlite3.IntegrityError as exc:
            raise InvariantViolation(f"pipeline evidence is not eligible: {exc}") from exc

    def assemble_canonical_decision_request(self, runtime_run_id: str, *,
                                            pricing_applicability_evidence: object):
        """Build a decision request exclusively from verified persisted artifacts.

        Economic values are deliberately not accepted as parameters.  The
        stage ledger first binds every selected artifact to this runtime; this
        assembler then re-reads that exact lineage and validates every JSON
        payload against its stored content hash before exposing it to the
        decision kernel.
        """
        from asset_management.decisions.governor import DecisionState, target_weight_hash
        from .decision_kernel import CanonicalDecisionRequest, FrozenDecisionInput, PricingApplicabilityEvidence

        if not isinstance(pricing_applicability_evidence, PricingApplicabilityEvidence):
            raise InvariantViolation("CANONICAL_ASSEMBLER_PRICING_AUTHORITY_INVALID")
        evidence = self._required_pre_execution_evidence(runtime_run_id)
        row = self._decision_artifact_row(runtime_run_id, evidence)
        feature = self._payload(row["feature_payload"], row["feature_hash"], "FEATURE")
        state = self._payload(row["state_payload"], row["state_hash"], "STATE")
        pricing = self._payload(row["pricing_payload"], row["pricing_hash"], "PRICING")
        expectation = self._payload(row["expectation_payload"], row["expectation_hash"], "EXPECTATION")
        risk_model = self._payload(row["risk_payload"], row["risk_hash"], "RISK")
        target = self._payload(row["target_payload"], row["target_hash"], "TARGET")

        pricing_evidence_id = pricing.get("pricing_applicability_evidence_id")
        if pricing_evidence_id != pricing_applicability_evidence.evidence_id:
            raise InvariantViolation("CANONICAL_ASSEMBLER_PRICING_AUTHORITY_MISMATCH")
        pricing_applicable = pricing.get("pricing_applicable")
        if type(pricing_applicable) is not bool:
            raise InvariantViolation("CANONICAL_ASSEMBLER_PRICING_APPLICABILITY_INVALID")
        non_applicability_reason = pricing.get("pricing_non_applicability_reason")

        target_weights = self._decimal_map(target, "target_weights", "TARGET")
        stored_target_hash = target.get("target_weight_hash")
        if stored_target_hash != target_weight_hash(target_weights):
            raise InvariantViolation("CANONICAL_ASSEMBLER_TARGET_HASH_INVALID")
        risk_state = self._decision_state(row["risk_action"])
        reason_codes = self._reason_codes(row["risk_reason_codes"])
        expected_risk_decision_hash = content_hash({
            "action": risk_state.value,
            "policy_hash": self._hash(row["risk_policy_hash"], "RISK_POLICY_HASH"),
            "policy_version": self._text(row["risk_policy_version"], "RISK_POLICY"),
            "portfolio_target_hash": self._hash(row["target_hash"], "TARGET"),
            "portfolio_target_id": self._text(row["portfolio_target_id"], "TARGET"),
            "reason_codes": list(reason_codes),
        })
        if row["risk_decision_hash"] != expected_risk_decision_hash:
            raise InvariantViolation("CANONICAL_ASSEMBLER_RISK_DECISION_CONTENT_INVALID")

        as_of = self._utc(row["as_of_utc"], "AS_OF")
        cutoff = self._utc(row["information_cutoff_utc"], "INFORMATION_CUTOFF")
        input_values = FrozenDecisionInput(
            snapshot_id=runtime_run_id,
            strategy_key=self._text(expectation.get("strategy_key"), "STRATEGY"),
            model_keys=(self._text(row["feature_version"], "FEATURE_VERSION"),
                        self._text(row["state_version"], "STATE_VERSION"),
                        self._text(row["pricing_version"], "PRICING_VERSION"),
                        self._text(row["expectation_version"], "EXPECTATION_VERSION"),
                        self._text(row["risk_model_version"], "RISK_VERSION")),
            policy_versions={
                "investment": evidence[PipelineStage.INVESTMENT_POLICY].evidence_id,
                "risk": self._text(row["risk_policy_version"], "RISK_POLICY"),
                "pricing_applicability": pricing_applicability_evidence.policy_version,
            },
            parameter_set_key=self._text(row["parameter_set_id"], "PARAMETER_SET"),
            input_manifest_ids=(evidence[PipelineStage.DATA_TRUTH].content_hash,
                                pricing_applicability_evidence.evidence_id),
            as_of=as_of,
            information_cutoff=cutoff,
            code_revision=self._text(row["code_revision"], "CODE_REVISION"),
            pricing_applicability_evidence=pricing_applicability_evidence,
        )
        return CanonicalDecisionRequest._from_persisted_pipeline(
            inputs=input_values,
            feature_values=self._decimal_map(feature, "feature_values", "FEATURE"),
            signal_values=self._decimal_map(state, "signal_values", "STATE"),
            forecast_values=self._decimal_map(expectation, "forecast_values", "EXPECTATION"),
            pricing_outputs=self._decimal_map(pricing, "pricing_outputs", "PRICING", required=pricing_applicable),
            risk_outputs=self._decimal_map(risk_model, "risk_outputs", "RISK"),
            target_weights=target_weights,
            risk_decision_id=self._text(row["risk_decision_id"], "RISK_DECISION"),
            risk_decision_hash=self._hash(row["risk_decision_hash"], "RISK_DECISION"),
            risk_state=risk_state,
            risk_reason_codes=reason_codes,
            order_intent_economics=self._text_map(target, "order_intent_economics", "TARGET",
                                                   required=risk_state in {DecisionState.ALLOW, DecisionState.REDUCE}),
            data_lineage_ids=tuple(evidence[item].content_hash for item in (
                PipelineStage.ACCOUNT_TRUTH, PipelineStage.TIME_TRUTH, PipelineStage.DATA_TRUTH)),
            calculation_lineage_ids=tuple(self._hash(row[column], column.upper()) for column in (
                "feature_hash", "state_hash", "pricing_hash", "expectation_hash", "risk_hash", "target_hash",
                "risk_decision_hash")),
            pricing_applicability_evidence_id=pricing_applicability_evidence.evidence_id,
            pricing_applicable=pricing_applicable,
            pricing_non_applicability_reason=non_applicability_reason,
        )

    def _required_pre_execution_evidence(self, runtime_run_id: str) -> dict[PipelineStage, StageEvidence]:
        values = self.load(runtime_run_id)
        required = STAGE_ORDER[:STAGE_ORDER.index(PipelineStage.RISK_CONTROL) + 1]
        if tuple(item.stage for item in values[:len(required)]) != required:
            raise InvariantViolation("CANONICAL_ASSEMBLER_PIPELINE_INCOMPLETE")
        selected = {item.stage: item for item in values}
        for stage in required:
            item = selected[stage]
            if self._resolve_hash(runtime_run_id, stage, item.evidence_id) != item.content_hash:
                raise InvariantViolation("CANONICAL_ASSEMBLER_PIPELINE_EVIDENCE_CONFLICT")
        return selected

    def _decision_artifact_row(self, runtime_run_id: str,
                               evidence: Mapping[PipelineStage, StageEvidence]) -> Mapping[str, object]:
        previous_factory = self._conn.row_factory
        self._conn.row_factory = sqlite3.Row
        try:
            row = self._conn.execute(
                """SELECT runtime.as_of_utc, runtime.information_cutoff_utc, runtime.code_revision,
                          feature.feature_version, feature.payload_json AS feature_payload, feature.content_hash AS feature_hash,
                          state.state_version, state.payload_json AS state_payload, state.content_hash AS state_hash,
                          pricing.pricing_version, pricing.payload_json AS pricing_payload, pricing.content_hash AS pricing_hash,
                          expectation.expectation_version, expectation.payload_json AS expectation_payload,
                          expectation.content_hash AS expectation_hash,
                          risk_model.risk_model_version, risk_model.payload_json AS risk_payload,
                          risk_model.content_hash AS risk_hash,
                          target.portfolio_target_id, target.parameter_set_id,
                          target.payload_json AS target_payload, target.content_hash AS target_hash,
                          decision.risk_decision_id, decision.action AS risk_action,
                          decision.reason_codes_json AS risk_reason_codes, decision.policy_version AS risk_policy_version,
                          risk_policy.content_hash AS risk_policy_hash,
                          decision.content_hash AS risk_decision_hash
                   FROM am_runtime_run runtime
                   JOIN am_feature_run feature ON feature.runtime_run_id=runtime.runtime_run_id
                   JOIN am_state_run state ON state.feature_run_id=feature.feature_run_id
                   JOIN am_pricing_run pricing ON pricing.state_run_id=state.state_run_id
                   JOIN am_expectation_run expectation ON expectation.pricing_run_id=pricing.pricing_run_id
                   JOIN am_portfolio_target target ON target.expectation_run_id=expectation.expectation_run_id
                   JOIN am_risk_model_run risk_model ON risk_model.risk_model_run_id=target.risk_model_run_id
                   JOIN am_state_run risk_state ON risk_state.state_run_id=risk_model.state_run_id
                   JOIN am_feature_run risk_feature ON risk_feature.feature_run_id=risk_state.feature_run_id
                   JOIN am_risk_decision decision ON decision.portfolio_target_id=target.portfolio_target_id
                   JOIN am_policy_version risk_policy ON risk_policy.policy_version=decision.policy_version
                   WHERE runtime.runtime_run_id=? AND risk_feature.runtime_run_id=?
                     AND expectation.expectation_run_id=? AND target.portfolio_target_id=?
                     AND decision.risk_decision_id=?""",
                (runtime_run_id, runtime_run_id,
                 evidence[PipelineStage.FINANCIAL_CALCULATION].evidence_id,
                 evidence[PipelineStage.TARGET_PORTFOLIO].evidence_id,
                 evidence[PipelineStage.RISK_CONTROL].evidence_id),
            ).fetchone()
            if row is None:
                raise InvariantViolation("CANONICAL_ASSEMBLER_ARTIFACT_LINEAGE_INVALID")
            result = dict(row)
        finally:
            self._conn.row_factory = previous_factory
        for stage, column in ((PipelineStage.FINANCIAL_CALCULATION, "expectation_hash"),
                              (PipelineStage.TARGET_PORTFOLIO, "target_hash"),
                              (PipelineStage.RISK_CONTROL, "risk_decision_hash")):
            if result[column] != evidence[stage].content_hash:
                raise InvariantViolation("CANONICAL_ASSEMBLER_ARTIFACT_HASH_MISMATCH")
        return result

    @staticmethod
    def _payload(raw: object, expected_hash: object, kind: str) -> Mapping[str, object]:
        try:
            value = json.loads(str(raw))
        except (TypeError, json.JSONDecodeError) as exc:
            raise InvariantViolation(f"CANONICAL_ASSEMBLER_{kind}_PAYLOAD_INVALID") from exc
        if not isinstance(value, dict) or content_hash(value) != expected_hash:
            raise InvariantViolation(f"CANONICAL_ASSEMBLER_{kind}_PAYLOAD_INVALID")
        return value

    @staticmethod
    def _text(value: object, field: str) -> str:
        if not isinstance(value, str) or not value.strip():
            raise InvariantViolation(f"CANONICAL_ASSEMBLER_{field}_INVALID")
        return value

    @classmethod
    def _hash(cls, value: object, field: str) -> str:
        value = cls._text(value, field)
        if len(value) != 64 or any(char not in "0123456789abcdef" for char in value):
            raise InvariantViolation(f"CANONICAL_ASSEMBLER_{field}_INVALID")
        return value

    @classmethod
    def _decimal_map(cls, payload: Mapping[str, object], key: str, kind: str, *, required: bool = True) -> Mapping[str, Decimal]:
        raw = payload.get(key)
        if not isinstance(raw, dict) or (required and not raw):
            raise InvariantViolation(f"CANONICAL_ASSEMBLER_{kind}_{key.upper()}_INVALID")
        result: dict[str, Decimal] = {}
        try:
            for name, value in raw.items():
                result[cls._text(name, key.upper())] = Decimal(str(value))
        except (InvalidOperation, ValueError) as exc:
            raise InvariantViolation(f"CANONICAL_ASSEMBLER_{kind}_{key.upper()}_INVALID") from exc
        if any(not value.is_finite() for value in result.values()):
            raise InvariantViolation(f"CANONICAL_ASSEMBLER_{kind}_{key.upper()}_INVALID")
        return result

    @classmethod
    def _text_map(cls, payload: Mapping[str, object], key: str, kind: str, *, required: bool) -> Mapping[str, str]:
        raw = payload.get(key)
        if not isinstance(raw, dict) or (required and not raw):
            raise InvariantViolation(f"CANONICAL_ASSEMBLER_{kind}_{key.upper()}_INVALID")
        return {cls._text(name, key.upper()): cls._text(value, key.upper()) for name, value in raw.items()}

    @classmethod
    def _utc(cls, value: object, field: str) -> datetime:
        try:
            parsed = datetime.fromisoformat(cls._text(value, field))
        except ValueError as exc:
            raise InvariantViolation(f"CANONICAL_ASSEMBLER_{field}_INVALID") from exc
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            raise InvariantViolation(f"CANONICAL_ASSEMBLER_{field}_INVALID")
        return parsed

    @classmethod
    def _decision_state(cls, value: object):
        from asset_management.decisions.governor import DecisionState
        try:
            return DecisionState(cls._text(value, "RISK_ACTION"))
        except ValueError as exc:
            raise InvariantViolation("CANONICAL_ASSEMBLER_RISK_ACTION_INVALID") from exc

    @classmethod
    def _reason_codes(cls, value: object) -> tuple[str, ...]:
        try:
            parsed = json.loads(str(value))
        except json.JSONDecodeError as exc:
            raise InvariantViolation("CANONICAL_ASSEMBLER_RISK_REASONS_INVALID") from exc
        if not isinstance(parsed, list) or any(not isinstance(item, str) or not item.strip() for item in parsed):
            raise InvariantViolation("CANONICAL_ASSEMBLER_RISK_REASONS_INVALID")
        if len(parsed) != len(set(parsed)):
            raise InvariantViolation("CANONICAL_ASSEMBLER_RISK_REASONS_INVALID")
        return tuple(parsed)

    def _resolve_hash(
        self, runtime_run_id: str, stage: PipelineStage, evidence_id: str
    ) -> str:
        if stage is PipelineStage.INVESTMENT_POLICY:
            row = self._conn.execute(
                """SELECT policy.content_hash
                   FROM am_policy_version policy JOIN am_runtime_run runtime
                     ON runtime.runtime_run_id=?
                   WHERE policy.policy_version=? AND policy.policy_kind='investment'
                     AND julianday(policy.effective_from_utc)<=julianday(runtime.as_of_utc)
                     AND (policy.effective_to_utc IS NULL OR
                          julianday(runtime.as_of_utc)<julianday(policy.effective_to_utc))""",
                (runtime_run_id, evidence_id),
            ).fetchone()
        elif stage is PipelineStage.ACCOUNT_TRUTH:
            row = self._conn.execute(
                """SELECT payload_json, content_hash FROM am_account_snapshot
                   WHERE account_snapshot_id=? AND runtime_run_id=?""",
                (evidence_id, runtime_run_id),
            ).fetchone()
            if row is not None:
                encoded = json.dumps(
                    json.loads(str(row[0])), ensure_ascii=False,
                    sort_keys=True, separators=(",", ":"),
                )
                if sha256(encoded.encode("utf-8")).hexdigest() != str(row[1]):
                    raise InvariantViolation("account truth content hash is invalid")
                raw_count = self._conn.execute(
                    """SELECT COUNT(*) FROM am_account_snapshot_raw lineage
                       JOIN am_raw_api_response raw USING(raw_response_id)
                       WHERE lineage.account_snapshot_id=?""",
                    (evidence_id,),
                ).fetchone()[0]
                if raw_count == 0:
                    raise InvariantViolation("account truth has no raw-response lineage")
                row = (row[1],)
        elif stage is PipelineStage.TIME_TRUTH:
            row = self._conn.execute(
                """SELECT as_of_utc, information_cutoff_utc, code_revision
                   FROM am_runtime_run WHERE runtime_run_id=? AND runtime_run_id=?""",
                (evidence_id, runtime_run_id),
            ).fetchone()
            if row is not None:
                row = (content_hash({
                    "runtime_run_id": runtime_run_id,
                    "as_of_utc": str(row[0]),
                    "information_cutoff_utc": str(row[1]),
                    "code_revision": str(row[2]),
                }),)
        elif stage is PipelineStage.DATA_TRUTH:
            row = self._conn.execute(
                """SELECT manifest.content_hash FROM am_dataset_manifest manifest
                   JOIN am_ingestion_run ingestion USING(ingestion_run_id)
                   WHERE manifest.dataset_manifest_id=? AND ingestion.runtime_run_id=?""",
                (evidence_id, runtime_run_id),
            ).fetchone()
        elif stage is PipelineStage.FINANCIAL_CALCULATION:
            row = self._conn.execute(
                """SELECT expectation.content_hash FROM am_expectation_run expectation
                   JOIN am_pricing_run pricing USING(pricing_run_id)
                   JOIN am_state_run state USING(state_run_id)
                   JOIN am_feature_run feature USING(feature_run_id)
                   WHERE expectation.expectation_run_id=? AND feature.runtime_run_id=?""",
                (evidence_id, runtime_run_id),
            ).fetchone()
        elif stage is PipelineStage.TARGET_PORTFOLIO:
            row = self._conn.execute(
                """SELECT target.content_hash FROM am_portfolio_target target
                   JOIN am_expectation_run expectation USING(expectation_run_id)
                   JOIN am_pricing_run pricing USING(pricing_run_id)
                   JOIN am_state_run state USING(state_run_id)
                   JOIN am_feature_run feature USING(feature_run_id)
                   JOIN am_risk_model_run risk_model USING(risk_model_run_id)
                   JOIN am_state_run risk_state ON risk_state.state_run_id=risk_model.state_run_id
                   JOIN am_feature_run risk_feature ON risk_feature.feature_run_id=risk_state.feature_run_id
                   WHERE target.portfolio_target_id=? AND feature.runtime_run_id=?
                     AND risk_feature.runtime_run_id=?""",
                (evidence_id, runtime_run_id, runtime_run_id),
            ).fetchone()
        elif stage is PipelineStage.RISK_CONTROL:
            row = self._conn.execute(
                """SELECT decision.content_hash FROM am_risk_decision decision
                   JOIN am_portfolio_target target USING(portfolio_target_id)
                   JOIN am_expectation_run expectation USING(expectation_run_id)
                   JOIN am_pricing_run pricing USING(pricing_run_id)
                   JOIN am_state_run state USING(state_run_id)
                   JOIN am_feature_run feature USING(feature_run_id)
                   JOIN am_risk_model_run risk_model USING(risk_model_run_id)
                   JOIN am_state_run risk_state ON risk_state.state_run_id=risk_model.state_run_id
                   JOIN am_feature_run risk_feature ON risk_feature.feature_run_id=risk_state.feature_run_id
                   WHERE decision.risk_decision_id=? AND decision.action IN ('ALLOW','REDUCE')
                     AND feature.runtime_run_id=? AND risk_feature.runtime_run_id=?""",
                (evidence_id, runtime_run_id, runtime_run_id),
            ).fetchone()
        else:
            row = self._conn.execute(
                """SELECT content_hash FROM am_order_intent
                   WHERE order_intent_id=? AND runtime_run_id=?""",
                (evidence_id, runtime_run_id),
            ).fetchone()
        if row is None:
            raise InvariantViolation(
                f"{stage.name} evidence does not resolve to runtime {runtime_run_id}"
            )
        return str(row[0])


class InvestmentPipeline:
    """Pipeline state backed by verified, immutable database evidence."""

    def __init__(self, runtime_run_id: str, repository: PipelineEvidenceRepository) -> None:
        if not runtime_run_id.strip():
            raise InvariantViolation("runtime_run_id cannot be blank")
        self.runtime_run_id = runtime_run_id
        self._repository = repository
        if self._repository._conn.execute(
            "SELECT 1 FROM am_runtime_run WHERE runtime_run_id=?", (runtime_run_id,)
        ).fetchone() is None:
            raise InvariantViolation("pipeline runtime run does not exist")

    @classmethod
    def start(cls, conn: sqlite3.Connection, runtime_run_id: str) -> "InvestmentPipeline":
        return cls(runtime_run_id, PipelineEvidenceRepository(conn))

    @property
    def completed(self) -> tuple[StageEvidence, ...]:
        return self._repository.load(self.runtime_run_id)

    @property
    def next_stage(self) -> PipelineStage | None:
        completed = self.completed
        return STAGE_ORDER[len(completed)] if len(completed) < len(STAGE_ORDER) else None

    def complete(self, evidence: StageEvidence) -> "InvestmentPipeline":
        self._repository.record(self.runtime_run_id, evidence)
        return self

    def require_order_authorized(self) -> None:
        completed = self.completed
        required = STAGE_ORDER.index(PipelineStage.RISK_CONTROL) + 1
        if len(completed) < required:
            raise NoTrade("order is blocked until every verified stage through RISK_CONTROL exists")

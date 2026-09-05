"""Persistent, artifact-verified runtime gates for the investment pipeline."""

from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum
from hashlib import sha256
import json
import sqlite3

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

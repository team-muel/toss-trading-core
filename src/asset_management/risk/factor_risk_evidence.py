"""Persisted, replayable factor-risk calculation evidence.

The numerical factor-risk helper is intentionally not a production authority.
This repository assembles its return panel from immutable PIT observations,
persists the exact estimator inputs and output, and replays them before an
assessment can be consumed by an execution-facing caller.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
import json
import re
import sqlite3
from collections.abc import Mapping, Sequence

from asset_management.data.asof_query import AsOfRepository
from asset_management.data.immutable import canonical, digest
from asset_management.data.repositories import observation_from_row
from asset_management.domain.economics import CurrencyBasis
from asset_management.domain.errors import DataQualityError, InvariantViolation
from asset_management.governance import (
    ModelScope, RuntimeModelAuthorization, RuntimeModelRegistryEvidenceRepository,
)
from asset_management.reference.calendars import SessionRepository
from asset_management.time.asof import AsOfContext, require_as_of_context
from asset_management.time.clock import Clock

from .factor_risk import (
    FactorExposure, FactorRiskAssessment, SpecificRiskPolicy,
    assess_factor_specific_risk,
)
from .covariance import sample_covariance
from .models import MissingPolicy, ReturnPanel
from .returns import build_return_panel


_HASH = re.compile(r"[0-9a-f]{64}")
_OBSERVATION_COLUMNS = """
observation_id, entity_id, field_name, value_json, reference_period,
event_time_utc, scheduled_release_at_utc, official_release_at_utc,
source_timestamp_utc, received_at_utc, available_at_utc, ingested_at_utc,
revised_at_utc, source_timezone, schema_version, raw_response_id,
dataset_manifest_id, supersedes_observation_id, content_hash
"""


def _utc(value: object, reason: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise InvariantViolation(reason)
    return value.astimezone(timezone.utc)


def _stored_utc(value: object, reason: str) -> datetime:
    try:
        return _utc(datetime.fromisoformat(str(value)), reason)
    except (TypeError, ValueError) as exc:
        raise InvariantViolation(reason) from exc


def _decimal(value: object, reason: str) -> Decimal:
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise InvariantViolation(reason) from exc
    if not result.is_finite():
        raise InvariantViolation(reason)
    return result


def _matrix(value: object, reason: str) -> tuple[tuple[Decimal, ...], ...]:
    if not isinstance(value, list) or not value or any(not isinstance(row, list) for row in value):
        raise InvariantViolation(reason)
    result = tuple(tuple(_decimal(item, reason) for item in row) for row in value)
    if any(len(row) != len(result) for row in result):
        raise InvariantViolation(reason)
    return result


def _rows(value: object, reason: str) -> tuple[tuple[Decimal, ...], ...]:
    if not isinstance(value, list) or not value or any(not isinstance(row, list) for row in value):
        raise InvariantViolation(reason)
    result = tuple(tuple(_decimal(item, reason) for item in row) for row in value)
    if not result[0] or any(len(row) != len(result[0]) for row in result):
        raise InvariantViolation(reason)
    return result


def _payload_matrix(value: Sequence[Sequence[Decimal]]) -> list[list[str]]:
    return [[str(item) for item in row] for row in value]


@dataclass(frozen=True, slots=True)
class PersistedFactorRiskAssessment:
    """The only factor-risk assessment eligible for production consumption."""

    factor_risk_calculation_id: str
    runtime_run_id: str
    content_hash: str
    assessment: FactorRiskAssessment

    def __post_init__(self) -> None:
        if (not _HASH.fullmatch(self.factor_risk_calculation_id) or
                not isinstance(self.runtime_run_id, str) or not self.runtime_run_id.strip() or
                not _HASH.fullmatch(self.content_hash) or
                not isinstance(self.assessment, FactorRiskAssessment)):
            raise InvariantViolation("FACTOR_RISK_PERSISTED_ASSESSMENT_INVALID")


class FactorRiskCalculationRepository:
    """Build and revalidate immutable factor-risk evidence for one runtime."""

    def __init__(self, conn: sqlite3.Connection, clock: Clock) -> None:
        if not isinstance(conn, sqlite3.Connection) or not hasattr(clock, "now_utc"):
            raise InvariantViolation("FACTOR_RISK_EVIDENCE_REPOSITORY_INVALID")
        self._conn = conn
        self._clock = clock
        self._conn.execute("PRAGMA foreign_keys=ON")

    def record_estimator_evidence(self, *, context: AsOfContext, model_key: str,
                                  model_registry_evidence: RuntimeModelRegistryEvidenceRepository,
                                  runtime_authorization: RuntimeModelAuthorization,
                                  exposures: Sequence[FactorExposure], factor_matrix: Sequence[Sequence[Decimal]],
                                  residual_variance: Sequence[Decimal], residual_history: Sequence[int],
                                  residual_serial_correlation: Sequence[Decimal],
                                  residual_heteroskedasticity: Sequence[Decimal], policy: SpecificRiskPolicy,
                                  maximum_exposure_age_days: int,
                                  maximum_return_age_days: int) -> str:
        """Publish estimator inputs before, never during, a risk calculation."""
        context = require_as_of_context(context)
        self._require_runtime(context)
        self._require_model(model_registry_evidence, runtime_authorization, model_key, context)
        if (type(maximum_exposure_age_days) is not int or maximum_exposure_age_days < 0 or
                type(maximum_return_age_days) is not int or maximum_return_age_days < 0):
            raise DataQualityError("FACTOR_RISK_ESTIMATION_LINEAGE_UNVERIFIED")
        recorded = _utc(self._clock.now_utc(), "FACTOR_RISK_CALCULATION_TIME_INVALID")
        if recorded > context.information_cutoff_utc:
            raise InvariantViolation("FACTOR_RISK_ESTIMATION_LINEAGE_UNVERIFIED")
        estimator = self._estimator_payload(
            exposures=exposures, factor_matrix=factor_matrix, residual_variance=residual_variance,
            residual_history=residual_history, residual_serial_correlation=residual_serial_correlation,
            residual_heteroskedasticity=residual_heteroskedasticity, policy=policy,
            full_covariance=None,
        ) | {"maximum_exposure_age_days": maximum_exposure_age_days,
             "maximum_return_age_days": maximum_return_age_days,
             "recorded_at": recorded.isoformat()}
        evidence_id = digest(canonical({"runtime_run_id": context.run_id, "model_key": model_key,
                                        "estimator": estimator}))
        row = self._conn.execute(
            "SELECT runtime_run_id, model_key, payload_json, content_hash, recorded_at_utc FROM am_factor_risk_estimator_evidence WHERE factor_risk_estimator_evidence_id=?",
            (evidence_id,)).fetchone()
        if row is not None:
            if (row[0] != context.run_id or row[1] != model_key or row[3] != evidence_id or
                    row[4] != recorded.isoformat() or json.loads(str(row[2])) != estimator):
                raise InvariantViolation("FACTOR_RISK_ESTIMATION_LINEAGE_UNVERIFIED")
            return evidence_id
        with self._conn:
            self._conn.execute(
                "INSERT INTO am_factor_risk_estimator_evidence VALUES (?, ?, ?, ?, ?, ?)",
                (evidence_id, context.run_id, model_key,
                 json.dumps(estimator, sort_keys=True, separators=(",", ":")), evidence_id, recorded.isoformat()))
        return evidence_id

    def calculate(self, *, context: AsOfContext, instruments: Sequence[str], return_field: str,
                  calendar_id: str, currency_basis: CurrencyBasis, missing_policy: MissingPolicy,
                  estimator_evidence_id: str, model_key: str,
                  model_registry_evidence: RuntimeModelRegistryEvidenceRepository,
                  runtime_authorization: RuntimeModelAuthorization) -> PersistedFactorRiskAssessment:
        """Create evidence from persisted, cutoff-visible total-return observations only."""
        context = require_as_of_context(context)
        if (not isinstance(currency_basis, CurrencyBasis) or not isinstance(missing_policy, MissingPolicy) or
                not isinstance(return_field, str) or not return_field.strip() or
                not isinstance(calendar_id, str) or not calendar_id.strip() or
                not isinstance(estimator_evidence_id, str) or not _HASH.fullmatch(estimator_evidence_id)):
            raise DataQualityError("FACTOR_RISK_ESTIMATION_LINEAGE_UNVERIFIED")
        names = tuple(instruments)
        if (not names or len(names) != len(set(names)) or any(not isinstance(name, str) or not name.strip() for name in names)):
            raise DataQualityError("FACTOR_RISK_ESTIMATION_LINEAGE_UNVERIFIED")
        self._require_runtime(context)
        self._require_model(model_registry_evidence, runtime_authorization, model_key, context)
        estimator = self._estimator_evidence(context, estimator_evidence_id, model_key)
        panel, observation_lineage = self._return_panel(
            context=context, instruments=names, return_field=return_field,
            calendar_id=calendar_id, currency_basis=currency_basis, missing_policy=missing_policy,
        )
        assessment = self._assess(panel=panel, estimator=estimator, currency_basis=currency_basis,
                                  context=context)
        input_lineage = {
            "runtime_run_id": context.run_id, "model_key": model_key,
            "estimator_evidence_id": estimator_evidence_id,
            "as_of": context.as_of_utc.isoformat(),
            "information_cutoff": context.information_cutoff_utc.isoformat(),
            "code_revision": context.code_revision,
            "return_field": return_field,
            "calendar_id": calendar_id,
            "currency_basis": currency_basis.value,
            "missing_policy": missing_policy.value,
            "return_panel": self._panel_payload(panel),
            "observations": observation_lineage,
        }
        assessment_payload = assessment.payload()
        body = {"input_lineage": input_lineage, "estimator": estimator,
                "assessment": assessment_payload}
        content_hash = digest(canonical(body))
        calculated = _utc(self._clock.now_utc(), "FACTOR_RISK_CALCULATION_TIME_INVALID")
        existing = self._conn.execute(
            "SELECT runtime_run_id, content_hash, assessment_payload_json FROM am_factor_risk_calculation WHERE factor_risk_calculation_id=?",
            (content_hash,),
        ).fetchone()
        if existing is None:
            with self._conn:
                self._conn.execute(
                    """INSERT INTO am_factor_risk_calculation
                       (factor_risk_calculation_id, runtime_run_id, factor_risk_estimator_evidence_id, input_lineage_json,
                        estimator_payload_json, assessment_payload_json, content_hash, calculated_at_utc)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                    (content_hash, context.run_id, estimator_evidence_id,
                     json.dumps(input_lineage, sort_keys=True, separators=(",", ":")),
                     json.dumps(estimator, sort_keys=True, separators=(",", ":")),
                     json.dumps(assessment_payload, sort_keys=True, separators=(",", ":")),
                     content_hash, calculated.isoformat()),
                )
        elif (existing[0] != context.run_id or existing[1] != content_hash or
              json.loads(str(existing[2])) != assessment_payload):
            raise InvariantViolation("FACTOR_RISK_CALCULATION_CONFLICT")
        return PersistedFactorRiskAssessment(content_hash, context.run_id, content_hash, assessment)

    def require(self, persisted: PersistedFactorRiskAssessment, *, model_registry_evidence: RuntimeModelRegistryEvidenceRepository,
                runtime_authorization: RuntimeModelAuthorization) -> FactorRiskAssessment:
        """Replay persisted inputs and fail closed on stale, missing, or changed evidence."""
        if not isinstance(persisted, PersistedFactorRiskAssessment):
            raise InvariantViolation("FACTOR_RISK_ESTIMATION_LINEAGE_UNVERIFIED")
        row = self._conn.execute(
            """SELECT runtime_run_id, input_lineage_json, estimator_payload_json,
                      assessment_payload_json, content_hash
               FROM am_factor_risk_calculation WHERE factor_risk_calculation_id=?""",
            (persisted.factor_risk_calculation_id,),
        ).fetchone()
        if row is None or row[0] != persisted.runtime_run_id or row[4] != persisted.content_hash:
            raise InvariantViolation("FACTOR_RISK_ESTIMATION_LINEAGE_UNVERIFIED")
        try:
            lineage = json.loads(str(row[1])); estimator = json.loads(str(row[2])); stored = json.loads(str(row[3]))
        except (TypeError, json.JSONDecodeError) as exc:
            raise InvariantViolation("FACTOR_RISK_ESTIMATION_LINEAGE_UNVERIFIED") from exc
        if digest(canonical({"input_lineage": lineage, "estimator": estimator, "assessment": stored})) != row[4]:
            raise InvariantViolation("FACTOR_RISK_ESTIMATION_LINEAGE_UNVERIFIED")
        context = self._context_from_lineage(lineage)
        self._require_runtime(context)
        self._require_model(model_registry_evidence, runtime_authorization, str(lineage.get("model_key")), context)
        if not isinstance(lineage.get("estimator_evidence_id"), str):
            raise InvariantViolation("FACTOR_RISK_ESTIMATION_LINEAGE_UNVERIFIED")
        if self._estimator_evidence(context, str(lineage["estimator_evidence_id"]), str(lineage["model_key"])) != estimator:
            raise InvariantViolation("FACTOR_RISK_ESTIMATION_LINEAGE_UNVERIFIED")
        panel = self._replay_panel(lineage, context)
        assessment = self._assess(panel=panel, estimator=estimator,
                                  currency_basis=CurrencyBasis(lineage["currency_basis"]), context=context)
        if assessment.payload() != stored or assessment != persisted.assessment:
            raise InvariantViolation("FACTOR_RISK_ESTIMATION_LINEAGE_UNVERIFIED")
        return assessment

    def replay(self, factor_risk_calculation_id: str, *,
               model_registry_evidence: RuntimeModelRegistryEvidenceRepository
               ) -> PersistedFactorRiskAssessment:
        """Replay a stored calculation without accepting a caller-built assessment.

        Production consumers have only the immutable calculation identifier.  The
        model key, runtime, estimator, observations, and assessment are all read
        back from the evidence store, then recomputed.  This is intentionally a
        separate entry point from :meth:`require`: callers cannot replace the
        persisted assessment with an in-memory value while asking for replay.
        """
        if (not isinstance(factor_risk_calculation_id, str) or
                _HASH.fullmatch(factor_risk_calculation_id) is None or
                not isinstance(model_registry_evidence, RuntimeModelRegistryEvidenceRepository) or
                model_registry_evidence._conn is not self._conn):
            raise InvariantViolation("FACTOR_RISK_ESTIMATION_LINEAGE_UNVERIFIED")
        row = self._conn.execute(
            """SELECT runtime_run_id, input_lineage_json, estimator_payload_json,
                      assessment_payload_json, content_hash
               FROM am_factor_risk_calculation WHERE factor_risk_calculation_id=?""",
            (factor_risk_calculation_id,),
        ).fetchone()
        if row is None or row[4] != factor_risk_calculation_id:
            raise InvariantViolation("FACTOR_RISK_ESTIMATION_LINEAGE_UNVERIFIED")
        try:
            lineage = json.loads(str(row[1])); estimator = json.loads(str(row[2])); stored = json.loads(str(row[3]))
        except (TypeError, json.JSONDecodeError) as exc:
            raise InvariantViolation("FACTOR_RISK_ESTIMATION_LINEAGE_UNVERIFIED") from exc
        if (not isinstance(lineage, Mapping) or not isinstance(estimator, Mapping) or
                not isinstance(stored, Mapping) or
                digest(canonical({"input_lineage": lineage, "estimator": estimator,
                                  "assessment": stored})) != row[4]):
            raise InvariantViolation("FACTOR_RISK_ESTIMATION_LINEAGE_UNVERIFIED")
        context = self._context_from_lineage(lineage)
        model_key = lineage.get("model_key")
        if context.run_id != row[0] or not isinstance(model_key, str) or not model_key.strip():
            raise InvariantViolation("FACTOR_RISK_ESTIMATION_LINEAGE_UNVERIFIED")
        self._require_runtime(context)
        authorization = model_registry_evidence.authorize(
            context.run_id, model_key=model_key, scope=ModelScope.RISK_ESTIMATION)
        self._require_model(model_registry_evidence, authorization, model_key, context)
        estimator_evidence_id = lineage.get("estimator_evidence_id")
        if not isinstance(estimator_evidence_id, str):
            raise InvariantViolation("FACTOR_RISK_ESTIMATION_LINEAGE_UNVERIFIED")
        if self._estimator_evidence(context, estimator_evidence_id, model_key) != estimator:
            raise InvariantViolation("FACTOR_RISK_ESTIMATION_LINEAGE_UNVERIFIED")
        panel = self._replay_panel(lineage, context)
        assessment = self._assess(panel=panel, estimator=estimator,
                                  currency_basis=CurrencyBasis(lineage["currency_basis"]), context=context)
        if assessment.payload() != stored:
            raise InvariantViolation("FACTOR_RISK_ESTIMATION_LINEAGE_UNVERIFIED")
        return PersistedFactorRiskAssessment(
            factor_risk_calculation_id, context.run_id, str(row[4]), assessment)

    def _require_runtime(self, context: AsOfContext) -> None:
        row = self._conn.execute(
            "SELECT as_of_utc, information_cutoff_utc, code_revision FROM am_runtime_run WHERE runtime_run_id=?",
            (context.run_id,),
        ).fetchone()
        if (row is None or _stored_utc(row[0], "FACTOR_RISK_ESTIMATION_LINEAGE_UNVERIFIED") != context.as_of_utc or
                _stored_utc(row[1], "FACTOR_RISK_ESTIMATION_LINEAGE_UNVERIFIED") != context.information_cutoff_utc or
                str(row[2]) != context.code_revision):
            raise InvariantViolation("FACTOR_RISK_ESTIMATION_LINEAGE_UNVERIFIED")

    def _require_model(self, model_registry_evidence: RuntimeModelRegistryEvidenceRepository,
                       runtime_authorization: RuntimeModelAuthorization, model_key: str,
                       context: AsOfContext) -> None:
        if (not isinstance(model_registry_evidence, RuntimeModelRegistryEvidenceRepository) or
                not isinstance(runtime_authorization, RuntimeModelAuthorization) or
                model_registry_evidence._conn is not self._conn or
                runtime_authorization.runtime_run_id != context.run_id):
            raise InvariantViolation("FACTOR_RISK_ESTIMATION_LINEAGE_UNVERIFIED")
        model_registry_evidence.require_authorization(
            runtime_authorization, model_key=model_key, scope=ModelScope.RISK_ESTIMATION,
            at=context.as_of_utc)

    def _estimator_evidence(self, context: AsOfContext, evidence_id: str, model_key: str) -> dict[str, object]:
        row = self._conn.execute(
            """SELECT runtime_run_id, model_key, payload_json, content_hash, recorded_at_utc
               FROM am_factor_risk_estimator_evidence WHERE factor_risk_estimator_evidence_id=?""",
            (evidence_id,)).fetchone()
        try:
            estimator = json.loads(str(row[2])) if row is not None else None
        except (TypeError, json.JSONDecodeError) as exc:
            raise InvariantViolation("FACTOR_RISK_ESTIMATION_LINEAGE_UNVERIFIED") from exc
        if (row is None or row[0] != context.run_id or row[1] != model_key or row[3] != evidence_id or
                not isinstance(estimator, dict) or
                _stored_utc(row[4], "FACTOR_RISK_ESTIMATION_LINEAGE_UNVERIFIED") > context.information_cutoff_utc or
                estimator.get("recorded_at") != _stored_utc(row[4], "FACTOR_RISK_ESTIMATION_LINEAGE_UNVERIFIED").isoformat() or
                digest(canonical({"runtime_run_id": context.run_id, "model_key": model_key,
                                  "estimator": estimator})) != evidence_id):
            raise InvariantViolation("FACTOR_RISK_ESTIMATION_LINEAGE_UNVERIFIED")
        return estimator

    def _return_panel(self, *, context: AsOfContext, instruments: tuple[str, ...], return_field: str,
                      calendar_id: str, currency_basis: CurrencyBasis, missing_policy: MissingPolicy
                      ) -> tuple[ReturnPanel, list[dict[str, str]]]:
        repository = AsOfRepository(self._conn)
        series = {instrument: repository.series(entity_id=instrument, field=return_field, context=context)
                  for instrument in instruments}
        values: dict[date, dict[str, Decimal | None]] = {}
        available: dict[date, datetime] = {}
        lineage: list[dict[str, str]] = []
        manifests: set[str] = set()
        for instrument, observations in series.items():
            for observation in observations:
                try:
                    day = date.fromisoformat(observation.reference_period)
                    raw = observation.value
                    if not isinstance(raw, Mapping) or set(raw) != {"return", "calendar_id", "currency_basis", "total_return"}:
                        raise ValueError
                    if raw["calendar_id"] != calendar_id or raw["currency_basis"] != currency_basis.value or raw["total_return"] is not True:
                        raise ValueError
                    value = _decimal(raw["return"], "FACTOR_RISK_ESTIMATION_LINEAGE_UNVERIFIED")
                except (TypeError, ValueError) as exc:
                    raise DataQualityError("FACTOR_RISK_ESTIMATION_LINEAGE_UNVERIFIED") from exc
                if observation.event_time.date() != day or observation.dataset_manifest_id is None:
                    raise DataQualityError("FACTOR_RISK_ESTIMATION_LINEAGE_UNVERIFIED")
                try:
                    session = SessionRepository(self._conn).session(calendar_id, day.isoformat(), context)
                except DataQualityError as exc:
                    raise DataQualityError("FACTOR_RISK_ESTIMATION_LINEAGE_UNVERIFIED") from exc
                if session.get("session_status") != "OPEN":
                    raise DataQualityError("FACTOR_RISK_ESTIMATION_LINEAGE_UNVERIFIED")
                manifest = self._conn.execute(
                    """SELECT manifest.content_hash FROM am_dataset_manifest manifest
                       JOIN am_ingestion_run ingestion USING(ingestion_run_id)
                       WHERE manifest.dataset_manifest_id=? AND ingestion.runtime_run_id=?""",
                    (observation.dataset_manifest_id, context.run_id),
                ).fetchone()
                if manifest is None or not _HASH.fullmatch(str(manifest[0])):
                    raise DataQualityError("FACTOR_RISK_ESTIMATION_LINEAGE_UNVERIFIED")
                manifests.add(str(manifest[0]))
                values.setdefault(day, {})[instrument] = value
                available[day] = max(available.get(day, observation.available_at), observation.available_at)
                lineage.append({"observation_id": observation.observation_id, "content_hash": observation.content_hash,
                                "manifest_hash": str(manifest[0])})
        try:
            panel = build_return_panel(instruments=instruments, observations=values, total_return=True,
                currency_basis=currency_basis, information_cutoff=context.information_cutoff_utc,
                dataset_manifest_ids=tuple(sorted(manifests)), available_at=available,
                missing_policy=missing_policy)
        except (DataQualityError, ValueError) as exc:
            raise DataQualityError("FACTOR_RISK_ESTIMATION_LINEAGE_UNVERIFIED") from exc
        return panel, sorted(lineage, key=lambda item: item["observation_id"])

    @staticmethod
    def _panel_payload(panel: ReturnPanel) -> dict[str, object]:
        return {"instruments": list(panel.instruments), "dates": [item.isoformat() for item in panel.dates],
                "returns": _payload_matrix(panel.returns), "total_return": panel.total_return,
                "currency_basis": panel.currency_basis.value, "missing_policy": panel.missing_policy.value,
                "information_cutoff": panel.information_cutoff.isoformat(),
                "dataset_manifest_ids": list(panel.dataset_manifest_ids),
                "available_at": [item.isoformat() for item in panel.available_at],
                "dropped_rows": panel.dropped_rows, "outlier_policy": panel.outlier_policy,
                "prelisting_rows_excluded": panel.prelisting_rows_excluded,
                "periods_per_year": panel.periods_per_year}

    @staticmethod
    def _estimator_payload(*, exposures: Sequence[FactorExposure], factor_matrix: Sequence[Sequence[Decimal]],
                           residual_variance: Sequence[Decimal], residual_history: Sequence[int],
                           residual_serial_correlation: Sequence[Decimal],
                           residual_heteroskedasticity: Sequence[Decimal], policy: SpecificRiskPolicy,
                           full_covariance: Sequence[Sequence[Decimal]] | None) -> dict[str, object]:
        try:
            exposed = tuple(exposures)
            if not exposed or any(not isinstance(item, FactorExposure) for item in exposed):
                raise ValueError
            payload = {
                "exposures": [{"instrument_id": item.instrument_id, "loadings": [str(value) for value in item.loadings],
                               "as_of": item.as_of.isoformat(), "available_at": item.available_at.isoformat(),
                               "source_version": item.source_version} for item in exposed],
                "factor_matrix": _payload_matrix(factor_matrix),
                "residual_variance": [str(_decimal(item, "FACTOR_RISK_ESTIMATION_LINEAGE_UNVERIFIED")) for item in residual_variance],
                "residual_history": list(residual_history),
                "residual_serial_correlation": [str(_decimal(item, "FACTOR_RISK_ESTIMATION_LINEAGE_UNVERIFIED")) for item in residual_serial_correlation],
                "residual_heteroskedasticity": [str(_decimal(item, "FACTOR_RISK_ESTIMATION_LINEAGE_UNVERIFIED")) for item in residual_heteroskedasticity],
                "policy": {"minimum_history": policy.minimum_history,
                           "residual_variance_floor": str(policy.residual_variance_floor),
                           "shrinkage_weight": str(policy.shrinkage_weight),
                           "winsorization_limit": str(policy.winsorization_limit),
                           "estimation_version": policy.estimation_version},
                "full_covariance": _payload_matrix(full_covariance) if full_covariance is not None else None,
            }
            if any(type(item) is not int or item < 0 for item in residual_history):
                raise ValueError
            return payload
        except (TypeError, ValueError) as exc:
            raise DataQualityError("FACTOR_RISK_ESTIMATION_LINEAGE_UNVERIFIED") from exc

    @staticmethod
    def _assess(*, panel: ReturnPanel, estimator: Mapping[str, object], currency_basis: CurrencyBasis,
                context: AsOfContext) -> FactorRiskAssessment:
        try:
            exposure_values = estimator["exposures"]
            if not isinstance(exposure_values, list):
                raise ValueError
            exposures = tuple(FactorExposure(str(item["instrument_id"]),
                tuple(_decimal(value, "FACTOR_RISK_ESTIMATION_LINEAGE_UNVERIFIED") for value in item["loadings"]),
                _stored_utc(item["as_of"], "FACTOR_RISK_ESTIMATION_LINEAGE_UNVERIFIED"),
                _stored_utc(item["available_at"], "FACTOR_RISK_ESTIMATION_LINEAGE_UNVERIFIED"),
                str(item["source_version"])) for item in exposure_values if isinstance(item, Mapping))
            if len(exposures) != len(exposure_values) or tuple(item.instrument_id for item in exposures) != panel.instruments:
                raise ValueError
            policy_raw = estimator["policy"]
            if not isinstance(policy_raw, Mapping):
                raise ValueError
            policy = SpecificRiskPolicy(int(policy_raw["minimum_history"]),
                _decimal(policy_raw["residual_variance_floor"], "FACTOR_RISK_ESTIMATION_LINEAGE_UNVERIFIED"),
                _decimal(policy_raw["shrinkage_weight"], "FACTOR_RISK_ESTIMATION_LINEAGE_UNVERIFIED"),
                _decimal(policy_raw["winsorization_limit"], "FACTOR_RISK_ESTIMATION_LINEAGE_UNVERIFIED"),
                str(policy_raw["estimation_version"]))
            max_exposure_age = estimator["maximum_exposure_age_days"]
            max_return_age = estimator["maximum_return_age_days"]
            if (type(max_exposure_age) is not int or max_exposure_age < 0 or
                    type(max_return_age) is not int or max_return_age < 0 or
                    any(item.as_of < context.as_of_utc - timedelta(days=max_exposure_age) for item in exposures) or
                    not panel.dates or panel.dates[-1] < context.as_of_utc.date() - timedelta(days=max_return_age)):
                raise InvariantViolation("FACTOR_RISK_ESTIMATION_LINEAGE_UNVERIFIED")
            full_raw = estimator.get("full_covariance")
            observed_covariance = sample_covariance(panel).matrix
            if (full_raw is not None and
                    _matrix(full_raw, "FACTOR_RISK_ESTIMATION_LINEAGE_UNVERIFIED") != observed_covariance):
                raise InvariantViolation("FACTOR_RISK_ESTIMATION_LINEAGE_UNVERIFIED")
            return assess_factor_specific_risk(
                exposures=exposures, factor_matrix=_matrix(estimator["factor_matrix"], "FACTOR_RISK_ESTIMATION_LINEAGE_UNVERIFIED"),
                residual_variance=tuple(_decimal(item, "FACTOR_RISK_ESTIMATION_LINEAGE_UNVERIFIED") for item in estimator["residual_variance"]),
                residual_history=tuple(estimator["residual_history"]),
                residual_serial_correlation=tuple(_decimal(item, "FACTOR_RISK_ESTIMATION_LINEAGE_UNVERIFIED") for item in estimator["residual_serial_correlation"]),
                residual_heteroskedasticity=tuple(_decimal(item, "FACTOR_RISK_ESTIMATION_LINEAGE_UNVERIFIED") for item in estimator["residual_heteroskedasticity"]),
                policy=policy, currency_basis=currency_basis, as_of=context.as_of_utc,
                information_cutoff=context.information_cutoff_utc,
                full_covariance=observed_covariance)
        except (KeyError, TypeError, ValueError, DataQualityError) as exc:
            raise InvariantViolation("FACTOR_RISK_ESTIMATION_LINEAGE_UNVERIFIED") from exc

    def _context_from_lineage(self, lineage: Mapping[str, object]) -> AsOfContext:
        if not isinstance(lineage, Mapping):
            raise InvariantViolation("FACTOR_RISK_ESTIMATION_LINEAGE_UNVERIFIED")
        try:
            return AsOfContext(str(lineage["runtime_run_id"]),
                _stored_utc(lineage["as_of"], "FACTOR_RISK_ESTIMATION_LINEAGE_UNVERIFIED"),
                _stored_utc(lineage["information_cutoff"], "FACTOR_RISK_ESTIMATION_LINEAGE_UNVERIFIED"),
                "factor-risk-lineage", "factor-risk-lineage", str(lineage["code_revision"]))
        except (KeyError, TypeError, ValueError) as exc:
            raise InvariantViolation("FACTOR_RISK_ESTIMATION_LINEAGE_UNVERIFIED") from exc

    def _replay_panel(self, lineage: Mapping[str, object], context: AsOfContext) -> ReturnPanel:
        panel_raw = lineage.get("return_panel")
        observations = lineage.get("observations")
        if not isinstance(panel_raw, Mapping) or not isinstance(observations, list):
            raise InvariantViolation("FACTOR_RISK_ESTIMATION_LINEAGE_UNVERIFIED")
        for item in observations:
            if not isinstance(item, Mapping):
                raise InvariantViolation("FACTOR_RISK_ESTIMATION_LINEAGE_UNVERIFIED")
            row = self._conn.execute(
                f"SELECT {_OBSERVATION_COLUMNS} FROM am_temporal_observation WHERE observation_id=?",
                (item.get("observation_id"),),
            ).fetchone()
            try:
                observation = observation_from_row(row) if row is not None else None
            except (InvariantViolation, ValueError, TypeError, json.JSONDecodeError) as exc:
                raise InvariantViolation("FACTOR_RISK_ESTIMATION_LINEAGE_UNVERIFIED") from exc
            manifest = self._conn.execute(
                """SELECT manifest.content_hash FROM am_dataset_manifest manifest
                   JOIN am_ingestion_run ingestion USING(ingestion_run_id)
                   WHERE manifest.dataset_manifest_id=? AND ingestion.runtime_run_id=?""",
                (observation.dataset_manifest_id, context.run_id)).fetchone() if observation is not None else None
            try:
                session = SessionRepository(self._conn).session(
                    str(lineage["calendar_id"]), observation.reference_period, context) if observation is not None else None
            except (KeyError, DataQualityError) as exc:
                raise InvariantViolation("FACTOR_RISK_ESTIMATION_LINEAGE_UNVERIFIED") from exc
            if (observation is None or observation.observation_id != item.get("observation_id") or
                    observation.content_hash != item.get("content_hash") or manifest is None or
                    manifest[0] != item.get("manifest_hash") or session is None or
                    session.get("session_status") != "OPEN"):
                raise InvariantViolation("FACTOR_RISK_ESTIMATION_LINEAGE_UNVERIFIED")
        try:
            return ReturnPanel(tuple(panel_raw["instruments"]), tuple(date.fromisoformat(item) for item in panel_raw["dates"]),
                _rows(panel_raw["returns"], "FACTOR_RISK_ESTIMATION_LINEAGE_UNVERIFIED"), bool(panel_raw["total_return"]),
                CurrencyBasis(panel_raw["currency_basis"]), MissingPolicy(panel_raw["missing_policy"]),
                _stored_utc(panel_raw["information_cutoff"], "FACTOR_RISK_ESTIMATION_LINEAGE_UNVERIFIED"),
                tuple(panel_raw["dataset_manifest_ids"]),
                tuple(_stored_utc(item, "FACTOR_RISK_ESTIMATION_LINEAGE_UNVERIFIED") for item in panel_raw["available_at"]),
                int(panel_raw["dropped_rows"]), str(panel_raw["outlier_policy"]),
                int(panel_raw["prelisting_rows_excluded"]), int(panel_raw["periods_per_year"]))
        except (KeyError, TypeError, ValueError) as exc:
            raise InvariantViolation("FACTOR_RISK_ESTIMATION_LINEAGE_UNVERIFIED") from exc

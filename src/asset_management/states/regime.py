"""Typed regime-inference representation detached from generic State construction."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from enum import StrEnum
import re
from typing import Mapping

from asset_management.data.immutable import ImmutableDatasetStore, canonical, digest
from asset_management.domain.errors import InvariantViolation

from .models import StateType


REGIME_SNAPSHOT_SCHEMA_VERSION = "regime-snapshot-v1"
_HASH = re.compile(r"[0-9a-f]{64}")
_CODE_REVISION = re.compile(r"git:[0-9a-f]{7,40}")
_IDENTIFIER = re.compile(r"[A-Za-z][A-Za-z0-9_.:-]*")


class RegimeOutputSemantics(StrEnum):
    """Whether a regime output was knowable at the decision cutoff."""

    FILTERED_CAUSAL = "FILTERED_CAUSAL"
    SMOOTHED_RETROSPECTIVE = "SMOOTHED_RETROSPECTIVE"


@dataclass(frozen=True, slots=True)
class RegimeModelSpec:
    """Versioned inference contract; this does not implement or select a classifier."""

    model_id: str
    version: str
    input_state_type: StateType
    input_component_ids: tuple[str, ...]
    latent_state_ids: tuple[str, ...]
    parameter_set_id: str
    purpose: str

    def __post_init__(self) -> None:
        text = (self.model_id, self.version, self.parameter_set_id, self.purpose)
        if (any(not isinstance(value, str) or not value.strip() for value in text) or
                not isinstance(self.input_state_type, StateType) or
                not self.input_component_ids or
                len(self.input_component_ids) != len(set(self.input_component_ids)) or
                any(not _IDENTIFIER.fullmatch(value) for value in self.input_component_ids) or
                len(self.latent_state_ids) < 2 or
                len(self.latent_state_ids) != len(set(self.latent_state_ids)) or
                any(not _IDENTIFIER.fullmatch(value) for value in self.latent_state_ids)):
            raise InvariantViolation("REGIME_MODEL_SPEC_INVALID")
        object.__setattr__(self, "input_component_ids", tuple(sorted(self.input_component_ids)))
        object.__setattr__(self, "latent_state_ids", tuple(sorted(self.latent_state_ids)))

    @property
    def model_key(self) -> str:
        return f"{self.model_id}@{self.version}"

    @property
    def spec_id(self) -> str:
        return digest(canonical(self.payload()))

    def payload(self) -> dict[str, object]:
        return {
            "model_id": self.model_id,
            "version": self.version,
            "input_state_type": self.input_state_type.value,
            "input_component_ids": list(self.input_component_ids),
            "latent_state_ids": list(self.latent_state_ids),
            "parameter_set_id": self.parameter_set_id,
            "purpose": self.purpose,
        }


@dataclass(frozen=True, slots=True)
class RegimeProbability:
    state_id: str
    probability: Decimal

    def __post_init__(self) -> None:
        if (not isinstance(self.state_id, str) or not _IDENTIFIER.fullmatch(self.state_id) or
                not isinstance(self.probability, Decimal) or
                not self.probability.is_finite() or
                not Decimal(0) <= self.probability <= Decimal(1)):
            raise InvariantViolation("REGIME_PROBABILITY_INVALID")

    def payload(self) -> dict[str, str]:
        return {"state_id": self.state_id, "probability": str(self.probability)}


@dataclass(frozen=True, slots=True)
class RegimeSnapshot:
    """Immutable inference output. It carries no Forecast/Risk/Portfolio/Order authority."""

    regime_id: str
    source_state_id: str
    model_spec_id: str
    model_key: str
    as_of: str
    information_cutoff: str
    output_semantics: RegimeOutputSemantics
    state_probabilities: tuple[RegimeProbability, ...]
    entropy: Decimal
    confidence: Decimal
    input_evidence_ids: tuple[str, ...]
    code_revision: str
    schema_version: str = REGIME_SNAPSHOT_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if (not _HASH.fullmatch(self.regime_id) or
                not _HASH.fullmatch(self.source_state_id) or
                not _HASH.fullmatch(self.model_spec_id) or
                not isinstance(self.model_key, str) or not self.model_key.strip() or
                not isinstance(self.output_semantics, RegimeOutputSemantics) or
                not isinstance(self.entropy, Decimal) or not self.entropy.is_finite() or
                self.entropy < 0 or
                not isinstance(self.confidence, Decimal) or not self.confidence.is_finite() or
                not Decimal(0) <= self.confidence <= Decimal(1) or
                not _CODE_REVISION.fullmatch(self.code_revision) or
                self.schema_version != REGIME_SNAPSHOT_SCHEMA_VERSION):
            raise InvariantViolation("REGIME_SNAPSHOT_INVALID")
        as_of = _aware(self.as_of, "REGIME_TIME_INVALID")
        cutoff = _aware(self.information_cutoff, "REGIME_TIME_INVALID")
        if cutoff > as_of:
            raise InvariantViolation("REGIME_CUTOFF_AFTER_AS_OF")
        ordered = tuple(sorted(self.state_probabilities, key=lambda item: item.state_id))
        if (len(ordered) < 2 or
                len({item.state_id for item in ordered}) != len(ordered) or
                sum((item.probability for item in ordered), Decimal(0)) != Decimal(1)):
            raise InvariantViolation("REGIME_PROBABILITY_SIMPLEX_INVALID")
        evidence = _hashes(self.input_evidence_ids, "REGIME_EVIDENCE_INVALID")
        object.__setattr__(self, "as_of", as_of.isoformat())
        object.__setattr__(self, "information_cutoff", cutoff.isoformat())
        object.__setattr__(self, "state_probabilities", ordered)
        object.__setattr__(self, "input_evidence_ids", evidence)
        expected = regime_identity(
            source_state_id=self.source_state_id,
            model_spec_id=self.model_spec_id,
            model_key=self.model_key,
            as_of=as_of,
            information_cutoff=cutoff,
            output_semantics=self.output_semantics,
            state_probabilities=ordered,
            entropy=self.entropy,
            confidence=self.confidence,
            input_evidence_ids=evidence,
            code_revision=self.code_revision,
        )
        if self.regime_id != expected:
            raise InvariantViolation("REGIME_IDENTITY_MISMATCH")

    @classmethod
    def create(
        cls,
        *,
        source_state_id: str,
        spec: RegimeModelSpec,
        as_of: datetime,
        information_cutoff: datetime,
        output_semantics: RegimeOutputSemantics,
        state_probabilities: Mapping[str, Decimal],
        entropy: Decimal,
        confidence: Decimal,
        input_evidence_ids: tuple[str, ...],
        code_revision: str,
    ) -> "RegimeSnapshot":
        if not isinstance(spec, RegimeModelSpec):
            raise InvariantViolation("REGIME_MODEL_SPEC_INVALID")
        as_of_utc = _aware(as_of, "REGIME_TIME_INVALID")
        cutoff_utc = _aware(information_cutoff, "REGIME_TIME_INVALID")
        if cutoff_utc > as_of_utc:
            raise InvariantViolation("REGIME_CUTOFF_AFTER_AS_OF")
        if set(state_probabilities) != set(spec.latent_state_ids):
            raise InvariantViolation("REGIME_STATES_DO_NOT_MATCH_SPEC")
        probabilities = tuple(
            RegimeProbability(state_id, probability)
            for state_id, probability in state_probabilities.items()
        )
        evidence = _hashes(input_evidence_ids, "REGIME_EVIDENCE_INVALID")
        identifier = regime_identity(
            source_state_id=source_state_id,
            model_spec_id=spec.spec_id,
            model_key=spec.model_key,
            as_of=as_of_utc,
            information_cutoff=cutoff_utc,
            output_semantics=output_semantics,
            state_probabilities=probabilities,
            entropy=entropy,
            confidence=confidence,
            input_evidence_ids=evidence,
            code_revision=code_revision,
        )
        return cls(
            identifier,
            source_state_id,
            spec.spec_id,
            spec.model_key,
            as_of_utc.isoformat(),
            cutoff_utc.isoformat(),
            output_semantics,
            probabilities,
            entropy,
            confidence,
            evidence,
            code_revision,
        )

    @property
    def is_causal(self) -> bool:
        return self.output_semantics is RegimeOutputSemantics.FILTERED_CAUSAL

    def payload(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "regime_id": self.regime_id,
            "source_state_id": self.source_state_id,
            "model_spec_id": self.model_spec_id,
            "model_key": self.model_key,
            "as_of": self.as_of,
            "information_cutoff": self.information_cutoff,
            "output_semantics": self.output_semantics.value,
            "state_probabilities": [item.payload() for item in self.state_probabilities],
            "entropy": str(self.entropy),
            "confidence": str(self.confidence),
            "input_evidence_ids": list(self.input_evidence_ids),
            "code_revision": self.code_revision,
        }


def _aware(value: str | datetime, reason: str) -> datetime:
    try:
        parsed = value if isinstance(value, datetime) else datetime.fromisoformat(value)
    except (TypeError, ValueError) as exc:
        raise InvariantViolation(reason) from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise InvariantViolation(reason)
    return parsed.astimezone(timezone.utc)


def _hashes(values: tuple[str, ...], reason: str) -> tuple[str, ...]:
    if (not values or len(values) != len(set(values)) or
            any(not isinstance(value, str) or not _HASH.fullmatch(value) for value in values)):
        raise InvariantViolation(reason)
    return tuple(sorted(values))


def regime_identity(
    *,
    source_state_id: str,
    model_spec_id: str,
    model_key: str,
    as_of: datetime,
    information_cutoff: datetime,
    output_semantics: RegimeOutputSemantics,
    state_probabilities: tuple[RegimeProbability, ...],
    entropy: Decimal,
    confidence: Decimal,
    input_evidence_ids: tuple[str, ...],
    code_revision: str,
) -> str:
    if as_of.tzinfo is None or as_of.utcoffset() is None or information_cutoff.tzinfo is None or information_cutoff.utcoffset() is None:
        raise InvariantViolation("REGIME_TIME_INVALID")
    body = {
        "schema_version": REGIME_SNAPSHOT_SCHEMA_VERSION,
        "source_state_id": source_state_id,
        "model_spec_id": model_spec_id,
        "model_key": model_key,
        "as_of": as_of.astimezone(timezone.utc).isoformat(),
        "information_cutoff": information_cutoff.astimezone(timezone.utc).isoformat(),
        "output_semantics": output_semantics.value,
        "state_probabilities": [
            item.payload() for item in sorted(state_probabilities, key=lambda item: item.state_id)
        ],
        "entropy": str(entropy),
        "confidence": str(confidence),
        "input_evidence_ids": list(sorted(input_evidence_ids)),
        "code_revision": code_revision,
    }
    return digest(canonical(body))


class RegimeRepository:
    """Immutable regime snapshots, separate from State snapshots and trading authority."""

    def __init__(self, store: ImmutableDatasetStore) -> None:
        self.store = store

    def publish(self, snapshot: RegimeSnapshot) -> str:
        if not isinstance(snapshot, RegimeSnapshot):
            raise InvariantViolation("REGIME_SNAPSHOT_INVALID")
        content = canonical(snapshot.payload())
        path = self.store.layout.resolve(
            "catalog", f"regime-snapshots/{snapshot.regime_id}.json"
        )
        self.store._publish(path, content)
        return snapshot.regime_id

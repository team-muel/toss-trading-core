"""Expression research specifications and reproducible mechanism-only receipts.

No provider, forecast, portfolio or execution owner is introduced here. Input
contract keys are declarations, not proof of their economic validity. Dataset
and reference truth remain upstream; BacktestRunSpec owns OOS preregistration.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, field, replace
from datetime import datetime
from enum import StrEnum
from hashlib import sha256
import json
from math import isfinite
import re
from types import MappingProxyType

from .datafields import PointInTimeDataSource
from .dsl import (
    CallNode, CompiledExpression, DataFieldNode, GroupFieldNode,
    RepositoryPanelResolver, _reference_period_key, compile_expression,
)
from .expression import AlphaSimulationSettings
from .history import HistoricalSession, HistorySimulationResult, simulate_history, _last_cross_section
from .input_journal import InputJournal, iter_snapshots
from asset_management.domain.errors import DataQualityError


class ResearchTheme(StrEnum):
    QUANT = "quant"
    MACRO = "macro"
    FUNDAMENTAL = "fundamental"


def _text(value: str, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be nonempty text")
    return value.strip()


def _json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False, allow_nan=False)


def _hash(value: object) -> str:
    return sha256(_json(value).encode("utf-8")).hexdigest()


def _settings(settings: AlphaSimulationSettings) -> dict:
    if type(settings) is not AlphaSimulationSettings:
        raise ValueError("canonical AlphaSimulationSettings required")
    for name in ("delay", "decay"):
        value = getattr(settings, name)
        if type(value) is not int or value < 0:
            raise ValueError(f"{name} must be a nonnegative integer")
    for name in ("book_size", "truncation"):
        value = getattr(settings, name)
        if type(value) not in (int, float) or not isfinite(value) or value <= 0:
            raise ValueError(f"{name} must be positive and finite")
    if settings.truncation > 1 or type(settings.long_only) is not bool:
        raise ValueError("invalid truncation or long_only")
    settings.__post_init__()
    result = asdict(settings)
    result.update(book_size=float(settings.book_size), truncation=float(settings.truncation))
    return result


def _used_fields(root) -> tuple[set[str], set[str]]:
    if isinstance(root, DataFieldNode):
        return {root.name}, set()
    if isinstance(root, GroupFieldNode):
        return set(), {root.name}
    fields, groups = set(), set()
    if isinstance(root, CallNode):
        for argument in root.arguments:
            child_fields, child_groups = _used_fields(argument)
            fields.update(child_fields)
            groups.update(child_groups)
    return fields, groups


@dataclass(frozen=True, slots=True)
class ResearchSpec:
    """A hypothesis bound to one expression; not an OOS/strategy approval.

    This first runner supports per-instrument expression scores only. Theme
    identifiers do not imply a macro-state or fundamental evidence builder is
    implemented, and a market-wide macro state must not be ranked over assets.
    """

    theme: ResearchTheme
    family: str
    version: str
    thesis: str
    falsification_criteria: tuple[str, ...]
    expression: str
    field_contracts: Mapping[str, str]
    settings: AlphaSimulationSettings
    evaluation_horizon_sessions: int
    policy_version: str
    dataset_source: str
    dataset_name: str
    dataset_schema_version: str
    group_fields: tuple[str, ...] = ()
    neutralization_group_field: str | None = None
    compiled: CompiledExpression = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        if not isinstance(self.theme, ResearchTheme):
            raise ValueError("unknown research theme")
        for name in ("family", "version", "thesis", "expression", "policy_version",
                     "dataset_source", "dataset_name", "dataset_schema_version"):
            object.__setattr__(self, name, _text(getattr(self, name), name))
        horizon = self.evaluation_horizon_sessions
        if type(horizon) is not int or horizon <= 0:
            raise ValueError("evaluation horizon must be a positive integer")
        if isinstance(self.falsification_criteria, str):
            raise ValueError("falsification criteria must be a sequence")
        criteria = tuple(_text(value, "falsification") for value in self.falsification_criteria)
        if not criteria or len(set(criteria)) != len(criteria):
            raise ValueError("falsification criteria must be nonempty and unique")
        if not isinstance(self.field_contracts, Mapping) or not self.field_contracts:
            raise ValueError("field contracts required")
        contracts = {}
        for name, contract in self.field_contracts.items():
            if not isinstance(name, str) or re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name) is None:
                raise ValueError("invalid field name")
            contracts[name] = _text(contract, "field contract")
        if isinstance(self.group_fields, str):
            raise ValueError("group fields must be a sequence")
        groups = tuple(sorted(_text(value, "group field") for value in self.group_fields))
        if len(set(groups)) != len(groups) or set(groups) & set(contracts):
            raise ValueError("group and data fields must be unique and disjoint")
        compiled = compile_expression(self.expression, data_fields=set(contracts), group_fields=set(groups))
        if _used_fields(compiled.root) != (set(contracts), set(groups)):
            raise ValueError("declared fields must exactly match the expression")
        _settings(self.settings)
        if self.neutralization_group_field is not None:
            object.__setattr__(self, "neutralization_group_field", _text(self.neutralization_group_field, "neutralization group field"))
        if self.settings.neutralization == "group" and self.neutralization_group_field is None:
            raise ValueError("group neutralization requires its canonical group field")
        object.__setattr__(self, "field_contracts", MappingProxyType(dict(sorted(contracts.items()))))
        object.__setattr__(self, "falsification_criteria", criteria)
        object.__setattr__(self, "group_fields", groups)
        object.__setattr__(self, "expression", compiled.canonical)
        object.__setattr__(self, "compiled", compiled)

    def payload(self) -> dict:
        return {
            "schema_version": "expression-research-spec-v1",
            "theme": self.theme.value, "family": self.family, "version": self.version,
            "thesis": self.thesis, "falsification_criteria": list(self.falsification_criteria),
            "expression": self.compiled.canonical, "expression_hash": self.compiled.expression_hash,
            "field_contracts": dict(self.field_contracts), "group_fields": list(self.group_fields),
            "neutralization_group_field": self.neutralization_group_field,
            "settings": _settings(self.settings),
            "evaluation_horizon_sessions": self.evaluation_horizon_sessions,
            "policy_version": self.policy_version,
            "dataset_source": self.dataset_source, "dataset_name": self.dataset_name,
            "dataset_schema_version": self.dataset_schema_version,
            "output_semantic_type": "SIGNAL_VALUE", "return_basis": "NOT_A_RETURN",
        }

    @property
    def spec_hash(self) -> str:
        return _hash(self.payload())


@dataclass(frozen=True, slots=True)
class _SnapshotResolver:
    fields: Mapping
    groups: Mapping
    membership: tuple[frozenset[str], ...]

    def field(self, name):
        return self.fields[name]

    def group(self, name):
        return self.groups[name]

    def members_at(self, index):
        return self.membership[index]


def _snapshot(spec: ResearchSpec, session: HistoricalSession, prefix: Sequence[HistoricalSession], group_cache=None):
    resolver = session.resolver
    if not isinstance(resolver, RepositoryPanelResolver):
        raise ValueError("research runs require a canonical repository panel resolver")
    if not isinstance(resolver.fields.source, PointInTimeDataSource):
        raise ValueError("research runs require a point-in-time repository data source")
    if resolver.context != session.context:
        raise ValueError("resolver/session context mismatch")
    if (session.context.parameter_set_id != spec.spec_hash or
            session.context.policy_version != spec.policy_version):
        raise ValueError("research spec is not bound to the session context")
    periods = tuple(resolver.reference_periods)
    if _reference_period_key(periods[-1]) > session.effective_time_utc.replace(tzinfo=None):
        raise ValueError("reference period is after effective time")
    members = tuple(resolver.members_at(index) for index in range(len(periods)))
    if set(session.instrument_ids) != members[-1]:
        raise ValueError("session universe differs from effective panel membership")
    if set(resolver.fields.source.universes.members(spec.settings.universe, session.context)) != set(session.instrument_ids):
        raise ValueError("session universe differs from canonical reference truth")
    if spec.settings.neutralization == "group" and (
            set(session.neutralization_groups) != set(session.instrument_ids) or
            any(not isinstance(value, str) or not value.strip()
                for value in session.neutralization_groups.values())):
        raise ValueError("complete neutralization groups required")
    # Every input column must denote a session from this very run, including
    # warm-up. Otherwise an omitted day silently changes the meaning of lag N,
    # or a fabricated past universe can survive a valid current-universe check.
    if len(periods) != len(prefix):
        raise ValueError("include the complete warm-up/session timeline")
    for index, (period, historical) in enumerate(zip(periods, prefix)):
        instant = historical.effective_time_utc.replace(tzinfo=None)
        expected = instant.replace(hour=0, minute=0, second=0, microsecond=0) if re.fullmatch(r"\d{4}-\d{2}-\d{2}", period) else instant
        if _reference_period_key(period) != expected:
            raise ValueError("panel periods must align with the complete session timeline")
        known_members = frozenset(resolver.fields.source.universes.members(
            spec.settings.universe, historical.context))
        if members[index] != known_members:
            raise ValueError("historical membership differs from canonical reference truth")
    ids = tuple(session.dataset_manifest_ids)
    if not ids or ids != resolver.dataset_manifest_ids:
        raise ValueError("missing or mismatched repository manifest lineage")
    for identifier in ids:
        manifest, _ = resolver.fields.source.datasets.read(identifier)
        session.context.require_known_at(
            datetime.fromisoformat(manifest.available_at), label="research manifest")
        if (manifest.source, manifest.dataset, manifest.schema_version) != (
                spec.dataset_source, spec.dataset_name, spec.dataset_schema_version):
            raise ValueError("research dataset contract mismatch")
        if (manifest.source, manifest.dataset, manifest.layer, manifest.quality_status) != (
                resolver.fields.source.source, resolver.fields.source.dataset, "silver", "VALID"):
            raise ValueError("invalid research manifest")
    fields, observation_evidence = {}, {}
    for name in spec.field_contracts:
        panel, records = resolver.field_with_evidence(name)
        fields[name] = MappingProxyType({key: tuple(values) for key, values in panel.items()})
        observation_evidence[name] = {instrument: _hash([
            {"observation_id": record.observation_id, "content_hash": record.content_hash,
             "schema_version": record.schema_version, "manifest_id": record.dataset_manifest_id}
            for record in rows]) for instrument, rows in records.items()}
    groups = {
        name: MappingProxyType({key: tuple(values) for key, values in resolver.group(name).items()})
        for name in spec.group_fields
    }
    # Expression grouping has its own historical panel. Validating only the
    # simulation's current neutralization_groups would leave missing expression
    # classifications to the operators' permissive __ungrouped__ fallback.
    for name, panel in groups.items():
        for index, period_members in enumerate(members):
            for instrument_id in period_members:
                series = panel.get(instrument_id, ())
                if len(series) != len(periods) or not isinstance(series[index], str) or not series[index].strip():
                    raise ValueError(
                        f"incomplete expression group {name} for {instrument_id} at {periods[index]}"
                    )
    # Caller-supplied dictionaries are not source evidence. Validate against
    # each historical session's own pinned, schema-checked observations, not the
    # current source's latest classifications. A missing daily classification
    # is not silently forward-filled or accepted from the caller.
    group_cache = {} if group_cache is None else group_cache
    group_evidence = {}
    required_groups = set(spec.group_fields)
    if spec.neutralization_group_field:
        required_groups.add(spec.neutralization_group_field)
    for name in sorted(required_groups):
        lineage = []
        for index, historical in enumerate(prefix):
            key = (name, historical.context, historical.dataset_manifest_ids)
            if key not in group_cache:
                source = historical.resolver.fields.source
                period_key = _reference_period_key(periods[index])
                records = {}
                for instrument in members[index]:
                    matching = [record for record in source.observation_evidence(name,
                        instrument_id=instrument, context=historical.context,
                        dataset_manifest_id=historical.dataset_manifest_ids[0])
                        if _reference_period_key(record.reference_period) == period_key]
                    if len(matching) != 1 or not isinstance(matching[0].value, str) or not matching[0].value.strip():
                        raise DataQualityError("MISSING_CANONICAL_GROUP_CLASSIFICATION")
                    record = matching[0]
                    records[instrument] = {"value": record.value, "observation_id": record.observation_id,
                        "content_hash": record.content_hash, "manifest_id": record.dataset_manifest_id,
                        "schema_version": record.schema_version, "available_at": record.available_at.isoformat()}
                group_cache[key] = records
            records = group_cache[key]
            for instrument, record in records.items():
                if name in groups and groups[name][instrument][index] != record["value"]:
                    raise DataQualityError("GROUP_CLASSIFICATION_PROVENANCE_MISMATCH")
                if name == spec.neutralization_group_field and index == len(prefix) - 1:
                    if session.neutralization_groups.get(instrument) != record["value"]:
                        raise DataQualityError("GROUP_NEUTRALIZATION_PROVENANCE_MISMATCH")
            lineage.append(records)
        group_evidence[name] = lineage
    snapshot = _SnapshotResolver(MappingProxyType(fields), MappingProxyType(groups), members)
    payload = {
        "context": {
            "run_id": session.context.run_id,
            "as_of_utc": session.context.as_of_utc.isoformat(),
            "information_cutoff_utc": session.context.information_cutoff_utc.isoformat(),
            "policy_version": session.context.policy_version,
            "parameter_set_id": session.context.parameter_set_id,
            "code_revision": session.context.code_revision,
        },
        "instrument_ids": list(session.instrument_ids),
        "effective_time_utc": session.effective_time_utc.isoformat(),
        # A canonical resolver may include known instruments outside the
        # historical membership union. Their masked panels are still hashed;
        # preserve the full read axis so replay can recover those exact hashes.
        "resolver_instrument_ids": list(resolver.instrument_ids),
        "dataset_manifest_ids": list(ids), "universe_version": session.universe_version,
        "reference_periods": list(periods), "membership": [sorted(value) for value in members],
        "observation_evidence_hashes": observation_evidence,
        "group_evidence": group_evidence,
        "fields": {name: {key: list(values) for key, values in panel.items()}
                   for name, panel in fields.items()},
        "groups": {name: {key: list(values) for key, values in panel.items()}
                   for name, panel in groups.items()},
        "neutralization_groups": dict(session.neutralization_groups),
    }
    # Store the consumed values, not just a pointer to a latest-vintage query.
    # Canonical append-only observation APIs can admit subsequently imported
    # vintages with historical availability. The manifest remains immutable but
    # a replay query may then choose another value under that same manifest.
    # This mechanism receipt freezes the input; its hash is not an approval.
    return replace(session, resolver=snapshot), _hash(payload), payload



def _history_payload(result: HistorySimulationResult) -> dict:
    return {
        "expression": result.expression, "expression_hash": result.expression_hash,
        "settings": _settings(result.settings),
        "points": [{
            "effective_time_utc": point.effective_time_utc.isoformat(),
            "signal_time_utc": None if point.signal_time_utc is None else point.signal_time_utc.isoformat(),
            "information_cutoff_utc": None if point.information_cutoff_utc is None else point.information_cutoff_utc.isoformat(),
            "raw": dict(point.raw), "base_weights": dict(point.base_weights), "weights": dict(point.weights),
            "dataset_manifest_ids": list(point.dataset_manifest_ids),
            "universe_version": point.universe_version,
            "signal_universe_version": point.signal_universe_version,
            "source_run_id": point.source_run_id, "code_revision": point.code_revision,
            "neutralization_groups": dict(point.neutralization_groups),
        } for point in result.points],
    }


@dataclass(frozen=True, slots=True)
class ResearchRun:
    """A content-addressed receipt, never a signed approval or performance claim."""

    result: HistorySimulationResult
    evidence_json: str

    @property
    def evidence_hash(self) -> str:
        return sha256(self.evidence_json.encode("utf-8")).hexdigest()

    def payload(self) -> dict:
        return json.loads(self.evidence_json)

    def iter_session_inputs(self):
        payload = self.payload()
        if payload.get("schema_version") != "expression-research-run-v2":
            raise ValueError("unsupported research receipt version")
        deltas = payload["session_input_deltas"]
        hashes = payload["session_input_hashes"]
        if len(deltas) != len(hashes):
            raise ValueError("research input count mismatch")
        for snapshot, expected in zip(iter_snapshots(deltas), hashes):
            if _hash(snapshot) != expected:
                raise ValueError("research input snapshot hash mismatch")
            yield snapshot


def run_expression_research(spec: ResearchSpec, sessions: Sequence[HistoricalSession]) -> ResearchRun:
    """Snapshot canonical repository reads, then call the existing simulator.

    No forward returns are accepted: outcome maturity, OOS splits and economic
    performance evidence belong to the existing validation/calibration owner.
    A caller must establish datafield economics and historical calendar/reference
    truth upstream; hashing a declaration does not verify that it is true.
    """
    if not isinstance(spec, ResearchSpec):
        raise ValueError("ResearchSpec required")
    sessions = tuple(sessions)
    if not sessions or not all(isinstance(item, HistoricalSession) for item in sessions):
        raise ValueError("nonempty historical sessions required")
    times = tuple(item.effective_time_utc for item in sessions)
    if any(left >= right for left, right in zip(times, times[1:])):
        raise ValueError("sessions must be strictly increasing")
    cutoffs = tuple(item.context.information_cutoff_utc for item in sessions)
    if any(left > right for left, right in zip(cutoffs, cutoffs[1:])):
        raise ValueError("information cutoffs must not move backwards")
    if len({item.context.code_revision for item in sessions}) != 1:
        raise ValueError("one research run must use one code revision")
    journal, hashes, scores, group_cache = InputJournal(), [], [], {}
    for index, session in enumerate(sessions):
        snapshot, input_hash, coordinates = _snapshot(spec, session, sessions[:index + 1], group_cache)
        scores.append(_last_cross_section(spec.compiled, snapshot, snapshot.instrument_ids))
        hashes.append(input_hash)
        journal.append(coordinates)
        # Do not retain the full numeric panel of every expanding prefix.
        del snapshot, coordinates
    result = simulate_history(spec.compiled, sessions, spec.settings, evaluated_scores=scores)
    history = _history_payload(result)
    evidence = {
        "schema_version": "expression-research-run-v2",
        "validation_scope": "MECHANISM_ONLY", "spec": spec.payload(), "spec_hash": spec.spec_hash,
        "result_status": "COMPUTED" if any(value is not None for point in result.points for value in point.raw.values()) else "NO_OBSERVATIONS",
        "session_input_hashes": list(hashes), "session_input_deltas": journal.finish(),
        "result": history, "result_hash": _hash(history),
    }
    return ResearchRun(result, _json(evidence))

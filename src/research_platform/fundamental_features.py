"""Compile sourced fundamental proposals with the existing reconciled model.

This is an evidence adapter, not another valuation engine or approval authority.
It never exports the dossier's recommendation, sizing or implied stock return.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date, datetime, timezone
from decimal import Decimal
from hashlib import sha256
import json
import re
from uuid import UUID

from asset_management.data.immutable import ImmutableDatasetStore
from asset_management.features.company import fcf_margin, growth
from asset_management.features.models import FeatureInput
from asset_management.time.asof import AsOfContext, require_as_of_context
from .variant_perception import build_focused_research_dossier, validate_focused_research_dossier


def _json(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def _hash(value):
    return sha256(_json(value).encode()).hexdigest()


def _instant(value):
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("evidence timestamp must have a timezone")
    return parsed.astimezone(timezone.utc)


@dataclass(frozen=True, slots=True)
class FundamentalSpec:
    instrument_id: str
    symbol: str
    proposal_manifest_id: str
    policy_hash: str
    policy_version: str
    minimum_positive_eps: str = "0.01"

    def __post_init__(self):
        UUID(self.instrument_id)
        if not re.fullmatch(r"[A-Z][A-Z0-9.-]{0,14}", self.symbol):
            raise ValueError("invalid fundamental symbol")
        if not re.fullmatch(r"[0-9a-f]{64}", self.policy_hash):
            raise ValueError("invalid fundamental policy hash")
        if not self.proposal_manifest_id or not self.policy_version.strip():
            raise ValueError("proposal and policy identity required")
        floor = Decimal(self.minimum_positive_eps)
        if not floor.is_finite() or floor <= 0:
            raise ValueError("positive EPS floor required")

    @property
    def spec_hash(self):
        return _hash({"schema": "fundamental-feature-spec-v1", **asdict(self)})


@dataclass(frozen=True, slots=True)
class FundamentalFeatureRun:
    evidence_json: str

    @property
    def evidence_hash(self):
        return sha256(self.evidence_json.encode()).hexdigest()

    def payload(self):
        return json.loads(self.evidence_json)

    def feature_inputs(self):
        """Unapproved research inputs; FeatureStore must still validate them."""
        payload = self.payload()
        timestamp = _instant(payload["computed_at"])
        return tuple(FeatureInput({"value": Decimal(value), "feature_name": name,
                                  "instrument_id": payload["instrument_id"],
                                  "source_manifest_id": payload["proposal_manifest_id"],
                                  "research_evidence_hash": self.evidence_hash}, timestamp, timestamp)
                     for name, value in payload["numeric_features"].items())


def _read(store, manifest_id, context, *, source=None, dataset, schema):
    manifest, body = store.read(manifest_id)
    if (manifest.layer, manifest.quality_status, manifest.dataset, manifest.schema_version) != (
            "bronze", "VALID", dataset, schema):
        raise ValueError("fundamental evidence manifest contract mismatch")
    if source is not None and manifest.source != source:
        raise ValueError("fundamental evidence source mismatch")
    context.require_known_at(_instant(manifest.available_at), label="fundamental evidence")
    retrieved, published = _instant(manifest.retrieved_at), _instant(manifest.provider_timestamp)
    if published > retrieved or retrieved > _instant(manifest.available_at):
        raise ValueError("fundamental evidence timestamp order invalid")
    return manifest, body


def compile_fundamental_features(spec: FundamentalSpec, store: ImmutableDatasetStore,
                                 context: AsOfContext, policy: dict) -> FundamentalFeatureRun:
    """Recalculate from pinned evidence; hashes do not authenticate interpretation."""
    require_as_of_context(context)
    if context.parameter_set_id != spec.spec_hash or context.policy_version != spec.policy_version:
        raise ValueError("fundamental spec/context mismatch")
    if _hash(policy) != spec.policy_hash:
        raise ValueError("fundamental policy hash mismatch")
    manifest, proposal = _read(store, spec.proposal_manifest_id, context,
        source="research-proposal", dataset="focused-research-proposal", schema="fundamental-proposal-v1")
    if proposal.get("instrument_id") != spec.instrument_id:
        raise ValueError("fundamental instrument identity mismatch")
    analysis = proposal["analysis"]
    if analysis.get("symbol") != spec.symbol or analysis.get("as_of_date") != context.as_of_utc.date().isoformat():
        raise ValueError("fundamental symbol/as-of mismatch")
    periods = proposal["period_contract"]
    if periods.get("currency") != "USD" or len({periods.get(name) for name in (
            "forecast_eps_basis", "consensus_eps_basis", "implied_eps_basis")}) != 1:
        raise ValueError("fundamental currency or EPS basis mismatch")
    if periods["forecast_eps_basis"] not in {"GAAP", "ADJUSTED"}:
        raise ValueError("explicit EPS basis required")
    prior, current, forecast = (date.fromisoformat(periods[name]) for name in (
        "prior_end", "current_end", "forecast_end"))
    if not prior < current <= context.as_of_utc.date() < forecast:
        raise ValueError("fundamental fiscal periods are not available/forward aligned")
    quality = analysis["earnings_quality"]
    if (quality["periods"]["prior_period"], quality["periods"]["current_period"], analysis["earnings_model"]["forecast_period"]) != (
            periods["prior_label"], periods["current_label"], periods["forecast_label"]):
        raise ValueError("fundamental fiscal label mismatch")
    for scenario in analysis["earnings_model"]["scenarios"]:
        if scenario["capital_efficiency"]["prior_period"] != periods["current_label"]:
            raise ValueError("forecast capital bridge is not bound to reported current period")
    # Unique source IDs and manifests prevent substituting one repeated artifact
    # for nominally independent organizations. The original validator owns the
    # remaining cross-source/model reconciliation requirements.
    source_ids = [item["source_id"] for item in analysis["sources"]]
    mapping = proposal["source_manifest_ids"]
    if len(source_ids) != len(set(source_ids)) or set(source_ids) != set(mapping):
        raise ValueError("fundamental source coverage mismatch")
    if len(set(mapping.values())) != len(mapping):
        raise ValueError("source artifacts must not be duplicated")
    sources = []
    for source in analysis["sources"]:
        source_manifest, evidence = _read(store, mapping[source["source_id"]], context,
            source=source["organization"], dataset="source-evidence", schema="fundamental-source-evidence-v1")
        if evidence.get("identity") != source or not isinstance(evidence.get("content"), str) or not evidence["content"].strip():
            raise ValueError("source evidence identity/content mismatch")
        published = _instant(evidence["published_at"])
        if published != _instant(source_manifest.provider_timestamp):
            raise ValueError("source publication timestamp mismatch")
        available = _instant(source_manifest.available_at)
        observed = date.fromisoformat(source["observed_at"])
        if not published.date() <= observed <= available.date() or available > _instant(manifest.available_at):
            raise ValueError("source was unavailable when proposal was collected")
        if source["source_type"] == "company_filing" and published.date() < current:
            raise ValueError("filing predates the claimed completed fiscal period")
        sources.append({"source_id": source["source_id"], "manifest_id": source_manifest.manifest_id,
                        "content_hash": source_manifest.content_sha256, "published_at": published.isoformat(),
                        "available_at": available.isoformat()})
    floor = Decimal(spec.minimum_positive_eps)
    for value in (analysis["earnings_model"]["consensus_eps_usd"], analysis["earnings_model"]["market_implied_eps_usd"]):
        if isinstance(value, bool):
            raise ValueError("EPS is not a boolean")
        number = Decimal(str(value))
        if not number.is_finite() or number < floor:
            raise ValueError("EPS denominator is missing, negative or too close to zero")
    # Reuse, never replace, the existing driver, cash-flow, quality and valuation
    # equations. The output is independently validated by the same mature path.
    dossier = build_focused_research_dossier(analysis, policy=policy, code_revision=context.code_revision)
    validate_focused_research_dossier(dossier, as_of_date=context.as_of_utc.date().isoformat(), maximum_age_days=0)
    sections = dossier["research_sections"]
    earnings = sections["earnings_model"]
    quality_result = sections["earnings_quality"]["accruals_and_cash_conversion"]
    base = next(item for item in earnings["scenarios"] if item["name"] == "base")
    decimal = lambda value: Decimal(str(value))
    numeric = {
        "research_base_eps_usd": decimal(base["eps_usd"]),
        "research_consensus_eps_usd": decimal(earnings["consensus_eps_usd"]),
        "research_eps_gap_to_consensus": growth(decimal(base["eps_usd"]), decimal(earnings["consensus_eps_usd"])),
        "research_base_fcf_margin": fcf_margin(decimal(base["free_cash_flow_usd_millions"]), decimal(base["revenue_usd_millions"])),
        "research_incremental_roic": decimal(base["incremental_roic_percent"]) / 100,
        "reported_accrual_ratio": decimal(quality_result["accrual_ratio_percent_of_average_assets"]) / 100,
        "reported_cash_conversion": decimal(quality_result["cash_conversion_percent"]) / 100,
    }
    if any(not value.is_finite() for value in numeric.values()):
        raise ValueError("fundamental numeric output is not finite")
    return FundamentalFeatureRun(_json({
        "schema_version": "fundamental-feature-run-v1", "validation_scope": "MECHANISM_ONLY",
        "semantic_type": "RESEARCH_NUMERIC_INPUT", "spec_hash": spec.spec_hash,
        "instrument_id": spec.instrument_id, "symbol": spec.symbol,
        "proposal_manifest_id": manifest.manifest_id, "proposal_hash": manifest.content_sha256,
        "policy_hash": spec.policy_hash, "period_contract": periods, "source_evidence": sources,
        "computed_at": context.as_of_utc.isoformat(), "information_cutoff": context.information_cutoff_utc.isoformat(),
        "code_revision": context.code_revision, "dossier_hash": _hash(dossier),
        "numeric_features": {name: str(value) for name, value in numeric.items()},
        "source_interpretation_verified": False, "forecast_authorized": False, "execution_authorized": False,
        "value_roles": {name: "SOURCE_REFERENCED_REPORTED_CLAIM" if name.startswith("reported_") else "MODEL_ASSUMPTION"
                        for name in numeric},
    }))

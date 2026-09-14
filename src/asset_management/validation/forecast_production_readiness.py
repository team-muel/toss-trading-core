"""Read-only, fail-closed readiness checks for AMA-150 PIT input acquisition.

This module deliberately does not collect data, create storage, create a runtime,
or calculate a forecast.  A report is ``READY_FOR_SAMPLE_INGEST`` only when an
already-provisioned evidence store, runtime-bound EXPECTED_RETURN authorization,
and source access are present.  It is never evidence for a canonical run or a
Gate D2 decision.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import json
import os
from pathlib import Path
import sqlite3
from typing import Final

from asset_management.domain.errors import InvariantViolation
from asset_management.governance import ModelScope, RuntimeModelRegistryEvidenceRepository
from asset_management.time.clock import SystemClock


REQUIRED_EVIDENCE_TABLES: Final = (
    "am_runtime_run", "am_runtime_model_registry", "am_model_registry_snapshot",
    "am_temporal_observation", "am_ingestion_run", "am_dataset_manifest",
)
_REQUIRED_STORE_DIRECTORIES: Final = ("bronze", "silver", "catalog/manifests")


@dataclass(frozen=True, slots=True)
class SourceRequirement:
    """One minimum provider capability; it never represents a numerical input."""

    source_id: str
    manifest_source: str | None
    access_env: str | None
    state: str
    purpose: str


@dataclass(frozen=True, slots=True)
class InstrumentInputRequirement:
    instrument_id: str
    asset_class: str
    component: str
    source_id: str
    notes: str


# Selected sources are only eligible to supply their documented raw material.
# ``UNASSIGNED`` is intentional: accepting an arbitrary Internet response or a
# caller-supplied zero would weaken the economic-input authority boundary.
SOURCE_REQUIREMENTS: Final = {
    "tiingo_eod": SourceRequirement(
        "tiingo_eod", "tiingo-eod", "TIINGO_API_TOKEN", "SELECTED",
        "PIT ETF total-return market observations"),
    "fred_alfred": SourceRequirement(
        "fred_alfred", "fred-alfred", "FRED_API_KEY", "SELECTED",
        "approved PIT macro and rate observations"),
    "issuer_official_fund": SourceRequirement(
        "issuer_official_fund", None, None, "UNASSIGNED",
        "issuer-published fund facts, distributions, structure, duration, and expense"),
    "approved_factor_model": SourceRequirement(
        "approved_factor_model", None, None, "UNASSIGNED",
        "governed factor exposure methodology and PIT factor inputs"),
    "approved_treasury_curve": SourceRequirement(
        "approved_treasury_curve", None, None, "UNASSIGNED",
        "PIT Treasury curve sufficient for roll-down and duration semantics"),
    "approved_spot_market": SourceRequirement(
        "approved_spot_market", None, None, "UNASSIGNED",
        "PIT physical commodity spot and fund valuation observations"),
    "approved_fx_provider": SourceRequirement(
        "approved_fx_provider", None, None, "UNASSIGNED",
        "PIT FX source and reporting-currency inclusion semantics"),
    "approved_bond_methodology": SourceRequirement(
        "approved_bond_methodology", None, None, "UNASSIGNED",
        "governed credit/default/expense component semantics; zero is not implied"),
}


def _requirements(instruments: tuple[str, ...]) -> tuple[InstrumentInputRequirement, ...]:
    rows: list[InstrumentInputRequirement] = []
    for instrument in instruments:
        if instrument in {"SPY", "QQQ", "VTV"}:
            asset_class = "EQUITY_ETF"
            sources = {
                "underlying_growth": "issuer_official_fund",
                "distribution_yield": "issuer_official_fund",
                "valuation_reversion": "issuer_official_fund",
                "factor_exposure": "approved_factor_model",
                "momentum_overlay": "tiingo_eod",
            }
        elif instrument == "TLT":
            asset_class = "BOND_ETF"
            sources = {
                "yield": "issuer_official_fund",
                "roll_down": "approved_treasury_curve",
                "duration_effect": "issuer_official_fund",
                "credit_spread_effect": "approved_bond_methodology",
                "default_expense_drag": "approved_bond_methodology",
            }
        elif instrument == "GLD":
            asset_class = "COMMODITY_ETF"
            # GLD is physical-backed.  The assembler omits roll_yield only after
            # immutable structure evidence proves that fact; this catalog must
            # not silently substitute a caller-authored zero for it.
            sources = {
                "spot_change": "approved_spot_market",
                "carry": "issuer_official_fund",
                "expense": "issuer_official_fund",
            }
        elif instrument == "SGOV":
            asset_class = "CASH"
            sources = {
                "current_yield": "issuer_official_fund",
                "expense": "issuer_official_fund",
                "fx_effect": "approved_fx_provider",
            }
        else:
            raise ValueError(f"AMA150_UNSUPPORTED_INSTRUMENT:{instrument}")
        for component, source_id in sources.items():
            rows.append(InstrumentInputRequirement(
                instrument, asset_class, component, source_id,
                "Requires provider-originated PIT evidence, Bronze/Silver manifests, and runtime binding."))
    return tuple(rows)


TARGET_INSTRUMENTS: Final = ("SPY", "QQQ", "VTV", "TLT", "GLD", "SGOV")
MINIMUM_INPUTS: Final = _requirements(TARGET_INSTRUMENTS)


def _table_names(connection: sqlite3.Connection) -> set[str]:
    return {str(row[0]) for row in connection.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    )}


def _store_status(root: Path | None) -> tuple[str, list[str], dict[str, int]]:
    if root is None or not root.is_dir():
        return "UNAVAILABLE", ["IMMUTABLE_RESEARCH_STORE_MISSING"], {}
    missing = [relative for relative in _REQUIRED_STORE_DIRECTORIES if not (root / relative).is_dir()]
    if missing:
        return "UNAVAILABLE", ["IMMUTABLE_RESEARCH_STORE_LAYOUT_MISSING:" + ",".join(missing)], {}
    counts: dict[str, int] = {}
    malformed = False
    for path in (root / "catalog" / "manifests").glob("*.json"):
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
            source = str(value["source"])
            layer = str(value["layer"])
        except (OSError, KeyError, TypeError, json.JSONDecodeError):
            malformed = True
            continue
        if layer in {"bronze", "silver"}:
            counts[f"{source}:{layer}"] = counts.get(f"{source}:{layer}", 0) + 1
    if malformed:
        return "UNAVAILABLE", ["IMMUTABLE_RESEARCH_MANIFEST_INVALID"], counts
    return "AVAILABLE", [], counts


def _runtime_status(connection: sqlite3.Connection | None, *, runtime_run_id: str | None,
                    missing_tables: set[str], database_reason: str | None) -> tuple[str, list[str], tuple[str, ...]]:
    if connection is None:
        return "UNAVAILABLE", [database_reason or "EVIDENCE_DATABASE_MISSING"], ()
    if missing_tables:
        return "UNAVAILABLE", ["EVIDENCE_SCHEMA_MISSING:" + ",".join(sorted(missing_tables))], ()
    if not runtime_run_id:
        return "UNAVAILABLE", ["RUNTIME_RUN_ID_REQUIRED"], ()
    if connection.execute("SELECT 1 FROM am_runtime_run WHERE runtime_run_id=?", (runtime_run_id,)).fetchone() is None:
        return "UNAVAILABLE", ["RUNTIME_RUN_UNAVAILABLE"], ()
    try:
        keys = RuntimeModelRegistryEvidenceRepository(connection, SystemClock()).authorized_model_keys(
            runtime_run_id, scope=ModelScope.EXPECTED_RETURN)
    except (InvariantViolation, sqlite3.DatabaseError):
        return "UNAVAILABLE", ["EXPECTED_RETURN_MODEL_AUTHORIZATION_UNAVAILABLE"], ()
    if not keys:
        return "UNAVAILABLE", ["EXPECTED_RETURN_MODEL_AUTHORIZATION_UNAVAILABLE"], ()
    return "AVAILABLE", [], keys


def evaluate_production_readiness(*, connection: sqlite3.Connection | None,
                                  immutable_store: Path | None,
                                  runtime_run_id: str | None,
                                  environment: Mapping[str, str] | None = None,
                                  database_reason: str | None = None) -> dict[str, object]:
    """Inspect only existing evidence and provider access; never write or fetch."""
    environment = os.environ if environment is None else environment
    tables = _table_names(connection) if connection is not None else set()
    missing_tables = set(REQUIRED_EVIDENCE_TABLES) - tables
    runtime_state, runtime_reasons, model_keys = _runtime_status(
        connection, runtime_run_id=runtime_run_id, missing_tables=missing_tables,
        database_reason=database_reason)
    store_state, store_reasons, manifest_counts = _store_status(immutable_store)
    providers = []
    for source in SOURCE_REQUIREMENTS.values():
        credential_present = bool(source.access_env and environment.get(source.access_env, "").strip())
        state = ("UNAVAILABLE" if source.state == "UNASSIGNED" else
                 "AVAILABLE" if credential_present else "UNAVAILABLE")
        reason = ("PROVIDER_CONTRACT_UNASSIGNED" if source.state == "UNASSIGNED" else
                  "PROVIDER_CREDENTIAL_MISSING" if not credential_present else None)
        providers.append({"source_id": source.source_id, "state": state,
                          "access_env": source.access_env, "credential_present": credential_present,
                          "purpose": source.purpose, "reason": reason})
    inputs = []
    for item in MINIMUM_INPUTS:
        provider = SOURCE_REQUIREMENTS[item.source_id]
        state = "UNAVAILABLE" if provider.state == "UNASSIGNED" else "PENDING_SAMPLE_INGEST"
        inputs.append({"instrument_id": item.instrument_id, "asset_class": item.asset_class,
                       "component": item.component, "source_id": item.source_id,
                       "state": state, "notes": item.notes})
    sample_sources = {
        source_id: {"manifest_source": source.manifest_source,
                    "bronze_manifest_count": (manifest_counts.get(
                        f"{source.manifest_source}:bronze", 0) if source.manifest_source else 0),
                    "silver_manifest_count": (manifest_counts.get(
                        f"{source.manifest_source}:silver", 0) if source.manifest_source else 0)}
        for source_id, source in SOURCE_REQUIREMENTS.items()
    }
    selected_source_ids = tuple(source.source_id for source in SOURCE_REQUIREMENTS.values()
                                if source.state == "SELECTED")
    # One successful Tiingo sample cannot stand in for FRED/ALFRED provenance.
    # Every selected collection route needs both immutable layers before an
    # operator may call the environment sample-ingest ready.
    sample_state = "AVAILABLE" if all(
        sample_sources[source_id]["bronze_manifest_count"] and
        sample_sources[source_id]["silver_manifest_count"]
        for source_id in selected_source_ids) else "UNAVAILABLE"
    reasons = [*runtime_reasons, *store_reasons]
    if any(value["state"] == "UNAVAILABLE" for value in providers):
        reasons.append("PROVIDER_QUALIFICATION_OR_CREDENTIALS_INCOMPLETE")
    if sample_state != "AVAILABLE":
        reasons.append("BRONZE_SILVER_SAMPLE_INGEST_UNAVAILABLE")
    # A calculation remains impossible until every required source is qualified,
    # source-bound samples exist, and the runtime has an active authorized model.
    return {
        "schema_version": "ama150-production-readiness@1",
        "status": "BLOCKED" if reasons else "READY_FOR_SAMPLE_INGEST",
        "mode": "READ_ONLY_READINESS",
        "prohibitions": ["CANONICAL_PRODUCTION_RUN", "GATE_D2_PASS", "M5", "LIVE_TRADING"],
        "runtime": {"state": runtime_state, "runtime_run_id": runtime_run_id,
                    "authorized_expected_return_models": list(model_keys), "reasons": runtime_reasons},
        "immutable_store": {"state": store_state, "path": str(immutable_store) if immutable_store else None,
                            "manifest_counts": manifest_counts, "reasons": store_reasons},
        "providers": providers,
        "minimum_inputs": inputs,
        "sample_ingest": {"state": sample_state, "required_selected_sources": list(selected_source_ids),
                          "sources": sample_sources,
                          "does_not_create_economic_inputs": True},
        "reasons": sorted(set(reasons)),
    }

"""Repository-backed, fail-closed economic component forecast assembly."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, localcontext
from enum import StrEnum
from typing import Mapping
import json

from asset_management.data.asof_query import AsOfRepository
from asset_management.data.immutable import canonical, digest
from asset_management.domain.errors import DataQualityError, InvariantViolation
from asset_management.domain.horizon import DecayProfile, SignalValidity
from asset_management.domain.economics import CurrencyBasis
from asset_management.domain.scalars import Currency
from asset_management.time.asof import AsOfContext
from asset_management.governance import (
    ModelScope, RuntimeModelAuthorization, RuntimeModelRegistryEvidenceRepository,
)

from .confidence import shrink_component
from asset_management.quality.models import QualityStatus

from .engine import COMPONENTS
from .models import AssetClass, ExpectedReturnComponent


class CommodityStructure(StrEnum):
    PHYSICAL_BACKED = "PHYSICAL_BACKED"
    FUTURES_BACKED = "FUTURES_BACKED"
    OTHER = "OTHER"


@dataclass(frozen=True, slots=True)
class ComponentForecastLineage:
    """One immutable persisted input selected by the canonical PIT query."""

    component_name: str
    observation_id: str
    observation_hash: str
    dataset_manifest_id: str
    dataset_manifest_hash: str
    model_version: str
    schema_version: str
    available_at_utc: datetime
    annualized_return: Decimal
    prior_annualized_return: Decimal
    annualized_uncertainty: Decimal
    confidence: Decimal
    economic_exposure_id: str

    def __post_init__(self) -> None:
        if (not self.component_name.strip() or not self.observation_id.strip() or
                not self.model_version.strip() or not self.schema_version.strip() or
                not self.economic_exposure_id.strip() or self.available_at_utc.tzinfo is None or
                self.available_at_utc.utcoffset() is None or
                any(not value.is_finite() for value in (self.annualized_return, self.prior_annualized_return,
                                                        self.annualized_uncertainty, self.confidence)) or
                any(len(value) != 64 or any(char not in "0123456789abcdef" for char in value)
                    for value in (self.observation_hash, self.dataset_manifest_id,
                                  self.dataset_manifest_hash))):
            raise DataQualityError("COMPONENT_FORECAST_LINEAGE_INVALID")


def _bundle_hash(*, runtime_run_id: str, instrument_id: str, asset_class: AssetClass,
                 information_cutoff_utc: datetime, currency: Currency,
                 currency_basis: CurrencyBasis, horizon: int,
                 components: tuple[ExpectedReturnComponent, ...],
                 applicable_component_names: tuple[str, ...],
                 lineage: tuple[ComponentForecastLineage, ...],
                 calculation_formula_version: str) -> str:
    """Canonical calculation identity, including selected inputs and generated values."""
    return digest(canonical({
        "runtime_run_id": runtime_run_id, "instrument_id": instrument_id,
        "asset_class": asset_class.value, "cutoff": information_cutoff_utc.isoformat(),
        "currency": currency.value, "currency_basis": currency_basis.value, "horizon": horizon,
        "formula_version": calculation_formula_version,
        "repository_selection": "as-of-repository.get-latest@1",
        "applicable_component_names": list(applicable_component_names),
        "selected_inputs": [{"component": item.component_name,
                             "observation_id": item.observation_id,
                             "observation_hash": item.observation_hash,
                             "dataset_manifest_id": item.dataset_manifest_id,
                             "dataset_manifest_hash": item.dataset_manifest_hash,
                             "model_version": item.model_version,
                             "schema_version": item.schema_version,
                             "available_at": item.available_at_utc.isoformat(),
                             "annualized_return": str(item.annualized_return),
                             "prior_annualized_return": str(item.prior_annualized_return),
                             "annualized_uncertainty": str(item.annualized_uncertainty),
                             "confidence": str(item.confidence),
                             "economic_exposure_id": item.economic_exposure_id} for item in lineage],
        "generated_components": [{"component": item.component_name,
                                  "point_estimate": str(item.point_estimate),
                                  "uncertainty": str(item.uncertainty),
                                  "confidence": str(item.confidence),
                                  "input_features": list(item.input_features),
                                  "horizon": item.horizon,
                                  "validity": item.validity.payload()} for item in components],
    }))


@dataclass(frozen=True, slots=True)
class ComponentForecastBundle:
    """Typed downstream input; it is not an alpha or pricing-baseline result."""
    runtime_run_id: str
    instrument_id: str
    asset_class: AssetClass
    information_cutoff_utc: datetime
    currency: Currency
    currency_basis: CurrencyBasis
    horizon: int
    components: tuple[ExpectedReturnComponent, ...]
    applicable_component_names: tuple[str, ...]
    lineage: tuple[ComponentForecastLineage, ...]
    calculation_formula_version: str
    lineage_id: str

    def __post_init__(self) -> None:
        if (not self.runtime_run_id.strip() or not self.instrument_id.strip() or
                self.information_cutoff_utc.tzinfo is None or
                self.information_cutoff_utc.utcoffset() is None or
                len(self.components) != len(self.lineage) or
                len(self.components) != len(self.applicable_component_names) or not self.components or
                not self.calculation_formula_version.strip() or
                len(self.lineage_id) != 64 or any(c not in "0123456789abcdef" for c in self.lineage_id)):
            raise DataQualityError("COMPONENT_FORECAST_BUNDLE_INVALID")
        if self.lineage_id != _bundle_hash(
                runtime_run_id=self.runtime_run_id, instrument_id=self.instrument_id,
                asset_class=self.asset_class, information_cutoff_utc=self.information_cutoff_utc,
                currency=self.currency, currency_basis=self.currency_basis, horizon=self.horizon,
                components=self.components, applicable_component_names=self.applicable_component_names,
                lineage=self.lineage, calculation_formula_version=self.calculation_formula_version):
            raise DataQualityError("COMPONENT_FORECAST_LINEAGE_UNVERIFIED")


@dataclass(frozen=True, slots=True)
class GrossComponentForecast:
    """Typed gross forecast; it deliberately cannot stand in for a net return."""

    bundle: ComponentForecastBundle
    gross_expected_return: Decimal
    lower_bound: Decimal
    upper_bound: Decimal
    confidence: Decimal
    quality_status: QualityStatus

    def __post_init__(self) -> None:
        components = self.bundle.components
        uncertainty = sum(item.uncertainty for item in components)
        if (self.gross_expected_return != sum(item.point_estimate for item in components) or
                self.lower_bound != self.gross_expected_return - Decimal("1.96") * uncertainty or
                self.upper_bound != self.gross_expected_return + Decimal("1.96") * uncertainty or
                self.confidence != min(item.confidence for item in components) or
                self.quality_status is not QualityStatus.VALID):
            raise DataQualityError("COMPONENT_FORECAST_GROSS_INVALID")


@dataclass(frozen=True, slots=True)
class PersistedGrossComponentForecast:
    calculation_id: str
    runtime_run_id: str
    gross_expected_return: Decimal
    lower_bound: Decimal
    upper_bound: Decimal
    confidence: Decimal


def _decimal(value: object, label: str) -> Decimal:
    if not isinstance(value, str):
        raise DataQualityError(f"ECONOMIC_INPUT_{label}_INVALID")
    try:
        parsed = Decimal(value)
    except Exception as exc:
        raise DataQualityError(f"ECONOMIC_INPUT_{label}_INVALID") from exc
    if not parsed.is_finite():
        raise DataQualityError(f"ECONOMIC_INPUT_{label}_INVALID")
    return parsed


def _horizon_return(annual: Decimal, horizon: int) -> Decimal:
    if annual <= Decimal("-1"):
        raise DataQualityError("ECONOMIC_INPUT_RETURN_INVALID")
    with localcontext() as context:
        context.prec = 34
        return (Decimal(1) + annual) ** (Decimal(horizon) / Decimal(252)) - Decimal(1)


def _fresh_until(value: object) -> datetime:
    if not isinstance(value, str):
        raise DataQualityError("ECONOMIC_INPUT_FRESHNESS_INVALID")
    try:
        result = datetime.fromisoformat(value)
    except ValueError as exc:
        raise DataQualityError("ECONOMIC_INPUT_FRESHNESS_INVALID") from exc
    if result.tzinfo is None or result.utcoffset() is None:
        raise DataQualityError("ECONOMIC_INPUT_FRESHNESS_INVALID")
    return result


def _authorization_payload(value: RuntimeModelAuthorization) -> dict[str, str]:
    return {
        "runtime_run_id": value.runtime_run_id,
        "model_registry_snapshot_id": value.model_registry_snapshot_id,
        "binding_hash": value.binding_hash,
        "model_key": value.authorization.model_key,
        "scope": value.authorization.scope.value,
        "registry_hash": value.authorization.registry_hash,
        "authorized_at": value.authorization.authorized_at,
        "authorization_hash": value.authorization.authorization_hash,
    }


def _validity_from_payload(value: object) -> SignalValidity:
    if not isinstance(value, Mapping) or set(value) != {
            "forecast_horizon", "holding_horizon", "valid_until", "decay_profile",
            "half_life_seconds"}:
        raise DataQualityError("COMPONENT_FORECAST_VALIDITY_INVALID")
    if (type(value["forecast_horizon"]) is not int or
            type(value["holding_horizon"]) is not int or
            not isinstance(value["valid_until"], str) or
            not isinstance(value["decay_profile"], str) or
            (value["half_life_seconds"] is not None and
             type(value["half_life_seconds"]) is not int)):
        raise DataQualityError("COMPONENT_FORECAST_VALIDITY_INVALID")
    try:
        return SignalValidity(
            value["forecast_horizon"], value["holding_horizon"],
            datetime.fromisoformat(value["valid_until"]),
            DecayProfile(value["decay_profile"]), value["half_life_seconds"],
        )
    except Exception as exc:
        raise DataQualityError("COMPONENT_FORECAST_VALIDITY_INVALID") from exc


class ComponentForecastAssembler:
    """Selects only persisted PIT inputs; callers cannot supply economic values."""

    def __init__(self, repository: AsOfRepository,
                 model_evidence: RuntimeModelRegistryEvidenceRepository) -> None:
        if (not isinstance(repository, AsOfRepository) or
                not isinstance(model_evidence, RuntimeModelRegistryEvidenceRepository) or
                repository.connection is not model_evidence.connection):
            raise DataQualityError("COMPONENT_FORECAST_REPOSITORY_REQUIRED")
        self._repository = repository
        self._model_evidence = model_evidence

    def assemble(self, *, context: AsOfContext, instrument_id: str,
                 asset_class: AssetClass, currency: Currency,
                 currency_basis: CurrencyBasis, horizon: int,
                 validity: SignalValidity,
                 model_key: str, runtime_authorization: RuntimeModelAuthorization,
                 commodity_structure: CommodityStructure | None = None) -> ComponentForecastBundle:
        if (not isinstance(asset_class, AssetClass) or not isinstance(currency, Currency) or
                not isinstance(currency_basis, CurrencyBasis) or horizon != validity.forecast_horizon or
                context.run_id.strip() == ""):
            raise DataQualityError("COMPONENT_FORECAST_CONTEXT_INVALID")
        if asset_class is AssetClass.EQUITY:
            raise DataQualityError("COMPONENT_FORECAST_EQUITY_GROWTH_BASIS_UNSUPPORTED")
        self._repository.require_exact_runtime_context(context)
        try:
            self._model_evidence.require_authorization(
                runtime_authorization, model_key=model_key, scope=ModelScope.EXPECTED_RETURN,
                at=context.information_cutoff_utc,
            )
        except InvariantViolation as exc:
            raise DataQualityError("COMPONENT_FORECAST_MODEL_AUTHORIZATION_INVALID") from exc
        if runtime_authorization.runtime_run_id != context.run_id:
            raise DataQualityError("COMPONENT_FORECAST_MODEL_RUNTIME_CONFLICT")
        names = COMPONENTS[asset_class]
        if asset_class is AssetClass.COMMODITY_ETF:
            if not isinstance(commodity_structure, CommodityStructure):
                raise DataQualityError("COMMODITY_STRUCTURE_REQUIRED")
            if commodity_structure is CommodityStructure.OTHER:
                raise DataQualityError("COMMODITY_STRUCTURE_UNSUPPORTED")
            if commodity_structure is CommodityStructure.PHYSICAL_BACKED:
                names = tuple(name for name in names if name != "roll_yield")
        elif commodity_structure is not None:
            raise DataQualityError("COMMODITY_STRUCTURE_UNEXPECTED")

        components: list[ExpectedReturnComponent] = []
        lineage: list[ComponentForecastLineage] = []
        for name in names:
            observation = self._repository.get_latest(
                entity_id=instrument_id, field=f"economic-input/{name}", context=context
            )
            if observation.dataset_manifest_id is None:
                raise DataQualityError("ECONOMIC_INPUT_IMMUTABLE_MANIFEST_REQUIRED")
            manifest_hash = self._repository.require_manifest_bound_to_runtime(
                dataset_manifest_id=observation.dataset_manifest_id, runtime_run_id=context.run_id)
            value = observation.value
            if not isinstance(value, Mapping):
                raise DataQualityError("ECONOMIC_INPUT_PAYLOAD_INVALID")
            if (value.get("semantic_type") != "ECONOMIC_COMPONENT_INPUT" or
                    value.get("runtime_run_id") != context.run_id or
                    value.get("information_cutoff_utc") != context.information_cutoff_utc.isoformat() or
                    value.get("currency") != currency.value or
                    value.get("currency_basis") != currency_basis.value or
                    value.get("compounding") != "ANNUAL_EFFECTIVE" or
                    value.get("model_version") != model_key or
                    not isinstance(value.get("economic_exposure_id"), str) or
                    not value["economic_exposure_id"].strip()):
                raise DataQualityError("ECONOMIC_INPUT_CONTEXT_CONFLICT")
            if _fresh_until(value.get("fresh_until_utc")) <= context.information_cutoff_utc:
                raise DataQualityError("ECONOMIC_INPUT_STALE")
            annual = _decimal(value.get("annualized_return"), "RETURN")
            prior = _decimal(value.get("prior_annualized_return"), "PRIOR")
            uncertainty = _decimal(value.get("annualized_uncertainty"), "UNCERTAINTY")
            confidence = _decimal(value.get("confidence"), "CONFIDENCE")
            if uncertainty < 0 or not Decimal(0) <= confidence <= Decimal(1):
                raise DataQualityError("ECONOMIC_INPUT_CONFIDENCE_INVALID")
            component = shrink_component(raw_estimate=_horizon_return(annual, horizon),
                                         prior=_horizon_return(prior, horizon), confidence=confidence,
                                         uncertainty=uncertainty * Decimal(horizon) / Decimal(252),
                                         component_name=name,
                                         input_features=(f"observation:{observation.content_hash}",),
                                         horizon=horizon, validity=validity)
            components.append(component)
            lineage.append(ComponentForecastLineage(
                component_name=name, observation_id=observation.observation_id,
                observation_hash=observation.content_hash,
                dataset_manifest_id=observation.dataset_manifest_id,
                dataset_manifest_hash=manifest_hash,
                model_version=value["model_version"], schema_version=observation.schema_version,
                available_at_utc=observation.available_at, annualized_return=annual,
                prior_annualized_return=prior, annualized_uncertainty=uncertainty,
                confidence=confidence, economic_exposure_id=value["economic_exposure_id"],
            ))
        if len({item.economic_exposure_id for item in lineage}) != len(lineage):
            raise DataQualityError("ECONOMIC_INPUT_EXPOSURE_DOUBLE_COUNTED")
        result_components = tuple(components)
        result_lineage = tuple(lineage)
        formula_version = "component-forecast-assembler@1"
        lineage_id = _bundle_hash(
            runtime_run_id=context.run_id, instrument_id=instrument_id, asset_class=asset_class,
            information_cutoff_utc=context.information_cutoff_utc, currency=currency,
            currency_basis=currency_basis, horizon=horizon, components=result_components,
            applicable_component_names=names, lineage=result_lineage,
            calculation_formula_version=formula_version,
        )
        return ComponentForecastBundle(context.run_id, instrument_id, asset_class,
                                       context.information_cutoff_utc, currency, currency_basis,
                                       horizon, result_components, names, result_lineage, formula_version,
                                       lineage_id)

    def _persist(self, bundle: ComponentForecastBundle) -> PersistedGrossComponentForecast:
        gross = sum(item.point_estimate for item in bundle.components)
        uncertainty = sum(item.uncertainty for item in bundle.components)
        authorization = self._model_evidence.authorize(
            bundle.runtime_run_id, model_key=bundle.lineage[0].model_version,
            scope=ModelScope.EXPECTED_RETURN)
        artifact = {
            "schema_version": "component-forecast-calculation@1",
            "semantic_contract": "FORECAST_TOTAL_RETURN_GROSS",
            "runtime_run_id": bundle.runtime_run_id,
            "information_cutoff_utc": bundle.information_cutoff_utc.isoformat(),
            "instrument_id": bundle.instrument_id, "asset_class": bundle.asset_class.value,
            "currency": bundle.currency.value, "currency_basis": bundle.currency_basis.value,
            "horizon": bundle.horizon, "compounding": "ANNUAL_EFFECTIVE",
            "formula_version": bundle.calculation_formula_version,
            "commodity_structure": (None if bundle.asset_class is not AssetClass.COMMODITY_ETF else
                                    (CommodityStructure.PHYSICAL_BACKED.value if "roll_yield" not in bundle.applicable_component_names
                                     else CommodityStructure.FUTURES_BACKED.value)),
            "applicable_component_names": list(bundle.applicable_component_names),
            "model_authorization": _authorization_payload(authorization),
            "components": [{"component_type": item.component_name,
                            "point_estimate": str(item.point_estimate),
                            "uncertainty": str(item.uncertainty),
                            "confidence": str(item.confidence),
                            "input_features": list(item.input_features),
                            "validity": item.validity.payload()} for item in bundle.components],
            "observation_lineage": [{
                "component_type": item.component_name, "observation_id": item.observation_id,
                "observation_hash": item.observation_hash,
                "dataset_manifest_id": item.dataset_manifest_id,
                "dataset_manifest_hash": item.dataset_manifest_hash,
                "schema_version": item.schema_version,
                "available_at_utc": item.available_at_utc.isoformat(),
                "annualized_return": str(item.annualized_return),
                "prior_annualized_return": str(item.prior_annualized_return),
                "annualized_uncertainty": str(item.annualized_uncertainty),
                "confidence": str(item.confidence),
                "economic_exposure_id": item.economic_exposure_id,
            } for item in bundle.lineage],
            "gross_forecast": {"gross_expected_return": str(gross),
                               "lower_bound": str(gross - Decimal("1.96") * uncertainty),
                               "upper_bound": str(gross + Decimal("1.96") * uncertainty),
                               "confidence": str(min(item.confidence for item in bundle.components))},
        }
        calculation_id = digest(canonical(artifact))
        row = self._repository.connection.execute(
            "SELECT canonical_artifact_json, content_hash FROM am_component_forecast_calculation WHERE component_forecast_calculation_id=?",
            (calculation_id,)).fetchone()
        if row is None:
            with self._repository.connection:
                self._repository.connection.execute(
                    "INSERT INTO am_component_forecast_calculation VALUES (?, ?, ?, ?, ?)",
                    (calculation_id, bundle.runtime_run_id,
                     json.dumps(artifact, sort_keys=True, separators=(",", ":")),
                     calculation_id, bundle.information_cutoff_utc.isoformat()))
        elif json.loads(str(row[0])) != artifact or row[1] != calculation_id:
            raise DataQualityError("COMPONENT_FORECAST_CALCULATION_CONFLICT")
        return PersistedGrossComponentForecast(calculation_id, bundle.runtime_run_id, gross,
            gross-Decimal("1.96")*uncertainty, gross+Decimal("1.96")*uncertainty,
            min(item.confidence for item in bundle.components))

    def assemble_gross_forecast(self, **arguments: object) -> PersistedGrossComponentForecast:
        """Only public typed consumer; net return requires separately persisted cost evidence."""
        bundle = self.assemble(**arguments)
        return self._persist(bundle)

    def replay_gross_forecast(self, *, calculation_id: str) -> PersistedGrossComponentForecast:
        if not isinstance(calculation_id, str) or len(calculation_id) != 64:
            raise DataQualityError("COMPONENT_FORECAST_CALCULATION_UNAVAILABLE")
        row = self._repository.connection.execute(
            "SELECT runtime_run_id,canonical_artifact_json,content_hash,calculated_at_utc FROM am_component_forecast_calculation WHERE component_forecast_calculation_id=?",
            (calculation_id,)).fetchone()
        if row is None:
            raise DataQualityError("COMPONENT_FORECAST_CALCULATION_UNAVAILABLE")
        try:
            artifact = json.loads(str(row[1]))
        except (TypeError, json.JSONDecodeError) as exc:
            raise DataQualityError("COMPONENT_FORECAST_LINEAGE_UNVERIFIED") from exc
        if (not isinstance(artifact, dict) or digest(canonical(artifact)) != calculation_id or
                row[2] != calculation_id or artifact.get("runtime_run_id") != row[0]):
            raise DataQualityError("COMPONENT_FORECAST_LINEAGE_UNVERIFIED")
        required = {"schema_version", "semantic_contract", "runtime_run_id",
                    "information_cutoff_utc", "instrument_id", "asset_class", "currency",
                    "currency_basis", "horizon", "compounding", "formula_version",
                    "commodity_structure", "applicable_component_names", "model_authorization", "components",
                    "observation_lineage", "gross_forecast"}
        if (set(artifact) != required or artifact["schema_version"] != "component-forecast-calculation@1" or
                artifact["semantic_contract"] != "FORECAST_TOTAL_RETURN_GROSS" or
                artifact["compounding"] != "ANNUAL_EFFECTIVE" or
                artifact["formula_version"] != "component-forecast-assembler@1"):
            raise DataQualityError("COMPONENT_FORECAST_SEMANTIC_CONTRACT_INVALID")
        runtime = self._repository.connection.execute(
            "SELECT as_of_utc,information_cutoff_utc,code_revision FROM am_runtime_run WHERE runtime_run_id=?",
            (row[0],)).fetchone()
        if runtime is None or artifact["information_cutoff_utc"] != runtime[1] or row[3] != runtime[1]:
            raise DataQualityError("COMPONENT_FORECAST_RUNTIME_CONFLICT")
        model = artifact["model_authorization"]
        try:
            authorization = self._model_evidence.authorize(
                row[0], model_key=str(model["model_key"]), scope=ModelScope.EXPECTED_RETURN)
        except (InvariantViolation, KeyError, TypeError) as exc:
            raise DataQualityError("COMPONENT_FORECAST_MODEL_AUTHORIZATION_INVALID") from exc
        if _authorization_payload(authorization) != model:
            raise DataQualityError("COMPONENT_FORECAST_MODEL_AUTHORIZATION_INVALID")
        if (not isinstance(artifact["asset_class"], str) or
                not isinstance(artifact["currency"], str) or
                not isinstance(artifact["currency_basis"], str) or
                type(artifact["horizon"]) is not int or artifact["horizon"] <= 0 or
                not isinstance(artifact["information_cutoff_utc"], str) or
                not isinstance(artifact["instrument_id"], str) or
                not artifact["instrument_id"].strip() or
                not isinstance(artifact["applicable_component_names"], list) or
                not all(isinstance(value, str) for value in artifact["applicable_component_names"])):
            raise DataQualityError("COMPONENT_FORECAST_SEMANTIC_CONTRACT_INVALID")
        try:
            asset_class = AssetClass(artifact["asset_class"]); currency = Currency(artifact["currency"])
            currency_basis = CurrencyBasis(artifact["currency_basis"]); horizon = artifact["horizon"]
            cutoff = datetime.fromisoformat(artifact["information_cutoff_utc"]); instrument = artifact["instrument_id"]
            runtime_context = AsOfContext(
                row[0], datetime.fromisoformat(str(runtime[0])), cutoff,
                "persisted-runtime-policy@1", "persisted-runtime-parameters@1", str(runtime[2]),
            )
            sources = artifact["observation_lineage"]; stored_components = artifact["components"]
        except (TypeError, ValueError, KeyError) as exc:
            raise DataQualityError("COMPONENT_FORECAST_SEMANTIC_CONTRACT_INVALID") from exc
        names = tuple(artifact["applicable_component_names"])
        if asset_class is AssetClass.EQUITY:
            raise DataQualityError("COMPONENT_FORECAST_EQUITY_GROWTH_BASIS_UNSUPPORTED")
        expected_names = COMPONENTS[asset_class]
        if asset_class is AssetClass.COMMODITY_ETF and artifact["commodity_structure"] == CommodityStructure.PHYSICAL_BACKED.value:
            expected_names = tuple(name for name in expected_names if name != "roll_yield")
        elif asset_class is AssetClass.COMMODITY_ETF and artifact["commodity_structure"] != CommodityStructure.FUTURES_BACKED.value:
            raise DataQualityError("COMPONENT_FORECAST_SEMANTIC_CONTRACT_INVALID")
        elif asset_class is not AssetClass.COMMODITY_ETF and artifact["commodity_structure"] is not None:
            raise DataQualityError("COMPONENT_FORECAST_SEMANTIC_CONTRACT_INVALID")
        if names != expected_names:
            raise DataQualityError("COMPONENT_FORECAST_SEMANTIC_CONTRACT_INVALID")
        if (not isinstance(sources, list) or not isinstance(stored_components, list) or
                len(sources) != len(names) or len(stored_components) != len(names)):
            raise DataQualityError("COMPONENT_FORECAST_LINEAGE_UNVERIFIED")
        replayed: list[ExpectedReturnComponent] = []
        exposures: set[str] = set()
        for name, source, stored in zip(names, sources, stored_components):
            source_keys = {"component_type", "observation_id", "observation_hash",
                           "dataset_manifest_id", "dataset_manifest_hash", "schema_version", "available_at_utc",
                           "annualized_return", "prior_annualized_return",
                           "annualized_uncertainty", "confidence", "economic_exposure_id"}
            component_keys = {"component_type", "point_estimate", "uncertainty", "confidence",
                              "input_features", "validity"}
            if (not isinstance(source, dict) or set(source) != source_keys or
                    not all(isinstance(source[key], str) for key in source_keys) or
                    not isinstance(stored, dict) or set(stored) != component_keys or
                    not isinstance(stored["component_type"], str) or
                    not isinstance(stored["point_estimate"], str) or
                    not isinstance(stored["uncertainty"], str) or
                    not isinstance(stored["confidence"], str) or
                    not isinstance(stored["input_features"], list) or
                    not all(isinstance(value, str) for value in stored["input_features"]) or
                    source.get("component_type") != name or stored.get("component_type") != name):
                raise DataQualityError("COMPONENT_FORECAST_LINEAGE_UNVERIFIED")
            try:
                observation = self._repository.get_by_id(source["observation_id"])
                canonical_observation = self._repository.get_latest(
                    entity_id=instrument, field=f"economic-input/{name}", context=runtime_context)
            except (DataQualityError, InvariantViolation) as exc:
                raise DataQualityError("COMPONENT_FORECAST_LINEAGE_UNVERIFIED") from exc
            if (observation.entity_id != instrument or observation.field != f"economic-input/{name}" or
                    observation.observation_id != source["observation_id"] or
                    canonical_observation.observation_id != observation.observation_id or
                    canonical_observation.content_hash != observation.content_hash or
                    observation.content_hash != source.get("observation_hash") or
                    observation.dataset_manifest_id != source.get("dataset_manifest_id") or
                    observation.schema_version != source.get("schema_version") or
                    observation.available_at.isoformat() != source.get("available_at_utc") or
                    observation.available_at > cutoff):
                raise DataQualityError("COMPONENT_FORECAST_LINEAGE_UNVERIFIED")
            manifest_hash = self._repository.require_manifest_bound_to_runtime(
                dataset_manifest_id=str(observation.dataset_manifest_id), runtime_run_id=row[0])
            if source.get("dataset_manifest_hash") != manifest_hash:
                raise DataQualityError("COMPONENT_FORECAST_LINEAGE_UNVERIFIED")
            value = observation.value
            if (not isinstance(value, Mapping) or value.get("semantic_type") != "ECONOMIC_COMPONENT_INPUT" or
                    value.get("runtime_run_id") != row[0] or value.get("information_cutoff_utc") != cutoff.isoformat() or
                    value.get("currency") != currency.value or value.get("currency_basis") != currency_basis.value or
                    value.get("compounding") != artifact["compounding"] or value.get("model_version") != model["model_key"] or
                    _fresh_until(value.get("fresh_until_utc")) <= cutoff):
                raise DataQualityError("COMPONENT_FORECAST_SEMANTIC_CONTRACT_INVALID")
            annual = _decimal(value.get("annualized_return"), "RETURN"); prior = _decimal(value.get("prior_annualized_return"), "PRIOR")
            uncertainty = _decimal(value.get("annualized_uncertainty"), "UNCERTAINTY"); confidence = _decimal(value.get("confidence"), "CONFIDENCE")
            exposure = value.get("economic_exposure_id")
            if (source.get("annualized_return") != str(annual) or source.get("prior_annualized_return") != str(prior) or
                    source.get("annualized_uncertainty") != str(uncertainty) or source.get("confidence") != str(confidence) or
                    not isinstance(exposure, str) or source.get("economic_exposure_id") != exposure or exposure in exposures):
                raise DataQualityError("COMPONENT_FORECAST_LINEAGE_UNVERIFIED")
            exposures.add(exposure)
            validity = _validity_from_payload(stored.get("validity"))
            component = shrink_component(raw_estimate=_horizon_return(annual, horizon), prior=_horizon_return(prior, horizon),
                confidence=confidence, uncertainty=uncertainty*Decimal(horizon)/Decimal(252), component_name=name,
                input_features=(f"observation:{observation.content_hash}",), horizon=horizon, validity=validity)
            expected = {"component_type": name, "point_estimate": str(component.point_estimate),
                        "uncertainty": str(component.uncertainty), "confidence": str(component.confidence),
                        "input_features": list(component.input_features), "validity": component.validity.payload()}
            if stored != expected:
                raise DataQualityError("COMPONENT_FORECAST_LINEAGE_UNVERIFIED")
            replayed.append(component)
        gross = sum(item.point_estimate for item in replayed); total_uncertainty = sum(item.uncertainty for item in replayed)
        expected_gross = {"gross_expected_return": str(gross),
                          "lower_bound": str(gross-Decimal("1.96")*total_uncertainty),
                          "upper_bound": str(gross+Decimal("1.96")*total_uncertainty),
                          "confidence": str(min(item.confidence for item in replayed))}
        if artifact["gross_forecast"] != expected_gross:
            raise DataQualityError("COMPONENT_FORECAST_LINEAGE_UNVERIFIED")
        return PersistedGrossComponentForecast(calculation_id, row[0], gross,
            Decimal(expected_gross["lower_bound"]), Decimal(expected_gross["upper_bound"]),
            Decimal(expected_gross["confidence"]))

    def consume_gross_forecast(self, *, calculation_id: str) -> PersistedGrossComponentForecast:
        """Production consumer boundary: a persisted calculation ID is the sole input."""
        return self.replay_gross_forecast(calculation_id=calculation_id)

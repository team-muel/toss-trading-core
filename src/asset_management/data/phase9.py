"""Phase 9 point-in-time collection contracts for market, macro, and company data."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from pathlib import Path
from typing import Callable, Mapping

from asset_management.data.immutable import (
    canonical,
    digest,
    ImmutableDatasetStore,
    IngestionResult,
    ProviderDatasetAdapter,
    StoredDatasetManifest,
)
from asset_management.domain.errors import DataQualityError


PRICE_FIELDS = {
    "provider_instrument_id": "string", "event_time_utc": "string",
    "available_at": "string", "exchange_local_date": "string", "open": "string",
    "high": "string", "low": "string", "close": "string", "volume": "string",
    "currency": "string", "session": "string", "adjustment": "string",
    "source": "string", "source_revision": "string",
}
SESSION_FIELDS = {
    "provider_entity_id": "string", "exchange_local_date": "string", "is_open": "boolean",
    "regular_open_at": "string", "regular_close_at": "string", "event_time_utc": "string",
    "received_at": "string", "available_at": "string", "source": "string",
    "source_revision": "string",
}
ACTION_FIELDS = {
    "provider_entity_id": "string", "action_type": "string", "effective_date": "string",
    "terms": "object", "event_time_utc": "string", "received_at": "string",
    "available_at": "string", "source": "string", "source_revision": "string",
}
FX_FIELDS = {
    "provider_entity_id": "string", "event_time": "string", "available_at": "string",
    "bid": "string", "ask": "string", "mid": "string", "source": "string",
    "source_revision": "string", "quote_type": "string",
}
RATE_FIELDS = {
    "provider_entity_id": "string", "event_time": "string", "available_at": "string",
    "horizon_months": "integer", "rate": "string", "source": "string",
    "source_revision": "string",
}
MACRO_FIELDS = {
    "provider_entity_id": "string", "reference_period": "string", "actual": "string",
    "prior_value": "string", "prior_vintage": "string", "revised_prior": "string",
    "scheduled_release_at": "string", "official_release_at": "string",
    "received_at": "string", "available_at": "string", "source": "string",
    "source_revision": "string",
}
CONSENSUS_FIELDS = {
    "provider_entity_id": "string", "reference_period": "string", "consensus": "string",
    "snapshot_at": "string", "available_at": "string", "source": "string",
    "source_revision": "string", "quality_status": "string",
}
FILING_FIELDS = {
    "provider_entity_id": "string", "form_type": "string", "period_end": "string",
    "filed_at": "string", "accepted_at": "string", "received_at": "string",
    "available_at": "string", "source": "string", "source_revision": "string",
}
FINANCIAL_FIELDS = {
    "provider_entity_id": "string", "reference_period": "string", "period_end": "string",
    "revenue": "string", "gross_profit": "string", "operating_income": "string",
    "net_income": "string", "diluted_eps": "string", "diluted_shares": "string",
    "operating_cash_flow": "string", "capex": "string", "free_cash_flow": "string",
    "cash": "string", "debt": "string", "receivables": "string", "inventory": "string",
    "contract_liabilities": "string", "deferred_revenue": "string",
    "stock_based_compensation": "string", "filed_at": "string", "accepted_at": "string",
    "received_at": "string", "available_at": "string", "source": "string",
    "source_revision": "string",
}
ESTIMATE_FIELDS = {
    "provider_entity_id": "string", "metric": "string", "forecast_period": "string",
    "consensus": "string", "analyst_count": "integer", "snapshot_at": "string",
    "available_at": "string", "source": "string", "source_revision": "string",
    "quality_status": "string",
}

MACRO_SERIES = frozenset({
    "CPI", "CORE_CPI", "PCE", "CORE_PCE", "EMPLOYMENT", "UNEMPLOYMENT",
    "WAGES", "GDP", "PMI_ISM", "POLICY_RATE", "TREASURY_YIELD", "CREDIT_SPREAD_PROXY",
})
FINANCIAL_METRICS = frozenset(FINANCIAL_FIELDS) - {
    "provider_entity_id", "reference_period", "period_end", "filed_at", "accepted_at",
    "received_at", "available_at", "source", "source_revision",
}
FILING_ORDER = {"10-K": 0, "10-Q": 1, "8-K": 2, "EARNINGS_RELEASE": 3}
INITIAL_DAILY_ETFS = frozenset({"SPY", "QQQ", "BIL"})


class CollectionStage(StrEnum):
    TRADING_SESSIONS = "TRADING_SESSIONS"
    CORPORATE_ACTIONS = "CORPORATE_ACTIONS"
    DAILY_PRICES = "DAILY_PRICES"
    FX = "FX"
    RISK_FREE_CURVE = "RISK_FREE_CURVE"
    MACRO = "MACRO"
    CONSENSUS = "CONSENSUS"
    FILINGS = "FILINGS"
    FINANCIALS = "FINANCIALS"
    ANALYST_ESTIMATES = "ANALYST_ESTIMATES"


COLLECTION_ORDER = tuple(CollectionStage)


@dataclass(frozen=True, slots=True)
class ProviderBatch:
    source: str
    dataset: str
    endpoint: str
    http_method: str
    request: object
    status_code: int
    body: object
    provider_timestamp: datetime
    received_at: datetime
    available_at: datetime
    source_revision: str
    schema_version: str
    license_tag: str
    code_revision: str

    def __post_init__(self) -> None:
        for name in ("source", "dataset", "endpoint", "http_method", "source_revision",
                     "schema_version", "license_tag", "code_revision"):
            if not str(getattr(self, name)).strip():
                raise DataQualityError(f"{name.upper()}_MISSING")
        for name in ("provider_timestamp", "received_at", "available_at"):
            value = getattr(self, name)
            if value.tzinfo is None or value.utcoffset() is None:
                raise DataQualityError(f"{name.upper()}_NOT_TIMEZONE_AWARE")
            object.__setattr__(self, name, value.astimezone(timezone.utc))
        if self.provider_timestamp > self.received_at or self.available_at < self.received_at:
            raise DataQualityError("BATCH_TIME_ORDER_INVALID")


def _rows(raw: object) -> list[dict]:
    if not isinstance(raw, dict) or not isinstance(raw.get("result"), list):
        raise DataQualityError("PROVIDER_RESULT_ARRAY_REQUIRED")
    if not all(isinstance(item, dict) for item in raw["result"]):
        raise DataQualityError("PROVIDER_RESULT_OBJECTS_REQUIRED")
    return [dict(item) for item in raw["result"]]


def _text(row: Mapping[str, object], name: str) -> str:
    value = row.get(name)
    if not isinstance(value, str) or not value.strip():
        raise DataQualityError(f"{name.upper()}_MISSING")
    return value


def _integer(row: Mapping[str, object], name: str) -> int:
    value = row.get(name)
    if type(value) is not int:
        raise DataQualityError(f"{name.upper()}_MISSING_OR_INVALID")
    return value


def _decimal(row: Mapping[str, object], name: str, *, positive: bool = False) -> str:
    text = _text(row, name)
    try:
        value = Decimal(text)
    except InvalidOperation as exc:
        raise DataQualityError(f"{name.upper()}_NOT_DECIMAL_STRING") from exc
    if not value.is_finite() or (positive and value <= 0):
        raise DataQualityError(f"{name.upper()}_OUT_OF_RANGE")
    return text


def _instant(row: Mapping[str, object], name: str) -> str:
    text = _text(row, name)
    try:
        value = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise DataQualityError(f"{name.upper()}_INVALID") from exc
    if value.tzinfo is None or value.utcoffset() != timezone.utc.utcoffset(None):
        raise DataQualityError(f"{name.upper()}_NOT_UTC")
    return value.astimezone(timezone.utc).isoformat()


def _day(row: Mapping[str, object], name: str) -> str:
    text = _text(row, name)
    try:
        date.fromisoformat(text)
    except ValueError as exc:
        raise DataQualityError(f"{name.upper()}_INVALID") from exc
    return text


def _base(row: Mapping[str, object], *, time_fields: tuple[str, ...] = ()) -> dict:
    value = dict(row)
    for field in time_fields:
        value[field] = _instant(row, field)
    value["source"] = _text(row, "source")
    value["source_revision"] = _text(row, "source_revision")
    return value


def normalize_prices(raw: object) -> list[dict]:
    output = []
    for row in _rows(raw):
        value = _base(row, time_fields=("event_time_utc", "available_at"))
        value["exchange_local_date"] = _day(row, "exchange_local_date")
        for field in ("open", "high", "low", "close", "volume"):
            _decimal(row, field, positive=field != "volume")
        if Decimal(value["high"]) < max(Decimal(value["open"]), Decimal(value["close"])) or \
                Decimal(value["low"]) > min(Decimal(value["open"]), Decimal(value["close"])):
            raise DataQualityError("OHLC_CONFLICT")
        if Decimal(value["volume"]) < 0 or value.get("session") not in {"REGULAR"}:
            raise DataQualityError("PRICE_SESSION_OR_VOLUME_INVALID")
        if value.get("adjustment") not in {"raw", "split_adjusted", "total_return"}:
            raise DataQualityError("PRICE_ADJUSTMENT_UNKNOWN")
        _text(row, "currency")
        output.append(value)
    return output


def normalize_sessions(raw: object) -> list[dict]:
    output = []
    for row in _rows(raw):
        value = _base(row, time_fields=("regular_open_at", "regular_close_at",
                                       "event_time_utc", "received_at", "available_at"))
        value["exchange_local_date"] = _day(row, "exchange_local_date")
        if type(value.get("is_open")) is not bool:
            raise DataQualityError("IS_OPEN_MISSING_OR_INVALID")
        if value["is_open"] and value["regular_open_at"] >= value["regular_close_at"]:
            raise DataQualityError("SESSION_TIME_ORDER_INVALID")
        output.append(value)
    return output


def normalize_actions(raw: object) -> list[dict]:
    output = []
    for row in _rows(raw):
        value = _base(row, time_fields=("event_time_utc", "received_at", "available_at"))
        value["effective_date"] = _day(row, "effective_date")
        if value.get("action_type") not in {
            "DIVIDEND", "SPLIT", "REVERSE_SPLIT", "MERGER", "SPINOFF", "DELISTING", "TICKER_CHANGE"
        } or not isinstance(value.get("terms"), dict) or not value["terms"]:
            raise DataQualityError("CORPORATE_ACTION_INVALID")
        output.append(value)
    return output


def normalize_fx(raw: object) -> list[dict]:
    output = []
    for row in _rows(raw):
        value = _base(row, time_fields=("event_time", "available_at"))
        bid, ask, mid = (_decimal(row, field, positive=True) for field in ("bid", "ask", "mid"))
        if not Decimal(bid) <= Decimal(mid) <= Decimal(ask):
            raise DataQualityError("FX_QUOTE_CONFLICT")
        if value.get("provider_entity_id") != "USD/KRW" or value.get("quote_type") not in {
            "REPORTING", "EXECUTED_CONVERSION"
        }:
            raise DataQualityError("FX_PAIR_OR_QUOTE_TYPE_INVALID")
        output.append(value)
    return output


def normalize_rates(raw: object) -> list[dict]:
    output = []
    horizons = set()
    for row in _rows(raw):
        value = _base(row, time_fields=("event_time", "available_at"))
        horizon = _integer(row, "horizon_months")
        if horizon not in {1, 3, 6, 12} or horizon in horizons:
            raise DataQualityError("RISK_FREE_HORIZON_INVALID")
        horizons.add(horizon)
        _decimal(row, "rate")
        output.append(value)
    if horizons != {1, 3, 6, 12}:
        raise DataQualityError("RISK_FREE_CURVE_INCOMPLETE")
    return output


def normalize_macro(raw: object) -> list[dict]:
    output = []
    for row in _rows(raw):
        value = _base(row, time_fields=("scheduled_release_at", "official_release_at",
                                       "received_at", "available_at"))
        if value.get("provider_entity_id") not in MACRO_SERIES:
            raise DataQualityError("MACRO_SERIES_UNKNOWN")
        for field in ("actual", "prior_value", "revised_prior"):
            _decimal(row, field)
        _text(row, "reference_period")
        _text(row, "prior_vintage")
        if value["available_at"] < max(value["official_release_at"], value["received_at"]):
            raise DataQualityError("MACRO_AVAILABLE_AT_INVALID")
        output.append(value)
    if {row["provider_entity_id"] for row in output} != MACRO_SERIES:
        raise DataQualityError("MACRO_MINIMUM_SET_INCOMPLETE")
    return output


def normalize_consensus(raw: object) -> list[dict]:
    output = []
    for row in _rows(raw):
        value = _base(row, time_fields=("snapshot_at", "available_at"))
        if value.get("quality_status") != "KNOWN":
            raise DataQualityError("CONSENSUS_HISTORY_UNKNOWN")
        _decimal(row, "consensus")
        _text(row, "reference_period")
        if value["available_at"] < value["snapshot_at"]:
            raise DataQualityError("CONSENSUS_AVAILABLE_AT_INVALID")
        output.append(value)
    return output


def normalize_filings(raw: object) -> list[dict]:
    output = []
    last_order: dict[tuple[str, str], int] = {}
    for row in _rows(raw):
        value = _base(row, time_fields=("filed_at", "accepted_at", "received_at", "available_at"))
        form = value.get("form_type")
        if form not in FILING_ORDER:
            raise DataQualityError("FILING_FORM_UNKNOWN")
        value["period_end"] = _day(row, "period_end")
        if not value["filed_at"] <= value["accepted_at"] <= value["received_at"] <= value["available_at"]:
            raise DataQualityError("FILING_TIME_ORDER_INVALID")
        key = (_text(row, "provider_entity_id"), value["period_end"])
        if FILING_ORDER[form] < last_order.get(key, -1):
            raise DataQualityError("FILING_COLLECTION_ORDER_INVALID")
        last_order[key] = FILING_ORDER[form]
        output.append(value)
    return output


def normalize_financials(raw: object) -> list[dict]:
    output = []
    for row in _rows(raw):
        value = _base(row, time_fields=("filed_at", "accepted_at", "received_at", "available_at"))
        value["period_end"] = _day(row, "period_end")
        _text(row, "reference_period")
        for field in FINANCIAL_METRICS:
            _decimal(row, field)
        if Decimal(value["free_cash_flow"]) != Decimal(value["operating_cash_flow"]) - Decimal(value["capex"]):
            raise DataQualityError("FREE_CASH_FLOW_CONFLICT")
        if not value["filed_at"] <= value["accepted_at"] <= value["received_at"] <= value["available_at"]:
            raise DataQualityError("FINANCIAL_TIME_ORDER_INVALID")
        output.append(value)
    return output


def normalize_estimates(raw: object) -> list[dict]:
    output = []
    for row in _rows(raw):
        value = _base(row, time_fields=("snapshot_at", "available_at"))
        if value.get("quality_status") != "KNOWN":
            raise DataQualityError("ESTIMATE_HISTORY_UNKNOWN")
        _decimal(row, "consensus")
        if _integer(row, "analyst_count") <= 0:
            raise DataQualityError("ANALYST_COUNT_INVALID")
        _text(row, "metric")
        _text(row, "forecast_period")
        if value["available_at"] < value["snapshot_at"]:
            raise DataQualityError("ESTIMATE_AVAILABLE_AT_INVALID")
        output.append(value)
    return output


class Phase9Collector:
    """Normalize provider batches without access to the account database."""

    def __init__(self, adapter: ProviderDatasetAdapter):
        self.adapter = adapter

    @staticmethod
    def _require_dataset(batch: ProviderBatch, expected: str) -> None:
        if batch.dataset != expected:
            raise DataQualityError("DATASET_CONTRACT_MISMATCH")

    def collect(self, batch: ProviderBatch, *, schema: Mapping[str, str],
                normalize: Callable[[object], list[dict]], entity_mapping: Mapping[str, str],
                provider_entity_field: str = "provider_entity_id",
                canonical_entity_field: str = "entity_id",
                allow_verified_empty: bool = False) -> IngestionResult:
        def checked(raw: object) -> list[dict]:
            rows = normalize(raw)
            if any(row.get("source") != batch.source or
                   row.get("source_revision") != batch.source_revision for row in rows):
                raise DataQualityError("SOURCE_REVISION_CONFLICT")
            for row in rows:
                row_available = datetime.fromisoformat(_text(row, "available_at"))
                if row_available.tzinfo is None or row_available > batch.available_at:
                    raise DataQualityError("DATASET_AVAILABLE_BEFORE_ROW")
                if "received_at" in row:
                    row_received = datetime.fromisoformat(_text(row, "received_at"))
                    if row_received.tzinfo is None or row_received > batch.received_at:
                        raise DataQualityError("BATCH_RECEIVED_BEFORE_ROW")
            return rows

        return self.adapter.ingest(
            body=batch.body, request=batch.request, source=batch.source, dataset=batch.dataset,
            endpoint=batch.endpoint, http_method=batch.http_method,
            retrieved_at=batch.received_at, available_at=batch.available_at,
            provider_timestamp=batch.provider_timestamp, license_tag=batch.license_tag,
            code_revision=batch.code_revision, schema_version=batch.schema_version,
            raw_schema={"result": "array"}, schema=schema,
            instrument_mapping=entity_mapping, normalize=checked, status_code=batch.status_code,
            provider_entity_field=provider_entity_field,
            canonical_entity_field=canonical_entity_field,
            allow_verified_empty=allow_verified_empty,
        )

    def daily_prices(self, batch: ProviderBatch, instrument_mapping: Mapping[str, str]) -> IngestionResult:
        self._require_dataset(batch, "daily-prices")
        symbols = set(instrument_mapping)
        if not symbols or not symbols <= INITIAL_DAILY_ETFS:
            raise DataQualityError("INITIAL_ETF_UNIVERSE_INVALID")
        return self.collect(batch, schema=PRICE_FIELDS, normalize=normalize_prices,
                            entity_mapping=instrument_mapping,
                            provider_entity_field="provider_instrument_id",
                            canonical_entity_field="instrument_id")

    def trading_sessions(self, batch: ProviderBatch, exchange_mapping: Mapping[str, str]) -> IngestionResult:
        self._require_dataset(batch, "sessions")
        return self.collect(batch, schema=SESSION_FIELDS, normalize=normalize_sessions,
                            entity_mapping=exchange_mapping)

    def corporate_actions(self, batch: ProviderBatch, instrument_mapping: Mapping[str, str]) -> IngestionResult:
        self._require_dataset(batch, "actions")
        return self.collect(batch, schema=ACTION_FIELDS, normalize=normalize_actions,
                            entity_mapping=instrument_mapping,
                            canonical_entity_field="instrument_id", allow_verified_empty=True)

    def fx(self, batch: ProviderBatch, pair_mapping: Mapping[str, str]) -> IngestionResult:
        self._require_dataset(batch, "fx")
        return self.collect(batch, schema=FX_FIELDS, normalize=normalize_fx,
                            entity_mapping=pair_mapping, canonical_entity_field="currency_pair_id")

    def risk_free_curve(self, batch: ProviderBatch, curve_mapping: Mapping[str, str]) -> IngestionResult:
        self._require_dataset(batch, "risk-free")
        return self.collect(batch, schema=RATE_FIELDS, normalize=normalize_rates,
                            entity_mapping=curve_mapping, canonical_entity_field="curve_id")

    def macro(self, batch: ProviderBatch, series_mapping: Mapping[str, str]) -> IngestionResult:
        self._require_dataset(batch, "macro")
        return self.collect(batch, schema=MACRO_FIELDS, normalize=normalize_macro,
                            entity_mapping=series_mapping, canonical_entity_field="series_id")

    def consensus(self, batch: ProviderBatch, series_mapping: Mapping[str, str]) -> IngestionResult:
        self._require_dataset(batch, "consensus")
        return self.collect(batch, schema=CONSENSUS_FIELDS, normalize=normalize_consensus,
                            entity_mapping=series_mapping, canonical_entity_field="series_id")

    def filings(self, batch: ProviderBatch, company_mapping: Mapping[str, str]) -> IngestionResult:
        self._require_dataset(batch, "filings")
        return self.collect(batch, schema=FILING_FIELDS, normalize=normalize_filings,
                            entity_mapping=company_mapping, canonical_entity_field="company_id",
                            allow_verified_empty=True)

    def financials(self, batch: ProviderBatch, company_mapping: Mapping[str, str]) -> IngestionResult:
        self._require_dataset(batch, "financials")
        return self.collect(batch, schema=FINANCIAL_FIELDS, normalize=normalize_financials,
                            entity_mapping=company_mapping, canonical_entity_field="company_id")

    def analyst_estimates(self, batch: ProviderBatch, company_mapping: Mapping[str, str]) -> IngestionResult:
        self._require_dataset(batch, "estimates")
        return self.collect(batch, schema=ESTIMATE_FIELDS, normalize=normalize_estimates,
                            entity_mapping=company_mapping, canonical_entity_field="company_id")

    def attach_price_context(self, *, price_manifest_id: str, session_manifest_id: str,
                             action_manifest_id: str,
                             instrument_exchange: Mapping[str, str],
                             code_revision: str, combined_source: str | None = None,
                             combined_license_tag: str | None = None) -> StoredDatasetManifest:
        """Publish prices only after their session and corporate-action lineage is verified."""
        store = self.adapter.store
        price_manifest, prices = store.read(price_manifest_id)
        session_manifest, sessions = store.read(session_manifest_id)
        action_manifest, actions = store.read(action_manifest_id)
        manifests = (price_manifest, session_manifest, action_manifest)
        if any(item.layer != "silver" or item.quality_status != "VALID" for item in manifests):
            raise DataQualityError("PRICE_CONTEXT_PARENT_INVALID")
        mixed_contracts = len({item.source for item in manifests}) != 1 or \
            len({item.license_tag for item in manifests}) != 1
        if mixed_contracts and (not combined_source or not combined_license_tag):
            raise DataQualityError("PRICE_CONTEXT_COMBINED_CONTRACT_REQUIRED")
        open_sessions = {
            (row["entity_id"], row["exchange_local_date"])
            for row in sessions if row.get("is_open") is True
        }
        for row in prices:
            exchange = instrument_exchange.get(row["instrument_id"])
            if not exchange or (exchange, row["exchange_local_date"]) not in open_sessions:
                raise DataQualityError("PRICE_SESSION_MISSING_OR_CLOSED")
        action_instruments = {row["instrument_id"] for row in actions}
        if any(item not in instrument_exchange for item in action_instruments):
            raise DataQualityError("ACTION_INSTRUMENT_NOT_IN_PRICE_UNIVERSE")
        retrieved_at = max(datetime.fromisoformat(item.retrieved_at) for item in manifests)
        available_at = max(datetime.fromisoformat(item.available_at) for item in manifests)
        provider_timestamp = max(datetime.fromisoformat(item.provider_timestamp) for item in manifests)
        parents = tuple(item.manifest_id for item in manifests)
        return store.write(
            {
                "status": "VALID", "price_manifest_id": price_manifest_id,
                "session_manifest_id": session_manifest_id,
                "action_manifest_id": action_manifest_id,
                "price_row_count": len(prices), "action_row_count": len(actions),
            },
            layer="gold", source=combined_source or price_manifest.source,
            dataset="daily-prices-with-context",
            schema_version="phase9-price-context-v1", retrieved_at=retrieved_at,
            available_at=available_at, provider_timestamp=provider_timestamp,
            license_tag=combined_license_tag or price_manifest.license_tag, code_revision=code_revision,
            request_hash=digest(canonical({"parents": parents})), parent_manifest_ids=parents,
            allow_mixed_parent_contracts=mixed_contracts,
        )


class LatestSuccessfulDataset:
    def __init__(self, store: ImmutableDatasetStore):
        self.store = store

    def get(self, *, source: str, dataset: str, cutoff: datetime) -> StoredDatasetManifest:
        if cutoff.tzinfo is None or cutoff.utcoffset() is None:
            raise DataQualityError("CUTOFF_NOT_TIMEZONE_AWARE")
        cutoff = cutoff.astimezone(timezone.utc)
        manifests = []
        directory = self.store.layout.resolve("catalog", "manifests")
        if directory.exists():
            for path in directory.glob("*.json"):
                manifest, _ = self.store.read(path.stem)
                if (manifest.source, manifest.dataset, manifest.layer, manifest.quality_status) == (
                    source, dataset, "silver", "VALID"
                ) and datetime.fromisoformat(manifest.available_at) <= cutoff:
                    manifests.append(manifest)
        if not manifests:
            raise DataQualityError("SUCCESSFUL_DATASET_MISSING")
        latest_time = max(item.available_at for item in manifests)
        latest = [item for item in manifests if item.available_at == latest_time]
        if len({item.manifest_id for item in latest}) != 1:
            raise DataQualityError("SUCCESSFUL_DATASET_CONFLICT")
        return latest[0]


def surprise(*, actual: str, consensus_row: Mapping[str, object],
             official_release_at: datetime) -> Decimal:
    if consensus_row.get("quality_status") != "KNOWN":
        raise DataQualityError("CONSENSUS_HISTORY_UNKNOWN")
    snapshot = datetime.fromisoformat(_text(consensus_row, "snapshot_at"))
    available = datetime.fromisoformat(_text(consensus_row, "available_at"))
    if official_release_at.tzinfo is None or official_release_at.utcoffset() is None:
        raise DataQualityError("OFFICIAL_RELEASE_NOT_TIMEZONE_AWARE")
    release = official_release_at.astimezone(timezone.utc)
    if (snapshot.tzinfo is None or available.tzinfo is None or
            snapshot.utcoffset() != timezone.utc.utcoffset(None) or
            available.utcoffset() != timezone.utc.utcoffset(None) or
            snapshot >= release or available >= release):
        raise DataQualityError("CONSENSUS_NOT_PRE_RELEASE")
    try:
        actual_value = Decimal(actual)
        consensus = Decimal(_text(consensus_row, "consensus"))
    except InvalidOperation as exc:
        raise DataQualityError("SURPRISE_VALUE_INVALID") from exc
    if not actual_value.is_finite() or not consensus.is_finite():
        raise DataQualityError("SURPRISE_VALUE_INVALID")
    return actual_value - consensus


class CollectionPlan:
    def run(self, tasks: Mapping[CollectionStage, Callable[[], IngestionResult]]) -> tuple[IngestionResult, ...]:
        if set(tasks) != set(COLLECTION_ORDER):
            raise DataQualityError("COLLECTION_PLAN_INCOMPLETE")
        completed = []
        for stage in COLLECTION_ORDER:
            result = tasks[stage]()
            completed.append(result)
            if result.status != "READY":
                break
        return tuple(completed)

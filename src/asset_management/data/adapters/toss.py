"""Toss read-only client integration for the immutable dataset contract."""
from datetime import datetime
from typing import Callable, Mapping, Protocol

from asset_management.data.immutable import IngestionResult, ProviderDatasetAdapter, utc


class TossResult(Protocol):
    endpoint: str
    status_code: int
    body: object
    raw_response_id: str


class TossDatasetAdapter:
    """Collect a Toss result and pass it through the mandatory dataset boundary."""

    def __init__(self, client: object, datasets: ProviderDatasetAdapter):
        credentials = getattr(client, "credentials", None)
        required = tuple(str(getattr(credentials, name, "") or "")
                         for name in ("client_id", "client_secret", "account_seq"))
        required = tuple(value for value in required if value)
        if credentials is None or any(value not in datasets.store.secrets for value in required):
            raise ValueError("TOSS_CREDENTIALS_NOT_REGISTERED_FOR_REDACTION")
        self.client = client
        self.datasets = datasets

    def collect(self, *, operation: str, operation_args: tuple[object, ...] = (),
                operation_kwargs: Mapping[str, object] | None = None, dataset: str,
                retrieved_at: datetime, available_at: datetime,
                provider_timestamp: datetime, license_tag: str, code_revision: str,
                schema_version: str, raw_schema: Mapping[str, str],
                schema: Mapping[str, str], instrument_mapping: Mapping[str, str],
                normalize: Callable[[object], list[dict]]) -> IngestionResult:
        if not operation.startswith("get_") or operation.startswith("get_all_orders"):
            raise ValueError("INVALID_TOSS_DATASET_OPERATION")
        fetch = getattr(self.client, operation, None)
        if not callable(fetch):
            raise ValueError("UNKNOWN_TOSS_DATASET_OPERATION")
        result = fetch(*operation_args, **dict(operation_kwargs or {}))
        if not result.raw_response_id:
            self.datasets.store.catalog("source-health", {
                "source": "toss", "dataset": dataset,
                "retrieved_at": utc(retrieved_at), "status": "BLOCKED",
                "reason_code": "TOSS_RAW_EVIDENCE_MISSING",
                "bronze_manifest_id": None, "silver_manifest_id": None,
            })
            raise ValueError("TOSS_RAW_EVIDENCE_MISSING")
        return self.datasets.ingest(
            body=result.body,
            request={"upstream_raw_response_id": result.raw_response_id},
            source="toss", dataset=dataset, endpoint=result.endpoint,
            http_method="GET", retrieved_at=retrieved_at, available_at=available_at,
            provider_timestamp=provider_timestamp, license_tag=license_tag,
            code_revision=code_revision, schema_version=schema_version,
            raw_schema=raw_schema, schema=schema,
            instrument_mapping=instrument_mapping, normalize=normalize,
            status_code=result.status_code,
        )

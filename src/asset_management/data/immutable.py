"""Content-addressed, raw-first JSON dataset storage and provider boundary.

Catalog objects and blobs are published with atomic no-replace hard links.
Unreferenced blobs after a crash are safe; readers only consume verified manifests.
"""
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import hashlib
import inspect
import json
import os
from pathlib import Path
import re
import tempfile
from typing import Callable, Mapping

from asset_management.broker.redaction import SENSITIVE_KEYS
from asset_management.data.layout import DataLakeLayout


def canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, ensure_ascii=False,
                      separators=(",", ":"), allow_nan=False).encode("utf-8")


def digest(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def utc(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("UNKNOWN_TIMESTAMP")
    return value.astimezone(timezone.utc).isoformat()


def sanitize(value: object, secrets: tuple[str, ...]) -> object:
    blocked = {re.sub(r"[^a-z0-9]", "", k) for k in SENSITIVE_KEYS} | {
        "apikey", "xapikey", "password", "secret", "token", "cookie", "setcookie",
        "proxyauthorization", "credential", "credentials",
    }
    if isinstance(value, dict):
        if any(not isinstance(k, str) for k in value):
            raise ValueError("INVALID_JSON_KEY")
        return {str(sanitize(k, secrets)): "***REDACTED***"
                if re.sub(r"[^a-z0-9]", "", k.lower()) in blocked
                else sanitize(v, secrets) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [sanitize(v, secrets) for v in value]
    if isinstance(value, str):
        for secret in sorted(secrets, key=len, reverse=True):
            if secret:
                value = value.replace(secret, "***REDACTED***")
        return re.sub(r"(?i)Bearer\s+[^\s\"&]+", "Bearer ***REDACTED***", value)
    return value


@dataclass(frozen=True)
class StoredDatasetManifest:
    manifest_id: str
    source: str
    dataset: str
    schema_version: str
    retrieved_at: str
    available_at: str
    content_sha256: str
    row_count: int
    license_tag: str
    code_revision: str
    parent_manifest_ids: tuple[str, ...]
    quality_status: str
    layer: str
    provider_timestamp: str
    request_hash: str


class ImmutableDatasetStore:
    def __init__(self, root: Path, *, secrets: tuple[str, ...] = ()):
        self.layout = DataLakeLayout(root)
        self.secrets = secrets

    def _publish(self, path: Path, content: bytes) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(dir=path.parent)
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            try:
                os.link(temporary, path)
            except FileExistsError:
                if path.read_bytes() != content:
                    raise ValueError("IMMUTABLE_CONTENT_CONFLICT") from None
        finally:
            Path(temporary).unlink(missing_ok=True)

    def catalog(self, kind: str, value: object) -> str:
        content = canonical(sanitize(value, self.secrets))
        identifier = digest(content)
        self._publish(self.layout.resolve("catalog", f"{kind}/{identifier}.json"), content)
        return identifier

    def write(self, body: object, *, layer: str, source: str, dataset: str,
              schema_version: str, retrieved_at: datetime, available_at: datetime,
              provider_timestamp: datetime, license_tag: str, code_revision: str,
              request_hash: str, parent_manifest_ids: tuple[str, ...] = (),
              quality_status: str = "VALID") -> StoredDatasetManifest:
        if layer not in {"bronze", "silver", "gold"}:
            raise ValueError("INVALID_LAYER")
        if quality_status not in {"RAW", "VALID"} or (layer != "bronze" and quality_status != "VALID"):
            raise ValueError("INVALID_QUALITY")
        for value in (source, dataset, schema_version, license_tag, code_revision):
            if not value.strip() or sanitize(value, self.secrets) != value:
                raise ValueError("INVALID_METADATA")
        if not re.fullmatch(r"[0-9a-f]{64}", request_hash):
            raise ValueError("INVALID_REQUEST_HASH")
        received, available, provider = map(utc, (retrieved_at, available_at, provider_timestamp))
        if available_at < retrieved_at or provider_timestamp > retrieved_at:
            raise ValueError("INVALID_TIMESTAMP_ORDER")
        if (layer == "bronze") != (not parent_manifest_ids):
            raise ValueError("MISSING_OR_INVALID_LINEAGE")
        parents = tuple(sorted(set(parent_manifest_ids)))
        for parent_id in parents:
            parent, _ = self.read(parent_id)
            if {"bronze": 0, "silver": 1, "gold": 2}[parent.layer] >= {"bronze": 0, "silver": 1, "gold": 2}[layer]:
                raise ValueError("INVALID_LINEAGE_LAYER")
            if datetime.fromisoformat(parent.available_at) > available_at:
                raise ValueError("PARENT_NOT_AVAILABLE")
            if parent.source != source or parent.license_tag != license_tag:
                raise ValueError("CONFLICTING_SOURCE_OR_LICENSE")
        safe = sanitize(body, self.secrets)
        if layer != "bronze" and (safe is None or safe == [] or safe == {}):
            raise ValueError("EMPTY_DERIVED_DATASET")
        content = canonical(safe)
        content_hash = digest(content)
        self._publish(self.layout.resolve(layer, f"{content_hash}.json"), content)
        fields = dict(source=source, dataset=dataset, schema_version=schema_version,
                      retrieved_at=received, available_at=available, content_sha256=content_hash,
                      row_count=len(safe) if isinstance(safe, list) else 1,
                      license_tag=license_tag, code_revision=code_revision,
                      parent_manifest_ids=parents, quality_status=quality_status, layer=layer,
                      provider_timestamp=provider, request_hash=request_hash)
        identifier = digest(canonical(fields))
        manifest = StoredDatasetManifest(manifest_id=identifier, **fields)
        self._publish(self.layout.resolve("catalog", f"manifests/{identifier}.json"), canonical(asdict(manifest)))
        return manifest

    def read(self, identifier: str) -> tuple[StoredDatasetManifest, object]:
        if not re.fullmatch(r"[0-9a-f]{64}", identifier):
            raise ValueError("INVALID_MANIFEST_ID")
        fields = json.loads(self.layout.resolve("catalog", f"manifests/{identifier}.json").read_bytes())
        if fields.pop("manifest_id") != identifier or digest(canonical(fields)) != identifier:
            raise ValueError("MANIFEST_HASH_MISMATCH")
        fields["parent_manifest_ids"] = tuple(fields["parent_manifest_ids"])
        manifest = StoredDatasetManifest(manifest_id=identifier, **fields)
        content = self.layout.resolve(manifest.layer, f"{manifest.content_sha256}.json").read_bytes()
        if digest(content) != manifest.content_sha256:
            raise ValueError("CONTENT_HASH_MISMATCH")
        for parent in manifest.parent_manifest_ids:
            self.read(parent)
        return manifest, json.loads(content)


@dataclass(frozen=True)
class IngestionResult:
    status: str
    reason_code: str
    bronze_manifest_id: str | None
    silver_manifest_id: str | None


class ProviderDatasetAdapter:
    """Shared boundary; transport supplies decoded JSON and all credential values.

    normalize maps provider JSON to nonempty rows. schema maps required field names
    to JSON scalar types; schema_version is bound to its immutable definition hash.
    code_revision must identify the complete normalizer code and dependencies.
    """
    def __init__(self, store: ImmutableDatasetStore):
        self.store = store

    def ingest(self, *, body: object, request: object, source: str, dataset: str,
               retrieved_at: datetime, available_at: datetime, provider_timestamp: datetime,
               license_tag: str, code_revision: str, schema_version: str,
               schema: Mapping[str, str], instrument_mapping: Mapping[str, str],
               normalize: Callable[[object], list[dict]], status_code: int = 200) -> IngestionResult:
        bronze = None
        silver = None
        reason = "RAW_STORAGE_FAILED"
        try:
            metadata = dict(source=source, dataset=dataset, retrieved_at=retrieved_at,
                            available_at=available_at, provider_timestamp=provider_timestamp,
                            license_tag=license_tag, code_revision=code_revision,
                            request_hash=digest(canonical(sanitize(
                                dict(source=source, dataset=dataset, request=request), self.store.secrets))))
            bronze = self.store.write(body, layer="bronze", schema_version="provider-json-v1",
                                      quality_status="RAW", **metadata)
            reason = "PROVIDER_HTTP_ERROR"
            if not 200 <= status_code < 300:
                raise ValueError(reason)
            reason = "SCHEMA_VALIDATION_FAILED"
            types = {"string": str, "integer": int, "number": float, "boolean": bool}
            if not schema or any(t not in types for t in schema.values()):
                raise ValueError(reason)
            schema_hash = self.store.catalog("schemas", dict(schema))
            mapping_hash = self.store.catalog("instrument-mappings", dict(instrument_mapping))
            reason = "NORMALIZATION_FAILED"
            normalizer_hash = digest(inspect.getsource(normalize).encode("utf-8"))
            _, raw = self.store.read(bronze.manifest_id)
            rows = normalize(raw)
            reason = "SCHEMA_VALIDATION_FAILED"
            if not isinstance(rows, list) or not rows:
                raise ValueError(reason)
            for row in rows:
                if not isinstance(row, dict) or any(k not in row or type(row[k]) is not types[t] for k, t in schema.items()):
                    raise ValueError(reason)
                reason = "UNKNOWN_INSTRUMENT"
                symbol = row.get("provider_instrument_id")
                if not isinstance(symbol, str) or not instrument_mapping.get(symbol, "").strip():
                    raise ValueError(reason)
                row["instrument_id"] = instrument_mapping[symbol]
                reason = "SCHEMA_VALIDATION_FAILED"
            metadata["code_revision"] = f"{code_revision}:normalizer:{normalizer_hash}:mapping:{mapping_hash}"
            reason = "NORMALIZED_STORAGE_FAILED"
            silver = self.store.write(rows, layer="silver", schema_version=f"{schema_version}:{schema_hash}",
                                      parent_manifest_ids=(bronze.manifest_id,), **metadata)
            reason = "OK"
        except Exception:
            # Never persist provider/normalizer exception text: it can contain credentials.
            pass
        health_time = utc(retrieved_at) if retrieved_at.tzinfo is not None and retrieved_at.utcoffset() is not None else None
        self.store.catalog("source-health", dict(source=source, dataset=dataset,
                           retrieved_at=health_time, status="OK" if reason == "OK" else "BLOCKED",
                           reason_code=reason, bronze_manifest_id=bronze.manifest_id if bronze else None,
                           silver_manifest_id=silver.manifest_id if silver else None))
        return IngestionResult("READY" if reason == "OK" else "NO_TRADE", reason,
                               bronze.manifest_id if bronze else None,
                               silver.manifest_id if silver else None)

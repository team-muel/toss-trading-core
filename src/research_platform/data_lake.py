from __future__ import annotations

import hashlib
import json
import os
import tempfile
import uuid
from dataclasses import asdict, dataclass
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Iterable


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _canonical_json(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


_SENSITIVE_REQUEST_KEY_PARTS = (
    "authorization",
    "api_key",
    "apikey",
    "access_key",
    "client_secret",
    "password",
    "secret",
    "token",
)


def _sanitize_request_metadata(value: Any) -> Any:
    if isinstance(value, dict):
        sanitized = {}
        for key, item in value.items():
            normalized = str(key).strip().lower()
            if any(part in normalized for part in _SENSITIVE_REQUEST_KEY_PARTS):
                sanitized[key] = "[REDACTED]"
            else:
                sanitized[key] = _sanitize_request_metadata(item)
        return sanitized
    if isinstance(value, list):
        return [_sanitize_request_metadata(item) for item in value]
    if isinstance(value, tuple):
        return tuple(_sanitize_request_metadata(item) for item in value)
    return value


def _timestamp(value: str, *, field: str) -> datetime:
    text = str(value).strip()
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"invalid {field} timestamp: {value!r}") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{field} timestamp must include a timezone: {value!r}")
    return parsed.astimezone(timezone.utc)


def _code_revision(value: str) -> str:
    revision = value.strip()
    if not revision or revision.lower() == "unknown":
        raise ValueError("an immutable code revision is required")
    return revision


def _safe_partition(value: str) -> str:
    text = value.strip()
    if not text or any(char not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-._" for char in text):
        raise ValueError(f"unsafe data-lake partition value: {value!r}")
    return text


def _decimal(value: str | int | float | Decimal) -> Decimal:
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"invalid decimal value: {value!r}") from exc
    if not result.is_finite():
        raise ValueError(f"non-finite decimal value: {value!r}")
    return result


@dataclass(frozen=True)
class DatasetManifest:
    manifest_id: str
    layer: str
    source: str
    dataset: str
    schema_version: str
    retrieved_at: str
    available_at: str
    content_sha256: str
    byte_count: int
    media_type: str
    relative_path: str
    request_sha256: str | None
    license_tag: str
    code_revision: str
    parent_manifest_ids: tuple[str, ...] = ()
    request_metadata: dict[str, Any] | None = None


@dataclass(frozen=True)
class MarketBar:
    symbol: str
    event_time_utc: str
    available_at: str
    exchange_local_date: str
    interval: str
    open: str
    high: str
    low: str
    close: str
    volume: str
    currency: str
    session: str
    adjustment: str
    source: str
    source_revision: str
    raw_manifest_id: str
    quality_flag: str = "ok"


def validate_market_bars(rows: Iterable[MarketBar]) -> list[MarketBar]:
    validated = list(rows)
    if not validated:
        raise ValueError("market bar dataset is empty")
    seen: set[tuple[str, str, str, str, str]] = set()
    last_time: dict[tuple[str, str, str, str], datetime] = {}
    for row in validated:
        if not row.symbol.strip() or not row.source.strip():
            raise ValueError("market bars require non-empty symbol and source")
        if not row.currency.strip() or not row.raw_manifest_id.strip():
            raise ValueError(
                "market bars require non-empty currency and raw_manifest_id"
            )
        try:
            date.fromisoformat(row.exchange_local_date)
        except ValueError as exc:
            raise ValueError(
                f"invalid exchange_local_date: {row.exchange_local_date!r}"
            ) from exc
        if row.interval not in {"1d", "1h", "1m"}:
            raise ValueError(f"unsupported interval: {row.interval}")
        if row.adjustment not in {"raw", "split_adjusted", "total_return"}:
            raise ValueError(f"unsupported adjustment: {row.adjustment}")
        if row.quality_flag not in {"ok", "estimated", "stale", "blocked"}:
            raise ValueError(f"unsupported quality flag: {row.quality_flag}")
        key = (
            row.symbol,
            row.event_time_utc,
            row.interval,
            row.source,
            row.adjustment,
        )
        if key in seen:
            raise ValueError(f"duplicate market bar: {key}")
        seen.add(key)
        open_px = _decimal(row.open)
        high_px = _decimal(row.high)
        low_px = _decimal(row.low)
        close_px = _decimal(row.close)
        volume = _decimal(row.volume)
        if min(open_px, high_px, low_px, close_px) <= 0:
            raise ValueError(f"nonpositive OHLC value: {key}")
        if high_px < max(open_px, low_px, close_px):
            raise ValueError(f"high price violates OHLC bounds: {key}")
        if low_px > min(open_px, high_px, close_px):
            raise ValueError(f"low price violates OHLC bounds: {key}")
        if volume < 0:
            raise ValueError(f"negative volume: {key}")
        event_time = _timestamp(row.event_time_utc, field="event_time_utc")
        available_at = _timestamp(row.available_at, field="available_at")
        if available_at < event_time:
            raise ValueError(f"available_at precedes event time: {key}")
        sequence_key = (row.symbol, row.interval, row.adjustment, row.source)
        previous = last_time.get(sequence_key)
        if previous is not None and event_time < previous:
            raise ValueError(f"market bars are not sorted: {sequence_key}")
        last_time[sequence_key] = event_time
    return validated


class DataLake:
    """Immutable raw objects, normalized Parquet, and per-object manifests."""

    def __init__(self, root: str | Path = "research_data") -> None:
        self.root = Path(root)

    def _atomic_write(self, path: Path, payload: bytes, *, immutable: bool = True) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            if immutable and path.read_bytes() != payload:
                raise FileExistsError(f"immutable data-lake object conflict: {path}")
            return
        handle = tempfile.NamedTemporaryFile(
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        )
        temporary = Path(handle.name)
        try:
            with handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)

    def _write_manifest(self, manifest: DatasetManifest) -> Path:
        path = (
            self.root
            / "catalog"
            / "manifests"
            / f"{manifest.manifest_id}.json"
        )
        self._atomic_write(path, _canonical_json(asdict(manifest)))
        return path

    def _read_manifest(self, manifest_id: str) -> DatasetManifest | None:
        path = self.root / "catalog" / "manifests" / f"{manifest_id}.json"
        if not path.exists():
            return None
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload["parent_manifest_ids"] = tuple(payload.get("parent_manifest_ids", ()))
        return DatasetManifest(**payload)

    def store_raw(
        self,
        *,
        source: str,
        dataset: str,
        body: bytes | str | dict[str, Any] | list[Any],
        media_type: str,
        schema_version: str,
        available_at: str,
        request: dict[str, Any] | None = None,
        license_tag: str,
        code_revision: str,
        retrieved_at: str | None = None,
    ) -> DatasetManifest:
        code_revision = _code_revision(code_revision)
        retrieved = retrieved_at or _utc_now()
        payload = (
            body
            if isinstance(body, bytes)
            else body.encode("utf-8")
            if isinstance(body, str)
            else _canonical_json(body)
        )
        digest = hashlib.sha256(payload).hexdigest()
        sanitized_request = (
            _sanitize_request_metadata(request) if request is not None else None
        )
        request_digest = (
            hashlib.sha256(_canonical_json(sanitized_request)).hexdigest()
            if sanitized_request is not None
            else None
        )
        extension = "json" if "json" in media_type else "csv" if "csv" in media_type else "bin"
        ingest_date = retrieved[:10]
        relative = (
            Path("bronze")
            / f"source={_safe_partition(source)}"
            / f"dataset={_safe_partition(dataset)}"
            / f"ingest_date={_safe_partition(ingest_date)}"
            / f"{digest}.{extension}"
        )
        identity = ":".join(
            (
                "raw",
                source,
                dataset,
                digest,
                request_digest or "no-request",
                schema_version,
                available_at,
                media_type,
                license_tag,
                code_revision,
            )
        )
        manifest_id = str(uuid.uuid5(uuid.NAMESPACE_URL, identity))
        existing = self._read_manifest(manifest_id)
        if existing is not None:
            if (
                existing.content_sha256 == digest
                and existing.request_sha256 == request_digest
                and existing.schema_version == schema_version
                and existing.available_at == available_at
                and existing.media_type == media_type
                and existing.license_tag == license_tag
                and existing.code_revision == code_revision
            ):
                return existing
            raise FileExistsError(
                f"immutable raw manifest conflict: {manifest_id}"
            )
        self._atomic_write(self.root / relative, payload)
        manifest = DatasetManifest(
            manifest_id=manifest_id,
            layer="bronze",
            source=source,
            dataset=dataset,
            schema_version=schema_version,
            retrieved_at=retrieved,
            available_at=available_at,
            content_sha256=digest,
            byte_count=len(payload),
            media_type=media_type,
            relative_path=relative.as_posix(),
            request_sha256=request_digest,
            license_tag=license_tag,
            code_revision=code_revision,
            request_metadata=sanitized_request,
        )
        self._write_manifest(manifest)
        return manifest

    def write_market_bars(
        self,
        rows: Iterable[MarketBar],
        *,
        code_revision: str,
        license_tag: str,
    ) -> list[DatasetManifest]:
        code_revision = _code_revision(code_revision)
        try:
            import duckdb
        except ImportError as exc:
            raise RuntimeError(
                "DuckDB is required for Parquet output; install toss-trading[research]"
            ) from exc

        validated = validate_market_bars(rows)
        groups: dict[tuple[str, str, str, str], list[MarketBar]] = {}
        for row in validated:
            year = row.exchange_local_date[:4]
            key = (row.source, row.interval, row.adjustment, year)
            groups.setdefault(key, []).append(row)

        manifests: list[DatasetManifest] = []
        for (source, interval, adjustment, year), group in sorted(groups.items()):
            group_payload = _canonical_json([asdict(item) for item in group])
            content_digest = hashlib.sha256(group_payload).hexdigest()
            relative = (
                Path("silver")
                / "market_bars"
                / f"source={_safe_partition(source)}"
                / f"interval={_safe_partition(interval)}"
                / f"adjustment={_safe_partition(adjustment)}"
                / f"year={_safe_partition(year)}"
                / f"part-{content_digest}.parquet"
            )
            output = self.root / relative
            output.parent.mkdir(parents=True, exist_ok=True)
            if not output.exists():
                temporary = output.with_name(f".{output.name}.{uuid.uuid4().hex}.tmp")
                json_handle = tempfile.NamedTemporaryFile(
                    dir=output.parent,
                    prefix=".rows-",
                    suffix=".json",
                    delete=False,
                )
                json_input = Path(json_handle.name)
                with json_handle:
                    json_handle.write(group_payload)
                connection = duckdb.connect()
                try:
                    escaped_input = str(json_input).replace("'", "''")
                    connection.execute(
                        f"""
                        CREATE TABLE bars AS
                        SELECT
                          CAST(symbol AS VARCHAR) AS symbol,
                          CAST(event_time_utc AS TIMESTAMPTZ) AS event_time_utc,
                          CAST(available_at AS TIMESTAMPTZ) AS available_at,
                          CAST(exchange_local_date AS DATE) AS exchange_local_date,
                          CAST(interval AS VARCHAR) AS interval,
                          CAST(open AS DECIMAL(38, 12)) AS open,
                          CAST(high AS DECIMAL(38, 12)) AS high,
                          CAST(low AS DECIMAL(38, 12)) AS low,
                          CAST(close AS DECIMAL(38, 12)) AS close,
                          CAST(volume AS DECIMAL(38, 12)) AS volume,
                          CAST(currency AS VARCHAR) AS currency,
                          CAST(session AS VARCHAR) AS session,
                          CAST(adjustment AS VARCHAR) AS adjustment,
                          CAST(source AS VARCHAR) AS source,
                          CAST(source_revision AS VARCHAR) AS source_revision,
                          CAST(raw_manifest_id AS VARCHAR) AS raw_manifest_id,
                          CAST(quality_flag AS VARCHAR) AS quality_flag
                        FROM read_json_auto('{escaped_input}', format='array')
                        """
                    )
                    escaped = str(temporary).replace("'", "''")
                    connection.execute(
                        f"COPY bars TO '{escaped}' (FORMAT PARQUET, COMPRESSION ZSTD)"
                    )
                except Exception:
                    temporary.unlink(missing_ok=True)
                    raise
                finally:
                    connection.close()
                    json_input.unlink(missing_ok=True)
                os.replace(temporary, output)
            parquet_digest = hashlib.sha256(output.read_bytes()).hexdigest()
            legacy_manifest_id = str(
                uuid.uuid5(uuid.NAMESPACE_URL, f"silver:{parquet_digest}")
            )
            manifest_id = str(
                uuid.uuid5(
                    uuid.NAMESPACE_URL,
                    f"silver:{parquet_digest}:{code_revision}:{license_tag}",
                )
            )
            existing = self._read_manifest(manifest_id) or self._read_manifest(
                legacy_manifest_id
            )
            if (
                existing is not None
                and existing.content_sha256 == parquet_digest
                and existing.relative_path == relative.as_posix()
                and existing.code_revision == code_revision
                and existing.license_tag == license_tag
            ):
                manifests.append(existing)
                continue
            manifest = DatasetManifest(
                manifest_id=manifest_id,
                layer="silver",
                source=source,
                dataset="market_bars",
                schema_version="market-bars-v1",
                retrieved_at=_utc_now(),
                available_at=max(item.available_at for item in group),
                content_sha256=parquet_digest,
                byte_count=output.stat().st_size,
                media_type="application/vnd.apache.parquet",
                relative_path=relative.as_posix(),
                request_sha256=None,
                license_tag=license_tag,
                code_revision=code_revision,
                parent_manifest_ids=tuple(
                    sorted({item.raw_manifest_id for item in group})
                ),
            )
            self._write_manifest(manifest)
            manifests.append(manifest)
        return manifests

    def manifests(self, *, layer: str | None = None) -> list[DatasetManifest]:
        results = []
        root = self.root / "catalog" / "manifests"
        if not root.exists():
            return results
        for path in sorted(root.glob("*.json")):
            payload = json.loads(path.read_text(encoding="utf-8"))
            payload["parent_manifest_ids"] = tuple(payload.get("parent_manifest_ids", ()))
            manifest = DatasetManifest(**payload)
            if layer is None or manifest.layer == layer:
                results.append(manifest)
        return results

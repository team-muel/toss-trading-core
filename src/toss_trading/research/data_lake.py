from __future__ import annotations

import hashlib
import json
import os
import tempfile
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
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
    last_time: dict[tuple[str, str, str], str] = {}
    for row in validated:
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
        if row.available_at < row.event_time_utc:
            raise ValueError(f"available_at precedes event time: {key}")
        sequence_key = (row.symbol, row.interval, row.adjustment)
        previous = last_time.get(sequence_key)
        if previous is not None and row.event_time_utc < previous:
            raise ValueError(f"market bars are not sorted: {sequence_key}")
        last_time[sequence_key] = row.event_time_utc
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
        retrieved = retrieved_at or _utc_now()
        payload = (
            body
            if isinstance(body, bytes)
            else body.encode("utf-8")
            if isinstance(body, str)
            else _canonical_json(body)
        )
        digest = hashlib.sha256(payload).hexdigest()
        request_digest = (
            hashlib.sha256(_canonical_json(request)).hexdigest()
            if request is not None
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
        self._atomic_write(self.root / relative, payload)
        identity = ":".join(
            (
                "raw",
                source,
                dataset,
                digest,
                request_digest or "no-request",
                retrieved,
            )
        )
        manifest = DatasetManifest(
            manifest_id=str(uuid.uuid5(uuid.NAMESPACE_URL, identity)),
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
            content_digest = hashlib.sha256(
                _canonical_json([asdict(item) for item in group])
            ).hexdigest()
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
                connection = duckdb.connect()
                try:
                    connection.execute(
                        """
                        CREATE TABLE bars (
                          symbol VARCHAR,
                          event_time_utc TIMESTAMPTZ,
                          available_at TIMESTAMPTZ,
                          exchange_local_date DATE,
                          interval VARCHAR,
                          open DECIMAL(38, 12),
                          high DECIMAL(38, 12),
                          low DECIMAL(38, 12),
                          close DECIMAL(38, 12),
                          volume DECIMAL(38, 12),
                          currency VARCHAR,
                          session VARCHAR,
                          adjustment VARCHAR,
                          source VARCHAR,
                          source_revision VARCHAR,
                          raw_manifest_id VARCHAR,
                          quality_flag VARCHAR
                        )
                        """
                    )
                    connection.executemany(
                        "INSERT INTO bars VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        [
                            (
                                item.symbol,
                                item.event_time_utc,
                                item.available_at,
                                item.exchange_local_date,
                                item.interval,
                                item.open,
                                item.high,
                                item.low,
                                item.close,
                                item.volume,
                                item.currency,
                                item.session,
                                item.adjustment,
                                item.source,
                                item.source_revision,
                                item.raw_manifest_id,
                                item.quality_flag,
                            )
                            for item in group
                        ],
                    )
                    escaped = str(temporary).replace("'", "''")
                    connection.execute(
                        f"COPY bars TO '{escaped}' (FORMAT PARQUET, COMPRESSION ZSTD)"
                    )
                finally:
                    connection.close()
                os.replace(temporary, output)
            parquet_digest = hashlib.sha256(output.read_bytes()).hexdigest()
            manifest = DatasetManifest(
                manifest_id=str(
                    uuid.uuid5(uuid.NAMESPACE_URL, f"silver:{parquet_digest}")
                ),
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

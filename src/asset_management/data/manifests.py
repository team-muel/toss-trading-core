"""Immutable dataset manifests connecting bronze, silver, and gold artifacts."""

from dataclasses import dataclass
from datetime import datetime, timezone


@dataclass(frozen=True, slots=True)
class DatasetManifest:
    manifest_id: str
    ingestion_run_id: str
    layer: str
    dataset_name: str
    uri: str
    content_hash: str
    observed_at_utc: datetime
    received_at_utc: datetime
    parent_manifest_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.layer not in {"bronze", "silver", "gold"}:
            raise ValueError("dataset layer must be bronze, silver, or gold")
        for name in ("observed_at_utc", "received_at_utc"):
            value = getattr(self, name)
            if value.tzinfo is None or value.utcoffset() != timezone.utc.utcoffset(None):
                raise ValueError(f"{name} must be timezone-aware UTC")
        if self.observed_at_utc > self.received_at_utc:
            raise ValueError("observed time cannot be after received time")
        if not self.content_hash or not self.uri:
            raise ValueError("manifest requires uri and content hash")

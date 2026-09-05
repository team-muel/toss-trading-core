from __future__ import annotations

from datetime import datetime, timezone
import hashlib
from pathlib import Path
from typing import Any

import yaml

from .schemas import PolicyBundle, PolicyDescriptor, PolicyRecord, PolicyRegistry
from .versions import content_hash


def _record(kind: str, raw: dict[str, Any]) -> PolicyRecord:
    required = {"version", "effective_from", "approved_by", "approval_reason", "values"}
    missing = required - raw.keys()
    if missing:
        raise ValueError(f"{kind} policy missing fields: {sorted(missing)}")
    start = datetime.fromisoformat(str(raw["effective_from"]).replace("Z", "+00:00"))
    end_raw = raw.get("effective_to")
    end = datetime.fromisoformat(str(end_raw).replace("Z", "+00:00")) if end_raw else None
    if start.utcoffset() != timezone.utc.utcoffset(None) or (end and end.utcoffset() != timezone.utc.utcoffset(None)):
        raise ValueError("policy effective times must be UTC")
    values = raw["values"]
    if not isinstance(values, dict):
        raise ValueError(f"{kind}.values must be an object")
    return PolicyRecord(kind, str(raw["version"]), start, end, str(raw["approved_by"]), str(raw["approval_reason"]), values, content_hash(values))


def load_policy_bundle(path: str | Path) -> PolicyBundle:
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError("policy bundle must be an object")
    return PolicyBundle(**{kind: _record(kind, raw[kind]) for kind in ("investment", "risk", "execution", "tax")})


def _optional_utc(value: object, field: str) -> datetime | None:
    if value is None:
        return None
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.utcoffset() != timezone.utc.utcoffset(None):
        raise ValueError(f"{field} must be UTC")
    return parsed


def load_policy_registry(path: str | Path, *, repository_root: str | Path | None = None) -> PolicyRegistry:
    registry_path = Path(path)
    raw = yaml.safe_load(registry_path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict) or not isinstance(raw.get("policies"), dict):
        raise ValueError("policy registry must contain a policies object")
    if raw.get("live_trading_enabled") is not False:
        raise ValueError("policy registry must keep live trading disabled")
    root = Path(repository_root) if repository_root else registry_path.resolve().parent.parent
    policies: dict[str, PolicyDescriptor] = {}
    for kind, item in raw["policies"].items():
        if not isinstance(item, dict):
            raise ValueError(f"policy descriptor must be an object: {kind}")
        document = str(item.get("document", ""))
        document_path = root / document
        if not document_path.is_file():
            raise ValueError(f"policy document does not exist: {document}")
        actual_hash = hashlib.sha256(document_path.read_bytes()).hexdigest()
        if actual_hash != item.get("document_hash"):
            raise ValueError(f"policy document hash mismatch: {kind}")
        start = _optional_utc(item.get("effective_from"), f"{kind}.effective_from")
        end = _optional_utc(item.get("effective_to"), f"{kind}.effective_to")
        if start and end and end <= start:
            raise ValueError(f"policy effective interval is invalid: {kind}")
        status = str(item.get("status", ""))
        approved_by = item.get("approved_by")
        if status == "ACCEPTED" and (start is None or not approved_by):
            raise ValueError(f"accepted policy requires approval and effective_from: {kind}")
        if status == "DRAFT" and (start is not None or approved_by is not None):
            raise ValueError(f"draft policy cannot be effective or approved: {kind}")
        policies[str(kind)] = PolicyDescriptor(
            str(kind), str(item.get("version", "")), status, document, actual_hash,
            start, end, str(approved_by) if approved_by else None,
        )
    versions = [item.version for item in policies.values()]
    if any(not version for version in versions) or len(versions) != len(set(versions)):
        raise ValueError("policy versions must be non-empty and unique")
    return PolicyRegistry(str(raw.get("registry_version", "")), False, policies)

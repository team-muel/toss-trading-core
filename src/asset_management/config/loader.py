from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from .schemas import PolicyBundle, PolicyRecord
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

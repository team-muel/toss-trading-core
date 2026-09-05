"""Append-only persistent registry for data-quality issues."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

from .models import QualityIssue


class QualityIssueRegistry:
    def __init__(self, path: Path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)

    def record(self, *, dataset: str, manifest_id: str, issue: QualityIssue,
               recorded_at: datetime | None = None) -> str:
        at = recorded_at or datetime.now(timezone.utc)
        if at.tzinfo is None or at.utcoffset() is None:
            raise ValueError("ISSUE_TIME_NOT_AWARE")
        payload = {"dataset": dataset, "manifest_id": manifest_id, "issue": asdict(issue),
                   "recorded_at": at.astimezone(timezone.utc).isoformat(), "lifecycle": "OPEN"}
        canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
        issue_id = hashlib.sha256(canonical.encode()).hexdigest()
        event = {"issue_id": issue_id, **payload}
        existing = {item["issue_id"] for item in self.read_all()}
        if issue_id not in existing:
            with self.path.open("a", encoding="utf-8", newline="\n") as stream:
                stream.write(json.dumps(event, sort_keys=True, default=str) + "\n")
        return issue_id

    def read_all(self) -> tuple[dict, ...]:
        if not self.path.exists():
            return ()
        return tuple(json.loads(line) for line in self.path.read_text(encoding="utf-8").splitlines() if line)

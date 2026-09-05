"""Broker-grounded settlement-date evidence for execution ledger postings."""

from dataclasses import dataclass
from datetime import date
from hashlib import sha256
import json
import re
import sqlite3
from typing import Any

from asset_management.data.raw_store import SQLiteRawResponseStore
from asset_management.domain.errors import ReconciliationError


_SETTLEMENT_KEYS = {"settlementdate"}


@dataclass(frozen=True, slots=True)
class SettlementEvidence:
    execution_delta_id: str
    execution_snapshot_id: str
    source_response_id: str
    settlement_date: date
    extraction_paths: tuple[str, ...]
    response_hash: str

    @property
    def evidence_hash(self) -> str:
        payload = {
            "execution_delta_id": self.execution_delta_id,
            "execution_snapshot_id": self.execution_snapshot_id,
            "source_response_id": self.source_response_id,
            "settlement_date": self.settlement_date.isoformat(),
            "extraction_paths": list(self.extraction_paths),
            "response_hash": self.response_hash,
        }
        return sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()


class SettlementEvidenceRepository:
    """Resolves settlement truth from the exact raw fill snapshot and persists it."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def resolve(self, execution_delta_id: str, claimed_date: date) -> SettlementEvidence:
        if not isinstance(claimed_date, date):
            raise ReconciliationError("execution settlement date is required")
        lineage = self._conn.execute(
            """SELECT d.to_snapshot_id, s.source_response_id, o.account_id
               FROM am_execution_delta d
               JOIN am_execution_snapshot s ON s.execution_snapshot_id=d.to_snapshot_id
               JOIN am_broker_order o ON o.broker_order_id=d.broker_order_id
               WHERE d.execution_delta_id=?""",
            (execution_delta_id,),
        ).fetchone()
        if lineage is None:
            raise ReconciliationError("settlement evidence execution lineage is missing")
        snapshot_id, source_response_id, account_id = map(str, lineage)
        try:
            raw = SQLiteRawResponseStore(self._conn).verified(source_response_id)
        except (KeyError, ValueError) as error:
            raise ReconciliationError("settlement raw evidence verification failed") from error
        if (
            raw.source.strip().lower() != "toss"
            or raw.account_id != account_id
            or not 200 <= raw.status_code < 300
        ):
            raise ReconciliationError("settlement evidence is not an authoritative Toss response")
        candidates, malformed = _settlement_candidates(raw.body)
        if malformed:
            raise ReconciliationError("settlement date evidence is malformed")
        distinct = {value for _, value in candidates}
        if not distinct:
            raise ReconciliationError("settlement date evidence is missing")
        if len(distinct) != 1:
            raise ReconciliationError("settlement date evidence conflicts within raw response")
        broker_date = next(iter(distinct))
        if broker_date != claimed_date:
            raise ReconciliationError("settlement evidence conflicts with claimed posting date")
        return SettlementEvidence(
            execution_delta_id=execution_delta_id,
            execution_snapshot_id=snapshot_id,
            source_response_id=source_response_id,
            settlement_date=broker_date,
            extraction_paths=tuple(sorted(path for path, _ in candidates)),
            response_hash=raw.response_hash,
        )

    def persist(self, evidence: SettlementEvidence) -> None:
        self._conn.execute(
            """INSERT INTO am_execution_settlement_evidence
               (execution_delta_id, execution_snapshot_id, source_response_id,
                settlement_date, extraction_paths_json, response_hash, evidence_hash)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                evidence.execution_delta_id,
                evidence.execution_snapshot_id,
                evidence.source_response_id,
                evidence.settlement_date.isoformat(),
                json.dumps(evidence.extraction_paths, separators=(",", ":")),
                evidence.response_hash,
                evidence.evidence_hash,
            ),
        )

    def require(self, execution_delta_id: str, claimed_date: date) -> SettlementEvidence:
        resolved = self.resolve(execution_delta_id, claimed_date)
        row = self._conn.execute(
            """SELECT execution_snapshot_id, source_response_id, settlement_date,
                      extraction_paths_json, response_hash, evidence_hash
               FROM am_execution_settlement_evidence WHERE execution_delta_id=?""",
            (execution_delta_id,),
        ).fetchone()
        if row is None:
            raise ReconciliationError("execution settlement evidence is missing")
        try:
            stored_paths = tuple(json.loads(str(row[3])))
        except (TypeError, ValueError) as error:
            raise ReconciliationError("execution settlement evidence paths are invalid") from error
        stored = (
            str(row[0]), str(row[1]), str(row[2]), stored_paths,
            str(row[4]), str(row[5]),
        )
        expected = (
            resolved.execution_snapshot_id,
            resolved.source_response_id,
            resolved.settlement_date.isoformat(),
            resolved.extraction_paths,
            resolved.response_hash,
            resolved.evidence_hash,
        )
        if stored != expected:
            raise ReconciliationError("execution settlement evidence hash or lineage mismatch")
        return resolved


def _settlement_candidates(value: Any) -> tuple[list[tuple[str, date]], bool]:
    candidates: list[tuple[str, date]] = []
    malformed = False

    def visit(node: Any, path: str) -> None:
        nonlocal malformed
        if isinstance(node, dict):
            for raw_key, child in node.items():
                key = str(raw_key)
                child_path = f"{path}.{key}"
                normalized = re.sub(r"[^a-z0-9]", "", key.lower())
                if normalized in _SETTLEMENT_KEYS:
                    if not isinstance(child, str):
                        malformed = True
                    else:
                        try:
                            parsed = date.fromisoformat(child)
                        except ValueError:
                            malformed = True
                        else:
                            if parsed.isoformat() != child:
                                malformed = True
                            else:
                                candidates.append((child_path, parsed))
                visit(child, child_path)
        elif isinstance(node, list):
            for index, child in enumerate(node):
                visit(child, f"{path}[{index}]")

    visit(value, "$")
    return candidates, malformed


__all__ = ["SettlementEvidence", "SettlementEvidenceRepository"]

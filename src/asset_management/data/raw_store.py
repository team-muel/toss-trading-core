"""Append-only raw Toss response storage with pre-storage redaction."""

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import sqlite3
from typing import Any, Mapping
from uuid import uuid4

from asset_management.broker.redaction import redact, sanitized_headers


@dataclass(frozen=True, slots=True)
class RawApiResponse:
    source: str
    endpoint: str
    http_method: str
    request_hash: str
    status_code: int
    response_hash: str
    body: object
    requested_at: datetime
    received_at: datetime
    account_id: str | None
    schema_version: str
    headers: Mapping[str, str]


class SQLiteRawResponseStore:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def append(
        self,
        *,
        source: str,
        endpoint: str,
        http_method: str,
        request_payload: object,
        status_code: int,
        body: Any,
        requested_at: datetime,
        received_at: datetime,
        account_id: str | None,
        schema_version: str,
        headers: Mapping[str, str] | None = None,
    ) -> str:
        for name, value in (("requested_at", requested_at), ("received_at", received_at)):
            if value.tzinfo is None or value.utcoffset() != timezone.utc.utcoffset(None):
                raise ValueError(f"{name} must be timezone-aware UTC")
        if received_at < requested_at:
            raise ValueError("received_at cannot precede requested_at")
        safe_body = redact(body)
        safe_headers = sanitized_headers(dict(headers or {}))
        request_hash = _hash(redact(request_payload))
        response_hash = _hash(safe_body)
        identifier = str(uuid4())
        with self._conn:
            self._conn.execute(
                """
                INSERT INTO am_raw_api_response (
                  raw_response_id, source, endpoint, http_method, request_hash,
                  status_code, response_hash, body_json, requested_at_utc,
                  received_at_utc, account_id, schema_version, headers_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    identifier, source, endpoint, http_method.upper(), request_hash,
                    status_code, response_hash, _canonical(safe_body), requested_at.isoformat(),
                    received_at.isoformat(), account_id, schema_version, _canonical(safe_headers),
                ),
            )
        return identifier


def _canonical(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _hash(value: object) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()

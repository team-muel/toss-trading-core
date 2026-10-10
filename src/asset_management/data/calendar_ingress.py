"""Official calendar publications: raw evidence only, never session authority."""
from __future__ import annotations

import base64
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import re
from urllib.parse import urlsplit

from .immutable import ImmutableDatasetStore, StoredDatasetManifest, digest, _assert_no_secret

SCHEMA = "official-calendar-publication-v1"
HOSTS = frozenset({"www.nyse.com", "www.nasdaq.com", "www.nasdaqtrader.com", "ir.theice.com"})


def _url(value: object):
    if not isinstance(value, str):
        raise ValueError("CALENDAR_URL_INVALID")
    url = urlsplit(value)
    if url.scheme != "https" or url.hostname not in HOSTS or url.username or url.password or url.port or url.fragment:
        raise ValueError("CALENDAR_URL_INVALID")
    return url


def _capture(store: ImmutableDatasetStore, identifier: str):
    receipt = store.read_catalog("unadmitted-http-responses", identifier)
    if (not isinstance(receipt, dict) or receipt.get("source") != "official-exchange-calendar"
            or receipt.get("mode") != "RESEARCH" or receipt.get("http_method") != "GET"
            or not isinstance(receipt.get("dataset"), str) or not receipt["dataset"].strip()
            or type(receipt.get("status_code")) is not int or receipt["status_code"] != 200):
        raise ValueError("CALENDAR_CAPTURE_INVALID")
    endpoint = _url(receipt.get("endpoint"))
    request = receipt.get("request")
    if not isinstance(request, dict) or request.get("url") != receipt["endpoint"]:
        raise ValueError("CALENDAR_REQUEST_CONFLICT")
    _url(receipt.get("final_url"))
    if not isinstance(receipt.get("body_base64"), str):
        raise ValueError("CALENDAR_BODY_REQUIRED")
    raw = base64.b64decode(receipt["body_base64"], validate=True)
    if not raw or digest(raw) != receipt.get("body_sha256"):
        raise ValueError("CALENDAR_BODY_HASH_INVALID")
    if any(secret and secret.encode() in raw for secret in store.secrets):
        raise ValueError("CALENDAR_SECRET_ECHO")
    _assert_no_secret(raw.decode("utf-8", errors="ignore"), store.secrets)
    if not (raw.startswith(b"%PDF-") or b"<html" in raw[:4096].lower() or b"<!doctype html" in raw[:4096].lower()):
        raise ValueError("CALENDAR_DOCUMENT_FORMAT_INVALID")
    if not isinstance(receipt.get("received_at"), str) or not isinstance(receipt.get("http_date"), str):
        raise ValueError("CALENDAR_TRANSPORT_TIME_REQUIRED")
    received = datetime.fromisoformat(receipt["received_at"])
    provider = parsedate_to_datetime(receipt["http_date"])
    if any(value.tzinfo is None or value.utcoffset() is None for value in (received, provider)):
        raise ValueError("CALENDAR_TRANSPORT_TIME_UNKNOWN")
    received, provider = received.astimezone(timezone.utc), provider.astimezone(timezone.utc)
    if provider > received:
        raise ValueError("CALENDAR_TRANSPORT_TIME_ORDER_INVALID")
    return receipt, endpoint, received, provider


def _request(receipt, endpoint):
    return {"source":"official-exchange-calendar", "dataset":"calendar-publication",
        "endpoint":endpoint.path + ("?" + endpoint.query if endpoint.query else ""),
        "http_method":"GET", "request":receipt["request"]}


def admit_calendar_publication(store: ImmutableDatasetStore, response_id: str, *,
                               license_tag: str, code_revision: str) -> StoredDatasetManifest:
    if not store.credentials_classified:
        raise ValueError("CREDENTIALS_NOT_CLASSIFIED")
    if not isinstance(code_revision, str) or not re.fullmatch(r"git:[0-9a-f]{7,40}", code_revision):
        raise ValueError("INVALID_CODE_REVISION")
    receipt, endpoint, received, provider = _capture(store, response_id)
    request_hash = store.catalog("requests", _request(receipt, endpoint))
    return store.write({"publication_base64":receipt["body_base64"], "response_receipt":response_id},
        layer="bronze", source="official-exchange-calendar", dataset="calendar-publication",
        schema_version=SCHEMA, retrieved_at=received, available_at=received,
        provider_timestamp=provider, license_tag=license_tag, code_revision=code_revision,
        request_hash=request_hash, quality_status="RAW")


def validate_calendar_publication_lineage(store, manifest, body):
    if not isinstance(body, dict) or set(body) != {"publication_base64", "response_receipt"}:
        raise ValueError("CALENDAR_PUBLICATION_LINEAGE_INVALID")
    receipt, endpoint, received, provider = _capture(store, body["response_receipt"])
    if (body["publication_base64"] != receipt["body_base64"]
            or store.read_catalog("requests", manifest.request_hash) != _request(receipt, endpoint)
            or manifest.retrieved_at != received.isoformat()
            or manifest.available_at != received.isoformat()
            or manifest.provider_timestamp != provider.isoformat()):
        raise ValueError("CALENDAR_PUBLICATION_LINEAGE_CONFLICT")

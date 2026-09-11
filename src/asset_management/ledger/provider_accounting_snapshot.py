"""Immutable, provider-derived accounting snapshots.

This module deliberately has no API that accepts a caller-supplied cash, NAV,
FX, or position value.  Those economic values are read only from a verified,
persisted provider response whose contract was recorded before it is used.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
import json
import sqlite3
from pathlib import Path
import sys
from typing import Mapping

from asset_management.data.immutable import canonical, digest
from asset_management.data.raw_store import SQLiteRawResponseStore
from asset_management.domain.errors import ReconciliationError
from .accounting import MoneyTranslation
from .cash import exact
from .nav_basis import NavComponent, NavComponentKind, reconcile_accounting_nav


_SNAPSHOT_SCHEMA = "provider-accounting-snapshot@1"
_CONTRACT_SCHEMA = "provider-accounting-contract@1"
_REGISTRY_SCHEMA = "provider-accounting-contract-registry@1"
_STATEMENT_SCHEMA = "normalized-accounting-statement@1"


def _text(value: object, code: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ReconciliationError(code)
    return value.strip()


def _timestamp(value: object, code: str) -> datetime:
    if not isinstance(value, str):
        raise ReconciliationError(code)
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ReconciliationError(code) from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ReconciliationError(code)
    return parsed.astimezone(timezone.utc)


def _database_now_utc(conn: sqlite3.Connection) -> datetime:
    """Get contract-recording time from the evidence store, not the caller."""
    value = conn.execute("SELECT strftime('%Y-%m-%dT%H:%M:%f+00:00', 'now')").fetchone()[0]
    return _timestamp(value, "ACCOUNTING_PROVIDER_CONTRACT_TIME_INVALID")


def _money(value: object, code: str) -> Decimal:
    try:
        return exact(value, code)
    except ReconciliationError:
        raise ReconciliationError(code) from None


def _currency(value: object, code: str) -> str:
    result = _text(value, code).upper()
    if len(result) != 3 or not result.isalpha():
        raise ReconciliationError(code)
    return result


def _provider(value: object, code: str) -> str:
    result = _text(value, code).lower()
    if result == "toss":
        raise ReconciliationError("ACCOUNTING_PROVIDER_CONTRACT_UNSUPPORTED")
    return result


def _digest(value: object, code: str) -> str:
    result = _text(value, code)
    if len(result) != 64 or any(character not in "0123456789abcdef" for character in result):
        raise ReconciliationError(code)
    return result


def _rows(value: object, code: str) -> list[Mapping[str, object]]:
    if not isinstance(value, list) or not value:
        raise ReconciliationError(code)
    if not all(isinstance(item, Mapping) for item in value):
        raise ReconciliationError(code)
    return list(value)


@dataclass(frozen=True, slots=True)
class _ApprovedContract:
    provider: str
    endpoint: str
    schema_version: str
    contract_version: str
    approval_endpoint: str
    approval_schema_version: str
    effective_from_utc: datetime
    effective_to_utc: datetime | None
    approved_by: str


def _provider_contract_registry_path() -> Path:
    """Use the repository/package-owned registry, never the process CWD."""
    checkout = Path(__file__).resolve().parents[3] / "config/provider_accounting_contracts.json"
    if checkout.is_file():
        return checkout
    installed = Path(sys.prefix) / "share/toss-trading/config/provider_accounting_contracts.json"
    if installed.is_file():
        return installed
    raise FileNotFoundError("provider accounting contract registry is unavailable")


def _approved_contracts() -> tuple[dict[tuple[str, str, str, str], _ApprovedContract], str]:
    """Load only the reviewed, version-controlled provider contract registry."""
    try:
        document = json.loads(_provider_contract_registry_path().read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, json.JSONDecodeError) as exc:
        raise ReconciliationError("ACCOUNTING_PROVIDER_REGISTRY_INVALID") from exc
    if not isinstance(document, Mapping) or document.get("schema_version") != _REGISTRY_SCHEMA:
        raise ReconciliationError("ACCOUNTING_PROVIDER_REGISTRY_INVALID")
    entries = document.get("contracts")
    if not isinstance(entries, list):
        raise ReconciliationError("ACCOUNTING_PROVIDER_REGISTRY_INVALID")
    result: dict[tuple[str, str, str, str], _ApprovedContract] = {}
    for row in entries:
        if not isinstance(row, Mapping) or set(row) != {
                "provider", "endpoint", "schema_version", "contract_version", "approval_endpoint",
                "approval_schema_version", "statement_schema", "effective_from_utc", "effective_to_utc",
                "approved_by"}:
            raise ReconciliationError("ACCOUNTING_PROVIDER_REGISTRY_INVALID")
        provider = _provider(row.get("provider"), "ACCOUNTING_PROVIDER_REGISTRY_INVALID")
        endpoint = _text(row.get("endpoint"), "ACCOUNTING_PROVIDER_REGISTRY_INVALID")
        approval_endpoint = _text(row.get("approval_endpoint"), "ACCOUNTING_PROVIDER_REGISTRY_INVALID")
        if not endpoint.startswith("/") or not approval_endpoint.startswith("/") or \
                row.get("statement_schema") != _STATEMENT_SCHEMA:
            raise ReconciliationError("ACCOUNTING_PROVIDER_REGISTRY_INVALID")
        effective_from = _timestamp(row.get("effective_from_utc"), "ACCOUNTING_PROVIDER_REGISTRY_INVALID")
        effective_to_raw = row.get("effective_to_utc")
        effective_to = None if effective_to_raw is None else _timestamp(
            effective_to_raw, "ACCOUNTING_PROVIDER_REGISTRY_INVALID")
        if effective_to is not None and effective_to <= effective_from:
            raise ReconciliationError("ACCOUNTING_PROVIDER_REGISTRY_INVALID")
        item = _ApprovedContract(
            provider, endpoint, _text(row.get("schema_version"), "ACCOUNTING_PROVIDER_REGISTRY_INVALID"),
            _text(row.get("contract_version"), "ACCOUNTING_PROVIDER_REGISTRY_INVALID"), approval_endpoint,
            _text(row.get("approval_schema_version"), "ACCOUNTING_PROVIDER_REGISTRY_INVALID"),
            effective_from, effective_to, _text(row.get("approved_by"), "ACCOUNTING_PROVIDER_REGISTRY_INVALID"),
        )
        key = (item.provider, item.endpoint, item.schema_version, item.contract_version)
        if key in result:
            raise ReconciliationError("ACCOUNTING_PROVIDER_REGISTRY_INVALID")
        result[key] = item
    return result, digest(canonical(document))


def _rule_payload(rule: _ApprovedContract, registry_hash: str) -> dict[str, object]:
    return {
        "registry_schema_version": _REGISTRY_SCHEMA,
        "registry_content_hash": registry_hash,
        "provider": rule.provider,
        "endpoint": rule.endpoint,
        "schema_version": rule.schema_version,
        "contract_version": rule.contract_version,
        "approval_endpoint": rule.approval_endpoint,
        "approval_schema_version": rule.approval_schema_version,
        "statement_schema": _STATEMENT_SCHEMA,
        "effective_from_utc": rule.effective_from_utc.isoformat(),
        "effective_to_utc": rule.effective_to_utc.isoformat() if rule.effective_to_utc else None,
        "approved_by": rule.approved_by,
    }


def _rule_from_payload(value: object) -> _ApprovedContract:
    if not isinstance(value, Mapping) or set(value) != {
            "registry_schema_version", "registry_content_hash", "provider", "endpoint", "schema_version",
            "contract_version", "approval_endpoint", "approval_schema_version", "statement_schema",
            "effective_from_utc", "effective_to_utc", "approved_by"} or \
            value.get("registry_schema_version") != _REGISTRY_SCHEMA or \
            value.get("statement_schema") != _STATEMENT_SCHEMA:
        raise ReconciliationError("ACCOUNTING_SNAPSHOT_CONTRACT_INVALID")
    _digest(value.get("registry_content_hash"), "ACCOUNTING_SNAPSHOT_CONTRACT_INVALID")
    provider = _provider(value.get("provider"), "ACCOUNTING_SNAPSHOT_CONTRACT_INVALID")
    endpoint = _text(value.get("endpoint"), "ACCOUNTING_SNAPSHOT_CONTRACT_INVALID")
    approval_endpoint = _text(value.get("approval_endpoint"), "ACCOUNTING_SNAPSHOT_CONTRACT_INVALID")
    if not endpoint.startswith("/") or not approval_endpoint.startswith("/"):
        raise ReconciliationError("ACCOUNTING_SNAPSHOT_CONTRACT_INVALID")
    start = _timestamp(value.get("effective_from_utc"), "ACCOUNTING_SNAPSHOT_CONTRACT_INVALID")
    end = value.get("effective_to_utc")
    end_at = None if end is None else _timestamp(end, "ACCOUNTING_SNAPSHOT_CONTRACT_INVALID")
    if end_at is not None and end_at <= start:
        raise ReconciliationError("ACCOUNTING_SNAPSHOT_CONTRACT_INVALID")
    return _ApprovedContract(
        provider, endpoint, _text(value.get("schema_version"), "ACCOUNTING_SNAPSHOT_CONTRACT_INVALID"),
        _text(value.get("contract_version"), "ACCOUNTING_SNAPSHOT_CONTRACT_INVALID"), approval_endpoint,
        _text(value.get("approval_schema_version"), "ACCOUNTING_SNAPSHOT_CONTRACT_INVALID"),
        start, end_at, _text(value.get("approved_by"), "ACCOUNTING_SNAPSHOT_CONTRACT_INVALID"),
    )


def _validate_approval_artifact(approval, rule: _ApprovedContract, registry_hash: str, code: str) -> None:
    """Require approval evidence to attest to the exact canonical contract rule."""
    if not isinstance(approval.body, Mapping):
        raise ReconciliationError(code)
    expected = {
        "provider": rule.provider,
        "endpoint": rule.endpoint,
        "schemaVersion": rule.schema_version,
        "contractVersion": rule.contract_version,
        "statementSchema": _STATEMENT_SCHEMA,
        "effectiveFromUtc": rule.effective_from_utc.isoformat(),
        "effectiveToUtc": rule.effective_to_utc.isoformat() if rule.effective_to_utc else None,
        "approvedBy": rule.approved_by,
        "registryContentHash": _digest(registry_hash, code),
    }
    if dict(approval.body) != expected:
        raise ReconciliationError(code)


@dataclass(frozen=True, slots=True)
class ProviderAccountingContract:
    """A reviewed provider response identity, never a source of economic values."""

    provider: str
    endpoint: str
    schema_version: str
    contract_version: str
    approval_evidence_id: str

    def __post_init__(self) -> None:
        values = (
            ("provider", self.provider), ("endpoint", self.endpoint),
            ("schema_version", self.schema_version), ("contract_version", self.contract_version),
            ("approval_evidence_id", self.approval_evidence_id),
        )
        for _name, value in values:
            _text(value, "ACCOUNTING_PROVIDER_CONTRACT_INVALID")
        # The currently approved Toss OpenAPI has no accounting-statement endpoint.
        # Its buying-power and holdings responses must therefore never be promoted
        # into cash or NAV evidence by declaring an ad-hoc Toss contract.
        object.__setattr__(self, "provider", _provider(self.provider, "ACCOUNTING_PROVIDER_CONTRACT_INVALID"))
        if not self.endpoint.startswith("/"):
            raise ReconciliationError("ACCOUNTING_PROVIDER_CONTRACT_INVALID")

    def payload(self) -> dict[str, str]:
        return {
            "schema_version": _CONTRACT_SCHEMA,
            "provider": self.provider,
            "endpoint": self.endpoint,
            "provider_schema_version": self.schema_version,
            "contract_version": self.contract_version,
            "approval_evidence_id": self.approval_evidence_id,
        }

class ProviderAccountingContractRepository:
    """Persists the reviewed source identity before statements can be consumed."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def record(self, contract: ProviderAccountingContract) -> str:
        if not isinstance(contract, ProviderAccountingContract):
            raise ReconciliationError("ACCOUNTING_PROVIDER_CONTRACT_INVALID")
        # Recovery must not depend on a later version of the mutable registry
        # or on its former effective interval.  A matching append-only contract
        # is self-describing: _contract validates its persisted rule, approval
        # response, content hash, and original recording time without rereading
        # the current registry.
        existing = self._existing(contract)
        if existing is not None:
            return existing
        recorded_at = _database_now_utc(self._conn)
        rules, registry_hash = _approved_contracts()
        rule = rules.get((contract.provider, contract.endpoint, contract.schema_version, contract.contract_version))
        if rule is None:
            raise ReconciliationError("ACCOUNTING_PROVIDER_CONTRACT_UNAPPROVED")
        # Approval must itself be hash-verified immutable provider evidence.  A bare
        # string, fixture name, or caller assertion cannot approve a contract.
        try:
            approval = SQLiteRawResponseStore(self._conn).verified(contract.approval_evidence_id)
        except (KeyError, TypeError, ValueError) as exc:
            raise ReconciliationError("ACCOUNTING_PROVIDER_CONTRACT_APPROVAL_INVALID") from exc
        if (approval.source != rule.provider or approval.endpoint != rule.approval_endpoint or
                approval.http_method != "GET" or approval.status_code != 200 or
                approval.schema_version != rule.approval_schema_version or
                approval.received_at.astimezone(timezone.utc) > recorded_at or
                recorded_at < rule.effective_from_utc or
                (rule.effective_to_utc is not None and recorded_at >= rule.effective_to_utc)):
            raise ReconciliationError("ACCOUNTING_PROVIDER_CONTRACT_APPROVAL_INVALID")
        _validate_approval_artifact(
            approval, rule, registry_hash, "ACCOUNTING_PROVIDER_CONTRACT_APPROVAL_INVALID")
        body = contract.payload() | {"approved_rule": _rule_payload(rule, registry_hash)}
        content_hash = digest(canonical(body))
        identifier = f"provider-accounting-contract:{content_hash}"
        payload = json.dumps(body, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        recorded = recorded_at.isoformat()
        with self._conn:
            existing = self._conn.execute(
                "SELECT payload_json, content_hash FROM am_provider_accounting_contract WHERE provider_contract_id=?",
                (identifier,),
            ).fetchone()
            if existing is not None:
                if (str(existing[0]), str(existing[1])) != (payload, content_hash):
                    raise ReconciliationError("ACCOUNTING_PROVIDER_CONTRACT_ID_CONFLICT")
                try:
                    ProviderAccountingSnapshotRepository(self._conn)._contract(identifier)
                except ReconciliationError as exc:
                    raise ReconciliationError("ACCOUNTING_PROVIDER_CONTRACT_ID_CONFLICT") from exc
                return identifier
            self._conn.execute(
                """INSERT INTO am_provider_accounting_contract
                   (provider_contract_id, provider, endpoint, http_method, provider_schema_version,
                    approval_evidence_id, payload_json, content_hash, recorded_at_utc)
                   VALUES (?, ?, ?, 'GET', ?, ?, ?, ?, ?)""",
                (identifier, contract.provider, contract.endpoint, contract.schema_version,
                 contract.approval_evidence_id, payload, content_hash, recorded),
            )
        return identifier

    def _existing(self, contract: ProviderAccountingContract) -> str | None:
        rows = self._conn.execute(
            """SELECT provider_contract_id, payload_json, content_hash
               FROM am_provider_accounting_contract
               WHERE provider=? AND endpoint=? AND provider_schema_version=? AND approval_evidence_id=?""",
            (contract.provider, contract.endpoint, contract.schema_version, contract.approval_evidence_id),
        ).fetchall()
        expected = contract.payload()
        for identifier, payload_json, content_hash in rows:
            try:
                payload = json.loads(str(payload_json))
            except json.JSONDecodeError as exc:
                raise ReconciliationError("ACCOUNTING_PROVIDER_CONTRACT_ID_CONFLICT") from exc
            if digest(canonical(payload)) != str(content_hash):
                raise ReconciliationError("ACCOUNTING_PROVIDER_CONTRACT_ID_CONFLICT")
            if not isinstance(payload, Mapping):
                raise ReconciliationError("ACCOUNTING_PROVIDER_CONTRACT_ID_CONFLICT")
            if all(payload.get(key) == value for key, value in expected.items()):
                try:
                    ProviderAccountingSnapshotRepository(self._conn)._contract(str(identifier))
                except ReconciliationError as exc:
                    raise ReconciliationError("ACCOUNTING_PROVIDER_CONTRACT_ID_CONFLICT") from exc
                return str(identifier)
        return None

@dataclass(frozen=True, slots=True)
class ProviderAccountingSnapshot:
    accounting_snapshot_id: str
    runtime_run_id: str
    provider_contract_id: str
    source_response_id: str
    account_id: str
    observed_at_utc: datetime
    payload: Mapping[str, object]
    content_hash: str


class ProviderAccountingSnapshotRepository:
    """Materialize a complete NAV snapshot only from persisted provider evidence."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def materialize(self, *, runtime_run_id: str, provider_contract_id: str,
                    source_response_id: str) -> ProviderAccountingSnapshot:
        runtime_run_id = _text(runtime_run_id, "ACCOUNTING_SNAPSHOT_RUNTIME_REQUIRED")
        provider_contract_id = _text(provider_contract_id, "ACCOUNTING_SNAPSHOT_CONTRACT_REQUIRED")
        source_response_id = _text(source_response_id, "ACCOUNTING_SNAPSHOT_RAW_REQUIRED")
        runtime = self._runtime(runtime_run_id)
        contract = self._contract(provider_contract_id)
        try:
            raw = SQLiteRawResponseStore(self._conn).verified(source_response_id)
        except (KeyError, TypeError, ValueError) as exc:
            raise ReconciliationError("ACCOUNTING_SNAPSHOT_RAW_INVALID") from exc
        if (raw.source != contract["provider"] or raw.endpoint != contract["endpoint"] or
                raw.http_method != "GET" or raw.status_code != 200 or
                raw.schema_version != contract["schema_version"] or not raw.account_id):
            raise ReconciliationError("ACCOUNTING_SNAPSHOT_SOURCE_CONTEXT_INVALID")
        requested_at = raw.requested_at.astimezone(timezone.utc)
        if (contract["recorded_at_utc"] > requested_at or contract["approval_received_at_utc"] > requested_at or
                requested_at < contract["effective_from_utc"] or
                (contract["effective_to_utc"] is not None and requested_at >= contract["effective_to_utc"])):
            raise ReconciliationError("ACCOUNTING_SNAPSHOT_CONTRACT_NOT_EFFECTIVE")
        body = self._parse_body(raw.body, raw.account_id, raw.received_at, runtime)
        body.update({
            "schema_version": _SNAPSHOT_SCHEMA,
            "runtime_run_id": runtime_run_id,
            "provider_contract_id": provider_contract_id,
            "provider_contract_hash": contract["content_hash"],
            "source_response_id": source_response_id,
            "source_response_hash": raw.response_hash,
            "provider": raw.source,
            "provider_schema_version": raw.schema_version,
            "provider_contract_effective_from_utc": contract["effective_from_utc"].isoformat(),
            "requested_at_utc": raw.requested_at.astimezone(timezone.utc).isoformat(),
            "received_at_utc": raw.received_at.astimezone(timezone.utc).isoformat(),
        })
        content_hash = digest(canonical(body))
        identifier = f"provider-accounting:{runtime_run_id}:{content_hash}"
        payload = json.dumps(body, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        with self._conn:
            existing = self._conn.execute(
                """SELECT runtime_run_id, provider_contract_id, source_response_id, account_id,
                          observed_at_utc, payload_json, content_hash
                   FROM am_provider_accounting_snapshot WHERE accounting_snapshot_id=?""",
                (identifier,),
            ).fetchone()
            if existing is None:
                self._conn.execute(
                    """INSERT INTO am_provider_accounting_snapshot
                       (accounting_snapshot_id, runtime_run_id, provider_contract_id, source_response_id,
                        account_id, observed_at_utc, payload_json, content_hash, recorded_at_utc)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (identifier, runtime_run_id, provider_contract_id, source_response_id,
                     body["account_id"], body["observed_at_utc"], payload, content_hash,
                     raw.received_at.astimezone(timezone.utc).isoformat()),
                )
            elif tuple(str(value) for value in existing[:4]) != (
                    runtime_run_id, provider_contract_id, source_response_id, str(body["account_id"])) or \
                    str(existing[4]) != str(body["observed_at_utc"]) or \
                    (str(existing[5]), str(existing[6])) != (payload, content_hash):
                raise ReconciliationError("ACCOUNTING_SNAPSHOT_ID_CONFLICT")
        return ProviderAccountingSnapshot(
            identifier, runtime_run_id, provider_contract_id, source_response_id,
            str(body["account_id"]), _timestamp(body["observed_at_utc"], "ACCOUNTING_SNAPSHOT_TIME_INVALID"),
            body, content_hash,
        )

    def replay(self, accounting_snapshot_id: str) -> ProviderAccountingSnapshot:
        identifier = _text(accounting_snapshot_id, "ACCOUNTING_SNAPSHOT_ID_REQUIRED")
        row = self._conn.execute(
            """SELECT runtime_run_id, provider_contract_id, source_response_id, payload_json, content_hash
               FROM am_provider_accounting_snapshot WHERE accounting_snapshot_id=?""", (identifier,)
        ).fetchone()
        if row is None:
            raise ReconciliationError("ACCOUNTING_SNAPSHOT_MISSING")
        persisted = json.loads(str(row[3]))
        if digest(canonical(persisted)) != str(row[4]):
            raise ReconciliationError("ACCOUNTING_SNAPSHOT_HASH_INVALID")
        replayed = self.materialize(runtime_run_id=str(row[0]), provider_contract_id=str(row[1]),
                                    source_response_id=str(row[2]))
        if replayed.accounting_snapshot_id != identifier or replayed.payload != persisted:
            raise ReconciliationError("ACCOUNTING_SNAPSHOT_REPLAY_MISMATCH")
        return replayed

    def _runtime(self, runtime_run_id: str) -> tuple[datetime, datetime]:
        row = self._conn.execute(
            "SELECT as_of_utc, information_cutoff_utc FROM am_runtime_run WHERE runtime_run_id=?",
            (runtime_run_id,),
        ).fetchone()
        if row is None:
            raise ReconciliationError("ACCOUNTING_SNAPSHOT_RUNTIME_MISSING")
        return (_timestamp(row[0], "ACCOUNTING_SNAPSHOT_RUNTIME_TIME_INVALID"),
                _timestamp(row[1], "ACCOUNTING_SNAPSHOT_RUNTIME_TIME_INVALID"))

    def _contract(self, provider_contract_id: str) -> dict[str, object]:
        row = self._conn.execute(
            """SELECT provider, endpoint, http_method, provider_schema_version, approval_evidence_id,
                      payload_json, content_hash, recorded_at_utc FROM am_provider_accounting_contract
               WHERE provider_contract_id=?""", (provider_contract_id,)
        ).fetchone()
        if row is None:
            raise ReconciliationError("ACCOUNTING_SNAPSHOT_CONTRACT_MISSING")
        try:
            payload = json.loads(str(row[5]))
        except json.JSONDecodeError as exc:
            raise ReconciliationError("ACCOUNTING_SNAPSHOT_CONTRACT_INVALID") from exc
        if (digest(canonical(payload)) != str(row[6]) or
                provider_contract_id != f"provider-accounting-contract:{row[6]}" or
                payload.get("schema_version") != _CONTRACT_SCHEMA or
                payload.get("provider") != row[0] or payload.get("endpoint") != row[1] or
                payload.get("provider_schema_version") != row[3] or
                payload.get("approval_evidence_id") != row[4] or str(row[2]) != "GET"):
            raise ReconciliationError("ACCOUNTING_SNAPSHOT_CONTRACT_INVALID")
        try:
            contract = ProviderAccountingContract(
                str(row[0]), str(row[1]), str(row[3]), _text(payload.get("contract_version"),
                "ACCOUNTING_SNAPSHOT_CONTRACT_INVALID"), str(row[4]))
            approved_rule = payload.get("approved_rule")
            rule = _rule_from_payload(approved_rule)
            if not isinstance(approved_rule, Mapping):
                raise ReconciliationError("ACCOUNTING_SNAPSHOT_CONTRACT_INVALID")
            registry_hash = _digest(
                approved_rule.get("registry_content_hash"), "ACCOUNTING_SNAPSHOT_CONTRACT_INVALID")
            approval = SQLiteRawResponseStore(self._conn).verified(str(row[4]))
            recorded_at = _timestamp(row[7], "ACCOUNTING_SNAPSHOT_CONTRACT_INVALID")
        except (KeyError, TypeError, ValueError, ReconciliationError) as exc:
            raise ReconciliationError("ACCOUNTING_SNAPSHOT_CONTRACT_INVALID") from exc
        approval_received = approval.received_at.astimezone(timezone.utc)
        if ((contract.provider, contract.endpoint, contract.schema_version, contract.contract_version) !=
                (rule.provider, rule.endpoint, rule.schema_version, rule.contract_version) or
                approval.source != rule.provider or approval.endpoint != rule.approval_endpoint or
                approval.http_method != "GET" or approval.status_code != 200 or
                approval.schema_version != rule.approval_schema_version or approval_received > recorded_at or
                recorded_at < rule.effective_from_utc or
                (rule.effective_to_utc is not None and recorded_at >= rule.effective_to_utc)):
            raise ReconciliationError("ACCOUNTING_SNAPSHOT_CONTRACT_INVALID")
        _validate_approval_artifact(approval, rule, registry_hash, "ACCOUNTING_SNAPSHOT_CONTRACT_INVALID")
        return {"provider": rule.provider, "endpoint": rule.endpoint,
                "schema_version": rule.schema_version, "content_hash": str(row[6]),
                "recorded_at_utc": recorded_at, "approval_received_at_utc": approval_received,
                "effective_from_utc": rule.effective_from_utc, "effective_to_utc": rule.effective_to_utc}

    def _parse_body(self, value: object, raw_account_id: str, received_at: datetime,
                    runtime: tuple[datetime, datetime]) -> dict[str, object]:
        if not isinstance(value, Mapping) or not isinstance(value.get("result"), Mapping):
            raise ReconciliationError("ACCOUNTING_SNAPSHOT_RESPONSE_INVALID")
        result = value["result"]
        # Account identifiers inside raw bodies are redacted before immutable storage.
        # The raw-store account context is therefore the only replayable authority.
        account_id = _text(raw_account_id, "ACCOUNTING_SNAPSHOT_ACCOUNT_INVALID")
        observed = _timestamp(result.get("observedAt"), "ACCOUNTING_SNAPSHOT_TIME_INVALID")
        as_of, cutoff = runtime
        if observed > received_at.astimezone(timezone.utc) or received_at.astimezone(timezone.utc) > as_of or \
                received_at.astimezone(timezone.utc) > cutoff:
            raise ReconciliationError("ACCOUNTING_SNAPSHOT_TIME_INVALID")
        reporting = _currency(result.get("reportingCurrency"), "ACCOUNTING_SNAPSHOT_CURRENCY_INVALID")
        fx_rates = self._fx_rates(result.get("fxRates"), reporting)
        components = self._components(result.get("components"), reporting, fx_rates)
        reported = MoneyTranslation(_money(result.get("reportedNav"), "ACCOUNTING_SNAPSHOT_NAV_INVALID"),
                                    reporting, reporting, Decimal(1))
        nav_basis = reconcile_accounting_nav(components, reported_nav=reported,
                                             formula_version="provider-accounting-snapshot@1")
        cash_states = self._cash_states(result.get("cashStates"), components, reporting, fx_rates)
        holdings = self._holdings(result.get("holdings"), components, reporting, fx_rates)
        buying_power = self._buying_power(result.get("brokerBuyingPower"), fx_rates, reporting)
        return {
            "account_id": account_id,
            "observed_at_utc": observed.isoformat(),
            "reporting_currency": reporting,
            "reported_nav": str(reported.amount_reporting),
            "nav_basis": nav_basis,
            "cash_states": cash_states,
            "holdings": holdings,
            "fx_rates": fx_rates,
            # Constraints remain typed evidence and are intentionally outside nav_basis.
            "broker_buying_power": buying_power,
        }

    def _fx_rates(self, value: object, reporting: str) -> list[dict[str, str]]:
        rows = _rows(value, "ACCOUNTING_SNAPSHOT_FX_REQUIRED")
        parsed: dict[str, Decimal] = {}
        for row in rows:
            base = _currency(row.get("baseCurrency"), "ACCOUNTING_SNAPSHOT_FX_INVALID")
            quote = _currency(row.get("quoteCurrency"), "ACCOUNTING_SNAPSHOT_FX_INVALID")
            rate = _money(row.get("rate"), "ACCOUNTING_SNAPSHOT_FX_INVALID")
            if quote != reporting or rate <= 0 or base in parsed:
                raise ReconciliationError("ACCOUNTING_SNAPSHOT_FX_INVALID")
            if base == reporting and rate != 1:
                raise ReconciliationError("ACCOUNTING_SNAPSHOT_FX_INVALID")
            parsed[base] = rate
        if reporting not in parsed:
            parsed[reporting] = Decimal(1)
        return [{"base_currency": currency, "reporting_currency": reporting,
                 "rate": str(parsed[currency])} for currency in sorted(parsed)]

    def _components(self, value: object, reporting: str,
                    fx_rates: list[dict[str, str]]) -> tuple[NavComponent, ...]:
        rates = {item["base_currency"]: Decimal(item["rate"]) for item in fx_rates}
        components: list[NavComponent] = []
        for index, row in enumerate(_rows(value, "ACCOUNTING_SNAPSHOT_COMPONENTS_REQUIRED")):
            try:
                kind = NavComponentKind(_text(row.get("kind"), "ACCOUNTING_SNAPSHOT_COMPONENT_INVALID"))
            except ValueError as exc:
                raise ReconciliationError("ACCOUNTING_SNAPSHOT_COMPONENT_INVALID") from exc
            if kind is NavComponentKind.BROKER_BUYING_POWER:
                raise ReconciliationError("ACCOUNTING_SNAPSHOT_COMPONENT_INVALID")
            currency = _currency(row.get("currency"), "ACCOUNTING_SNAPSHOT_COMPONENT_INVALID")
            rate = _money(row.get("fxToReporting"), "ACCOUNTING_SNAPSHOT_COMPONENT_INVALID")
            if rate <= 0 or rates.get(currency) != rate:
                raise ReconciliationError("ACCOUNTING_SNAPSHOT_COMPONENT_FX_INVALID")
            included = row.get("includedInField")
            if included is not None:
                # This statement contract has a complete, non-overlapping cash
                # and holdings breakdown. It does not carry enough provider
                # semantics to prove that an aggregate's cash/security totals
                # include a child exactly once, so any hidden child fails closed.
                _text(included, "ACCOUNTING_SNAPSHOT_COMPONENT_INVALID")
                raise ReconciliationError("ACCOUNTING_SNAPSHOT_COMPONENT_INVALID")
            components.append(NavComponent(
                _text(row.get("fieldId"), "ACCOUNTING_SNAPSHOT_COMPONENT_INVALID"), kind,
                MoneyTranslation(_money(row.get("amount"), "ACCOUNTING_SNAPSHOT_COMPONENT_INVALID"),
                                 currency, reporting, rate), included,
                f"provider-response:components[{index}].includedInField",
            ))
        return tuple(components)

    def _cash_states(self, value: object, components: tuple[NavComponent, ...], reporting: str,
                     fx_rates: list[dict[str, str]]) -> list[dict[str, str]]:
        fields = {item.field_id: item for item in components}
        rates = {item["base_currency"]: Decimal(item["rate"]) for item in fx_rates}
        output = []
        seen = set()
        referenced_fields = set()
        for row in _rows(value, "ACCOUNTING_SNAPSHOT_CASH_STATES_REQUIRED"):
            currency = _currency(row.get("currency"), "ACCOUNTING_SNAPSHOT_CASH_STATE_INVALID")
            if currency in seen:
                raise ReconciliationError("ACCOUNTING_SNAPSHOT_CASH_STATE_INVALID")
            seen.add(currency)
            rate = _money(row.get("fxToReporting"), "ACCOUNTING_SNAPSHOT_CASH_STATE_INVALID")
            if rates.get(currency) != rate:
                raise ReconciliationError("ACCOUNTING_SNAPSHOT_CASH_STATE_INVALID")
            entries = (("settled_cash", "settledCash", "settledCashFieldId", NavComponentKind.CASH),
                       ("unsettled_cash", "unsettledCash", "unsettledCashFieldId", NavComponentKind.UNSETTLED_CASH),
                       ("settlement_receivable", "settlementReceivable", "settlementReceivableFieldId", NavComponentKind.SETTLEMENT_RECEIVABLE),
                       ("settlement_payable", "settlementPayable", "settlementPayableFieldId", NavComponentKind.SETTLEMENT_PAYABLE))
            parsed = {"currency": currency, "fx_to_reporting": str(rate)}
            for output_name, amount_name, field_name, expected_kind in entries:
                amount = _money(row.get(amount_name), "ACCOUNTING_SNAPSHOT_CASH_STATE_INVALID")
                field_id = _text(row.get(field_name), "ACCOUNTING_SNAPSHOT_CASH_STATE_INVALID")
                field = fields.get(field_id)
                if field is None or field.kind is not expected_kind or field.money.native_currency != currency or \
                        field.money.fx_to_reporting != rate or field.money.amount_native != amount:
                    raise ReconciliationError("ACCOUNTING_SNAPSHOT_CASH_STATE_COMPONENT_MISMATCH")
                if field_id in referenced_fields:
                    raise ReconciliationError("ACCOUNTING_SNAPSHOT_CASH_STATE_COMPONENT_MISMATCH")
                referenced_fields.add(field_id)
                parsed[output_name] = str(amount)
                parsed[f"{output_name}_field_id"] = field_id
            output.append(parsed)
        cash_kinds = {NavComponentKind.CASH, NavComponentKind.UNSETTLED_CASH,
                      NavComponentKind.SETTLEMENT_RECEIVABLE, NavComponentKind.SETTLEMENT_PAYABLE}
        if referenced_fields != {field.field_id for field in components if field.kind in cash_kinds}:
            raise ReconciliationError("ACCOUNTING_SNAPSHOT_CASH_STATE_COVERAGE_INCOMPLETE")
        return sorted(output, key=lambda item: item["currency"])

    def _holdings(self, value: object, components: tuple[NavComponent, ...], reporting: str,
                  fx_rates: list[dict[str, str]]) -> list[dict[str, str]]:
        rates = {item["base_currency"]: Decimal(item["rate"]) for item in fx_rates}
        totals: dict[tuple[str, Decimal], Decimal] = {}
        output = []
        identities = set()
        for index, row in enumerate(_rows(value, "ACCOUNTING_SNAPSHOT_HOLDINGS_REQUIRED")):
            instrument = _text(row.get("instrumentId"), "ACCOUNTING_SNAPSHOT_HOLDING_INVALID")
            currency = _currency(row.get("currency"), "ACCOUNTING_SNAPSHOT_HOLDING_INVALID")
            quantity = _money(row.get("quantity"), "ACCOUNTING_SNAPSHOT_HOLDING_INVALID")
            price = _money(row.get("marketPrice"), "ACCOUNTING_SNAPSHOT_HOLDING_INVALID")
            value_native = _money(row.get("marketValue"), "ACCOUNTING_SNAPSHOT_HOLDING_INVALID")
            rate = _money(row.get("fxToReporting"), "ACCOUNTING_SNAPSHOT_HOLDING_INVALID")
            if quantity < 0 or price < 0 or value_native < 0 or value_native != quantity * price or \
                    rates.get(currency) != rate or (instrument, currency) in identities:
                raise ReconciliationError("ACCOUNTING_SNAPSHOT_HOLDING_INVALID")
            identities.add((instrument, currency))
            totals[(currency, rate)] = totals.get((currency, rate), Decimal(0)) + value_native
            output.append({"instrument_id": instrument, "currency": currency, "quantity": str(quantity),
                           "market_price": str(price), "market_value": str(value_native),
                           "fx_to_reporting": str(rate), "source_path": f"holdings[{index}]"})
        securities: dict[tuple[str, Decimal], Decimal] = {}
        for component in components:
            if component.kind is NavComponentKind.SECURITIES and component.included_in_field is None:
                key = (component.money.native_currency, component.money.fx_to_reporting)
                securities[key] = securities.get(key, Decimal(0)) + component.money.amount_native
        if totals != securities:
            raise ReconciliationError("ACCOUNTING_SNAPSHOT_HOLDINGS_COMPONENT_MISMATCH")
        return sorted(output, key=lambda item: (item["instrument_id"], item["currency"]))

    def _buying_power(self, value: object, fx_rates: list[dict[str, str]], reporting: str) -> list[dict[str, str]]:
        rates = {item["base_currency"] for item in fx_rates}
        output = []
        seen = set()
        for index, row in enumerate(_rows(value, "ACCOUNTING_SNAPSHOT_BUYING_POWER_REQUIRED")):
            currency = _currency(row.get("currency"), "ACCOUNTING_SNAPSHOT_BUYING_POWER_INVALID")
            amount = _money(row.get("cashBuyingPower"), "ACCOUNTING_SNAPSHOT_BUYING_POWER_INVALID")
            if amount < 0 or currency not in rates or currency in seen:
                raise ReconciliationError("ACCOUNTING_SNAPSHOT_BUYING_POWER_INVALID")
            seen.add(currency)
            output.append({"currency": currency, "cash_buying_power": str(amount),
                           "source_path": f"brokerBuyingPower[{index}]"})
        return sorted(output, key=lambda item: item["currency"])


__all__ = [
    "ProviderAccountingContract", "ProviderAccountingContractRepository",
    "ProviderAccountingSnapshot", "ProviderAccountingSnapshotRepository",
]

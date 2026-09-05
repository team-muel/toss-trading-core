"""Immutable, fail-closed broker-to-ledger account reconciliation."""

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from hashlib import sha256
import json
import sqlite3
from typing import Iterable
from uuid import NAMESPACE_URL, uuid5

from asset_management.data.raw_store import SQLiteRawResponseStore
from asset_management.domain.errors import DataQualityError, ReconciliationError
from asset_management.account.orders import normalize_broker_order_state
from asset_management.ledger.cash import CashLedger
from asset_management.ledger.positions import PositionLedger


class ReconciliationTarget(StrEnum):
    HOLDINGS = "HOLDINGS"
    CASH = "CASH"
    OPEN_ORDERS = "OPEN_ORDERS"
    ORDER_STATE = "ORDER_STATE"
    CUMULATIVE_EXECUTION = "CUMULATIVE_EXECUTION"
    COMMISSION = "COMMISSION"
    TAX = "TAX"
    SETTLEMENT_DATE = "SETTLEMENT_DATE"
    SELLABLE_QUANTITY = "SELLABLE_QUANTITY"
    BUYING_POWER = "BUYING_POWER"


class ReconciliationStatus(StrEnum):
    MATCH = "MATCH"
    TOLERANCE_MATCH = "TOLERANCE_MATCH"
    MISMATCH = "MISMATCH"
    UNVERIFIABLE = "UNVERIFIABLE"
    BLOCKED = "BLOCKED"
    RESOLVED = "RESOLVED"


@dataclass(frozen=True, slots=True)
class ReconciliationFact:
    target: ReconciliationTarget
    dimension_key: str
    value: str
    numeric: bool
    currency: str | None = None
    instrument_id: str | None = None

    def __post_init__(self) -> None:
        if not self.dimension_key.strip() or self.value is None:
            raise ReconciliationError("reconciliation fact is incomplete")
        if self.numeric:
            _decimal(self.value, "reconciliation fact")
        if self.currency:
            object.__setattr__(self, "currency", self.currency.upper())


@dataclass(frozen=True, slots=True)
class ToleranceRule:
    target: ReconciliationTarget
    absolute: Decimal
    currency: str | None = None
    instrument_id: str | None = None

    def __post_init__(self) -> None:
        value = _decimal(self.absolute, "tolerance")
        if value < 0:
            raise ReconciliationError("reconciliation tolerance cannot be negative")
        object.__setattr__(self, "absolute", value)
        if self.currency:
            object.__setattr__(self, "currency", self.currency.upper())

    def matches(self, fact: ReconciliationFact) -> bool:
        return (
            self.target is fact.target
            and (self.currency is None or self.currency == fact.currency)
            and (self.instrument_id is None or self.instrument_id == fact.instrument_id)
        )

    @property
    def specificity(self) -> int:
        return int(self.currency is not None) + int(self.instrument_id is not None)


@dataclass(frozen=True, slots=True)
class ReconciliationPolicy:
    version: str
    effective_from_utc: datetime
    effective_to_utc: datetime | None
    approved_by: str
    approval_reason: str
    rules: tuple[ToleranceRule, ...]
    max_age_seconds: int

    def __post_init__(self) -> None:
        if (not self.version.strip() or not self.approved_by.strip()
                or not self.approval_reason.strip()):
            raise ReconciliationError("reconciliation policy approval is incomplete")
        if self.effective_from_utc.tzinfo is None or (
            self.effective_to_utc is not None and self.effective_to_utc.tzinfo is None
        ):
            raise ReconciliationError("reconciliation policy times must be timezone-aware")
        if self.effective_to_utc is not None and self.effective_to_utc <= self.effective_from_utc:
            raise ReconciliationError("reconciliation policy interval is invalid")
        if (not isinstance(self.max_age_seconds, int)
                or isinstance(self.max_age_seconds, bool) or self.max_age_seconds <= 0):
            raise ReconciliationError("reconciliation max age must be a positive integer")
        selectors = [(rule.target, rule.currency, rule.instrument_id) for rule in self.rules]
        if len(selectors) != len(set(selectors)):
            raise ReconciliationError("duplicate reconciliation tolerance selector")

    def tolerance_for(self, fact: ReconciliationFact) -> Decimal | None:
        matches = sorted(
            (rule for rule in self.rules if rule.matches(fact)),
            key=lambda rule: rule.specificity,
            reverse=True,
        )
        if len(matches) > 1 and matches[0].specificity == matches[1].specificity:
            raise ReconciliationError("ambiguous reconciliation tolerance rules")
        return matches[0].absolute if matches else None

    def payload(self) -> dict[str, object]:
        return {
            "version": self.version,
            "effective_from_utc": _utc(self.effective_from_utc),
            "effective_to_utc": _utc(self.effective_to_utc) if self.effective_to_utc else None,
            "approved_by": self.approved_by,
            "approval_reason": self.approval_reason,
            "max_age_seconds": self.max_age_seconds,
            "rules": [
                {
                    "target": rule.target.value,
                    "absolute": format(rule.absolute, "f"),
                    "currency": rule.currency,
                    "instrument_id": rule.instrument_id,
                }
                for rule in sorted(
                    self.rules,
                    key=lambda rule: (
                        rule.target.value, rule.currency or "", rule.instrument_id or ""
                    ),
                )
            ],
        }


@dataclass(frozen=True, slots=True)
class ReconciliationItem:
    target: ReconciliationTarget
    dimension_key: str
    broker_value: str | None
    internal_value: str | None
    difference: Decimal | None
    tolerance: Decimal | None
    status: ReconciliationStatus
    action_required: str | None


@dataclass(frozen=True, slots=True)
class ReconciliationRun:
    reconciliation_run_id: str
    account_id: str
    status: ReconciliationStatus
    items: tuple[ReconciliationItem, ...]
    unresolved_issue_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class TradeGate:
    action: str
    reason_codes: tuple[str, ...]


class AccountReconciler:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def register_policy(self, policy: ReconciliationPolicy) -> None:
        policy_payload = policy.payload()
        digest = _hash(_canonical(policy_payload))
        values = (
            _utc(policy.effective_from_utc),
            _utc(policy.effective_to_utc) if policy.effective_to_utc else None,
            policy.approved_by, policy.approval_reason,
            _canonical(policy_payload["rules"]), digest, policy.max_age_seconds,
        )
        existing = self._conn.execute(
            """SELECT effective_from_utc, effective_to_utc, approved_by,
                      approval_reason, rules_json, content_hash, max_age_seconds
               FROM am_reconciliation_tolerance_policy
               WHERE tolerance_policy_version=?""", (policy.version,),
        ).fetchone()
        if existing is not None:
            if tuple(existing) != values:
                raise ReconciliationError("reconciliation policy version is immutable")
            return
        self._conn.execute(
            """INSERT INTO am_reconciliation_tolerance_policy (
                 tolerance_policy_version, effective_from_utc, effective_to_utc,
                 approved_by, approval_reason, rules_json, content_hash, max_age_seconds
               ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (policy.version, *values),
        )

    def reconcile(
        self, *, runtime_run_id: str, account_snapshot_id: str, account_id: str,
        as_of_utc: datetime, policy: ReconciliationPolicy,
        broker_facts: Iterable[ReconciliationFact],
        internal_facts: Iterable[ReconciliationFact],
    ) -> ReconciliationRun:
        if as_of_utc.tzinfo is None:
            raise ReconciliationError("reconciliation as-of must be timezone-aware")
        as_of = as_of_utc.astimezone(timezone.utc)
        start = policy.effective_from_utc.astimezone(timezone.utc)
        end = policy.effective_to_utc.astimezone(timezone.utc) if policy.effective_to_utc else None
        if as_of < start or (end is not None and as_of >= end):
            raise ReconciliationError("reconciliation policy is not effective at as-of")
        self._validate_snapshot(runtime_run_id, account_snapshot_id, account_id, as_of)
        self.register_policy(policy)
        items = self._compare(
            _fact_map(broker_facts, "broker"), _fact_map(internal_facts, "internal"), policy
        )
        current_bad = any(item.status in {
            ReconciliationStatus.MISMATCH, ReconciliationStatus.UNVERIFIABLE,
            ReconciliationStatus.BLOCKED,
        } for item in items)
        prior_open = tuple(str(row[0]) for row in self._conn.execute(
            """SELECT i.issue_id FROM am_reconciliation_issue_v2 i
               LEFT JOIN am_reconciliation_resolution_v2 r USING(issue_id)
               WHERE i.account_id=? AND r.issue_id IS NULL ORDER BY i.issue_id""",
            (account_id,),
        ))
        if current_bad or prior_open:
            status = ReconciliationStatus.BLOCKED
        elif any(item.status is ReconciliationStatus.TOLERANCE_MATCH for item in items):
            status = ReconciliationStatus.TOLERANCE_MATCH
        else:
            status = ReconciliationStatus.MATCH
        payload = {
            "runtime_run_id": runtime_run_id, "account_snapshot_id": account_snapshot_id,
            "account_id": account_id, "policy_version": policy.version,
            "as_of_utc": as_of.isoformat(),
            "items": [_item_payload(item) for item in items],
        }
        content_hash = _hash(_canonical(payload))
        existing = self._conn.execute(
            """SELECT reconciliation_run_id, content_hash
               FROM am_account_reconciliation_v2
               WHERE runtime_run_id=? AND account_snapshot_id=?
                 AND tolerance_policy_version=?""",
            (runtime_run_id, account_snapshot_id, policy.version),
        ).fetchone()
        if existing is not None:
            if str(existing[1]) != content_hash:
                raise ReconciliationError(
                    "replayed reconciliation inputs conflict with the persisted run"
                )
            return self.load_run(str(existing[0]))
        run_id = str(uuid5(NAMESPACE_URL, f"reconciliation:{content_hash}"))
        self._conn.execute("SAVEPOINT am_account_reconciliation")
        try:
            self._conn.execute(
                "INSERT INTO am_account_reconciliation_v2 VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (run_id, runtime_run_id, account_snapshot_id, account_id, policy.version,
                 as_of.isoformat(), status, content_hash, as_of.isoformat()),
            )
            for item in items:
                self._persist_item(run_id, account_id, as_of, item)
            self._conn.execute("RELEASE SAVEPOINT am_account_reconciliation")
        except Exception:
            self._conn.execute("ROLLBACK TO SAVEPOINT am_account_reconciliation")
            self._conn.execute("RELEASE SAVEPOINT am_account_reconciliation")
            raise
        return self.load_run(run_id)

    def reconcile_snapshot(
        self, *, account_snapshot_id: str, as_of_utc: datetime,
        policy: ReconciliationPolicy,
    ) -> ReconciliationRun:
        """Compare one immutable Toss account-truth snapshot to ledger state."""

        row = self._conn.execute(
            """SELECT runtime_run_id, account_id, payload_json
               FROM am_account_snapshot WHERE account_snapshot_id=?""",
            (account_snapshot_id,),
        ).fetchone()
        if row is None:
            raise ReconciliationError("account snapshot does not exist")
        payload = json.loads(str(row[2]))
        if not isinstance(payload, dict):
            raise ReconciliationError("account snapshot payload is invalid")
        account_id = str(row[1])
        return self.reconcile(
            runtime_run_id=str(row[0]), account_snapshot_id=account_snapshot_id,
            account_id=account_id, as_of_utc=as_of_utc, policy=policy,
            broker_facts=_broker_facts(payload),
            internal_facts=self._internal_facts(account_id, as_of_utc),
        )

    def _internal_facts(
        self, account_id: str, as_of_utc: datetime
    ) -> tuple[ReconciliationFact, ...]:
        facts: list[ReconciliationFact] = []
        position_states = {}
        for (instrument_id,) in self._conn.execute(
            """SELECT instrument_id FROM am_position_opening_balance
               WHERE account_id=? ORDER BY instrument_id""", (account_id,),
        ):
            try:
                state = PositionLedger(self._conn).state(
                    account_id=account_id, instrument_id=str(instrument_id), as_of_utc=as_of_utc
                )
            except (DataQualityError, ReconciliationError):
                continue
            if state.quantity != 0:
                position_states[str(instrument_id)] = state
                facts.extend((
                    ReconciliationFact(
                        ReconciliationTarget.HOLDINGS, f"{instrument_id}:quantity",
                        format(state.quantity, "f"), True, state.native_currency,
                        str(instrument_id),
                    ),
                    ReconciliationFact(
                        ReconciliationTarget.HOLDINGS, f"{instrument_id}:average_cost",
                        format(state.average_cost, "f"), True, state.native_currency,
                        str(instrument_id),
                    ),
                    ReconciliationFact(
                        ReconciliationTarget.SELLABLE_QUANTITY, str(instrument_id),
                        format(state.available_to_sell, "f"), True, None,
                        str(instrument_id),
                    ),
                ))
        facts.append(_text_fact(
            ReconciliationTarget.HOLDINGS, "*", sorted(position_states)
        ))
        if not position_states:
            facts.append(_text_fact(ReconciliationTarget.SELLABLE_QUANTITY, "*", []))

        for (currency,) in self._conn.execute(
            """SELECT currency FROM am_cash_opening_balance
               WHERE account_id=? ORDER BY currency""", (account_id,),
        ):
            try:
                state = CashLedger(self._conn).state(
                    account_id=account_id, currency=str(currency), as_of_utc=as_of_utc
                )
            except (DataQualityError, ReconciliationError):
                continue
            facts.extend((
                ReconciliationFact(
                    ReconciliationTarget.CASH, str(currency),
                    format(state.settled_cash + state.unsettled_cash, "f"), True,
                    str(currency),
                ),
                ReconciliationFact(
                    ReconciliationTarget.BUYING_POWER, str(currency),
                    format(state.orderable_cash, "f"), True, str(currency),
                ),
            ))

        latest_orders = list(self._conn.execute(
            """SELECT o.broker_order_id, e.state, o.payload_json FROM am_broker_order o
               LEFT JOIN am_order_state_event e ON e.order_state_event_id=(
                 SELECT e2.order_state_event_id FROM am_order_state_event e2
                 WHERE e2.broker_order_id=o.broker_order_id
                 ORDER BY e2.sequence_no DESC LIMIT 1
               ) WHERE o.account_id=? ORDER BY o.broker_order_id""", (account_id,),
        ))
        open_rows = [row for row in latest_orders if row[1] in {
            "SUBMITTING", "SUBMITTED", "ACKNOWLEDGED", "OPEN", "PARTIALLY_FILLED",
            "CANCEL_PENDING", "REPLACE_PENDING", "UNKNOWN", "REVIEW_REQUIRED",
        }]
        open_ids = sorted(str(row[0]) for row in open_rows)
        facts.append(_text_fact(ReconciliationTarget.OPEN_ORDERS, "*", open_ids))
        for order_id_raw, _, payload_json in open_rows:
            order_id = str(order_id_raw)
            order_payload = json.loads(str(payload_json))
            for metric, field, numeric in (
                ("quantity", "quantity", True), ("side", "side", False),
                ("price", "price", True),
            ):
                if order_payload.get(field) is not None:
                    facts.append(ReconciliationFact(
                        ReconciliationTarget.OPEN_ORDERS, f"{order_id}:{metric}",
                        str(order_payload[field]).upper() if not numeric
                        else str(order_payload[field]), numeric,
                        str(order_payload.get("currency", "")) or None,
                        str(order_payload.get("instrumentId", order_payload.get("symbol", ""))) or None,
                    ))
        if not latest_orders:
            for target in (
                ReconciliationTarget.ORDER_STATE,
                ReconciliationTarget.CUMULATIVE_EXECUTION,
                ReconciliationTarget.COMMISSION, ReconciliationTarget.TAX,
                ReconciliationTarget.SETTLEMENT_DATE,
            ):
                facts.append(_text_fact(target, "*", []))
        for order_id_raw, state, order_payload_json in latest_orders:
            order_id = str(order_id_raw)
            order_payload = json.loads(str(order_payload_json))
            order_currency = str(order_payload.get("currency", "")) or None
            order_instrument = str(
                order_payload.get("instrumentId", order_payload.get("symbol", ""))
            ) or None
            if state is not None:
                facts.append(ReconciliationFact(
                    ReconciliationTarget.ORDER_STATE, order_id, str(state), False
                ))
            execution = self._conn.execute(
                """SELECT s.cumulative_quantity_decimal, s.cumulative_amount_decimal,
                          s.cumulative_commission_decimal, s.cumulative_tax_decimal,
                          c.settlement_date
                   FROM am_execution_snapshot s
                   LEFT JOIN am_execution_delta d ON d.to_snapshot_id=s.execution_snapshot_id
                   LEFT JOIN am_execution_posting_context c USING(execution_delta_id)
                   WHERE s.broker_order_id=? ORDER BY s.sequence_no DESC LIMIT 1""",
                (order_id,),
            ).fetchone()
            if execution is not None:
                for target, metric, value in (
                    (ReconciliationTarget.CUMULATIVE_EXECUTION, "quantity", execution[0]),
                    (ReconciliationTarget.CUMULATIVE_EXECUTION, "amount", execution[1]),
                    (ReconciliationTarget.COMMISSION, "commission", execution[2]),
                    (ReconciliationTarget.TAX, "tax", execution[3]),
                ):
                    facts.append(ReconciliationFact(
                        target, f"{order_id}:{metric}", str(value), True,
                        order_currency, order_instrument,
                    ))
                facts.append(ReconciliationFact(
                    ReconciliationTarget.SETTLEMENT_DATE, order_id,
                    str(execution[4]) if execution[4] is not None else "NONE", False,
                ))
        return tuple(facts)

    def _persist_item(
        self, run_id: str, account_id: str, as_of: datetime, item: ReconciliationItem
    ) -> None:
        item_id = str(uuid5(
            NAMESPACE_URL, f"reconciliation-item:{run_id}:{item.target}:{item.dimension_key}"
        ))
        self._conn.execute(
            "INSERT INTO am_reconciliation_item_v2 VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (item_id, run_id, item.target, item.dimension_key,
             item.broker_value, item.internal_value,
             format(item.difference, "f") if item.difference is not None else None,
             format(item.tolerance, "f") if item.tolerance is not None else None,
             item.status, item.action_required),
        )
        if item.status in {
            ReconciliationStatus.MISMATCH, ReconciliationStatus.UNVERIFIABLE,
            ReconciliationStatus.BLOCKED,
        }:
            difference = (
                format(item.difference, "f") if item.difference is not None
                else f"broker={item.broker_value!r};internal={item.internal_value!r}"
            )
            issue_id = str(uuid5(NAMESPACE_URL, f"issue:{item_id}"))
            self._conn.execute(
                "INSERT INTO am_reconciliation_issue_v2 VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (issue_id, run_id, account_id, item.target, item.dimension_key,
                 as_of.isoformat(), item.status, difference,
                 item.action_required or "NO_NEW_TRADES_AND_INVESTIGATE"),
            )

    def resolve_issue(
        self, *, issue_id: str, evidence_reconciliation_run_id: str,
        resolved_at_utc: datetime, resolution_note: str, approved_by: str,
    ) -> None:
        if resolved_at_utc.tzinfo is None or not resolution_note.strip() or not approved_by.strip():
            raise ReconciliationError("resolution requires time, note, and approver")
        issue = self._conn.execute(
            "SELECT account_id, target, dimension_key FROM am_reconciliation_issue_v2 WHERE issue_id=?",
            (issue_id,),
        ).fetchone()
        if issue is None:
            raise ReconciliationError("reconciliation issue does not exist")
        if self._conn.execute(
            "SELECT 1 FROM am_reconciliation_resolution_v2 WHERE issue_id=?", (issue_id,),
        ).fetchone():
            raise ReconciliationError("reconciliation issue is already resolved")
        evidence = self._conn.execute(
            """SELECT r.account_id, i.status FROM am_account_reconciliation_v2 r
               JOIN am_reconciliation_item_v2 i USING(reconciliation_run_id)
               WHERE r.reconciliation_run_id=? AND i.target=? AND i.dimension_key=?""",
            (evidence_reconciliation_run_id, issue[1], issue[2]),
        ).fetchone()
        if evidence is None or str(evidence[0]) != str(issue[0]) or str(evidence[1]) not in {
            ReconciliationStatus.MATCH, ReconciliationStatus.TOLERANCE_MATCH
        }:
            raise ReconciliationError("resolution requires matching reconciliation evidence")
        latest = self._conn.execute(
            """SELECT reconciliation_run_id FROM am_account_reconciliation_v2
               WHERE account_id=? ORDER BY completed_at_utc DESC, reconciliation_run_id DESC LIMIT 1""",
            (issue[0],),
        ).fetchone()
        if latest is None or str(latest[0]) != evidence_reconciliation_run_id:
            raise ReconciliationError("resolution evidence must be the latest reconciliation run")
        self._conn.execute(
            "INSERT INTO am_reconciliation_resolution_v2 VALUES (?, ?, ?, ?, ?)",
            (issue_id, evidence_reconciliation_run_id,
             resolved_at_utc.astimezone(timezone.utc).isoformat(),
             resolution_note.strip(), approved_by.strip()),
        )

    def trade_gate(
        self, *, account_id: str, reconciliation_run_id: str,
        evaluated_at_utc: datetime,
    ) -> TradeGate:
        if evaluated_at_utc.tzinfo is None:
            return TradeGate("NO_NEW_TRADES", ("EVALUATION_TIME_INVALID",))
        evaluated_at = evaluated_at_utc.astimezone(timezone.utc)
        run = self._conn.execute(
            """SELECT rr.status, rr.completed_at_utc, policy.max_age_seconds
               FROM am_account_reconciliation_v2 rr
               JOIN am_reconciliation_tolerance_policy policy
                 ON policy.tolerance_policy_version=rr.tolerance_policy_version
               WHERE rr.reconciliation_run_id=? AND rr.account_id=?""",
            (reconciliation_run_id, account_id),
        ).fetchone()
        if run is None:
            return TradeGate("NO_NEW_TRADES", ("RECONCILIATION_RUN_MISSING",))
        reasons = []
        completed_at = datetime.fromisoformat(str(run[1])).astimezone(timezone.utc)
        max_age_seconds = int(run[2]) if run[2] is not None else None
        age_seconds = (evaluated_at - completed_at).total_seconds()
        if age_seconds < 0:
            reasons.append("RECONCILIATION_FROM_FUTURE")
        if max_age_seconds is None or age_seconds > max_age_seconds:
            reasons.append("RECONCILIATION_STALE")
        latest = self._conn.execute(
            """SELECT reconciliation_run_id FROM am_account_reconciliation_v2
               WHERE account_id=? ORDER BY completed_at_utc DESC, reconciliation_run_id DESC LIMIT 1""",
            (account_id,),
        ).fetchone()
        if latest is None or str(latest[0]) != reconciliation_run_id:
            reasons.append("RECONCILIATION_RUN_NOT_LATEST")
        bad_items = self._conn.execute(
            """SELECT COUNT(*) FROM am_reconciliation_item_v2 WHERE reconciliation_run_id=?
               AND status IN ('MISMATCH','UNVERIFIABLE','BLOCKED')""",
            (reconciliation_run_id,),
        ).fetchone()[0]
        if bad_items:
            reasons.append("CURRENT_RECONCILIATION_FAILED")
        open_issues = self._conn.execute(
            """SELECT COUNT(*) FROM am_reconciliation_issue_v2 i
               LEFT JOIN am_reconciliation_resolution_v2 r USING(issue_id)
               WHERE i.account_id=? AND r.issue_id IS NULL""", (account_id,),
        ).fetchone()[0]
        if open_issues:
            reasons.append("UNRESOLVED_RECONCILIATION_ISSUE")
        return TradeGate("NO_NEW_TRADES", tuple(reasons)) if reasons else TradeGate("ALLOW", ())

    def authorize_order_intent(
        self, *, order_intent_id: str, account_id: str,
        reconciliation_run_id: str, authorized_at_utc: datetime,
    ) -> None:
        """Create the prerequisite consumed by the DB order-intent guard."""

        if not order_intent_id.strip() or authorized_at_utc.tzinfo is None:
            raise ReconciliationError("order-intent authorization is incomplete")
        gate = self.trade_gate(
            account_id=account_id, reconciliation_run_id=reconciliation_run_id,
            evaluated_at_utc=authorized_at_utc,
        )
        if gate.action != "ALLOW":
            raise ReconciliationError(
                f"NO_NEW_TRADES:{','.join(gate.reason_codes) or 'RECONCILIATION_BLOCKED'}"
            )
        existing = self._conn.execute(
            """SELECT account_id, reconciliation_run_id, authorized_at_utc
               FROM am_order_intent_reconciliation_authorization
               WHERE order_intent_id=?""", (order_intent_id,),
        ).fetchone()
        values = (
            account_id, reconciliation_run_id,
            authorized_at_utc.astimezone(timezone.utc).isoformat(),
        )
        if existing is not None:
            if tuple(existing) != values:
                raise ReconciliationError("order-intent reconciliation authorization conflicts")
            return
        self._conn.execute(
            "INSERT INTO am_order_intent_reconciliation_authorization VALUES (?, ?, ?, ?)",
            (order_intent_id, *values),
        )

    def load_run(self, reconciliation_run_id: str) -> ReconciliationRun:
        run = self._conn.execute(
            "SELECT account_id, status FROM am_account_reconciliation_v2 WHERE reconciliation_run_id=?",
            (reconciliation_run_id,),
        ).fetchone()
        if run is None:
            raise ReconciliationError("reconciliation run does not exist")
        items = tuple(
            ReconciliationItem(
                ReconciliationTarget(row[0]), str(row[1]), row[2], row[3],
                Decimal(row[4]) if row[4] is not None else None,
                Decimal(row[5]) if row[5] is not None else None,
                ReconciliationStatus(row[6]), row[7],
            )
            for row in self._conn.execute(
                """SELECT target, dimension_key, broker_value, internal_value,
                          difference_decimal, tolerance_decimal, status, action_required
                   FROM am_reconciliation_item_v2 WHERE reconciliation_run_id=?
                   ORDER BY target, dimension_key""", (reconciliation_run_id,),
            )
        )
        open_issues = tuple(str(row[0]) for row in self._conn.execute(
            """SELECT i.issue_id FROM am_reconciliation_issue_v2 i
               LEFT JOIN am_reconciliation_resolution_v2 r USING(issue_id)
               WHERE i.account_id=? AND r.issue_id IS NULL ORDER BY i.issue_id""",
            (run[0],),
        ))
        return ReconciliationRun(
            reconciliation_run_id, str(run[0]), ReconciliationStatus(run[1]), items, open_issues
        )

    def _validate_snapshot(
        self, runtime_run_id: str, snapshot_id: str, account_id: str, as_of: datetime
    ) -> None:
        row = self._conn.execute(
            """SELECT runtime_run_id, account_id, observed_at_utc, payload_json, content_hash
               FROM am_account_snapshot WHERE account_snapshot_id=?""", (snapshot_id,),
        ).fetchone()
        if row is None or (str(row[0]), str(row[1])) != (runtime_run_id, account_id):
            raise ReconciliationError("account snapshot lineage conflicts with reconciliation")
        observed = datetime.fromisoformat(str(row[2]))
        if observed.tzinfo is None:
            raise ReconciliationError("account snapshot observation time has no timezone")
        if observed.astimezone(timezone.utc) > as_of:
            raise ReconciliationError("account snapshot is from the future")
        payload = json.loads(str(row[3]))
        if _hash(_canonical(payload)) != str(row[4]):
            raise ReconciliationError("account snapshot content hash mismatch")
        raw_ids = tuple(row[0] for row in self._conn.execute(
            "SELECT raw_response_id FROM am_account_snapshot_raw WHERE account_snapshot_id=?",
            (snapshot_id,),
        ))
        if not raw_ids:
            raise ReconciliationError("account snapshot has no raw evidence")
        payload_raw_ids = payload.get("raw_response_ids")
        if (not isinstance(payload_raw_ids, list)
                or len(payload_raw_ids) != len(set(payload_raw_ids))
                or set(str(item) for item in payload_raw_ids) != set(str(item) for item in raw_ids)):
            raise ReconciliationError("account snapshot raw lineage is incomplete or conflicting")
        store = SQLiteRawResponseStore(self._conn)
        for raw_id in raw_ids:
            try:
                store.verified(str(raw_id))
            except (KeyError, ValueError) as error:
                raise ReconciliationError("account snapshot raw evidence failed verification") from error

    def _compare(self, broker, internal, policy) -> tuple[ReconciliationItem, ...]:
        keys = set(broker) | set(internal)
        present_targets = {key[0] for key in keys}
        keys.update((target, "*") for target in ReconciliationTarget if target not in present_targets)
        result = []
        for target, dimension in sorted(keys, key=lambda key: (key[0].value, key[1])):
            broker_fact, internal_fact = broker.get((target, dimension)), internal.get((target, dimension))
            if broker_fact is None or internal_fact is None:
                result.append(ReconciliationItem(
                    target, dimension, broker_fact.value if broker_fact else None,
                    internal_fact.value if internal_fact else None, None, None,
                    ReconciliationStatus.UNVERIFIABLE,
                    "NO_NEW_TRADES_AND_RELOAD_ACCOUNT_TRUTH",
                ))
                continue
            if broker_fact.numeric != internal_fact.numeric:
                result.append(ReconciliationItem(
                    target, dimension, broker_fact.value, internal_fact.value, None, None,
                    ReconciliationStatus.UNVERIFIABLE, "NO_NEW_TRADES_AND_REVIEW_SCHEMA",
                ))
                continue
            if broker_fact.numeric:
                tolerance = policy.tolerance_for(broker_fact)
                if tolerance is None:
                    result.append(ReconciliationItem(
                        target, dimension, broker_fact.value, internal_fact.value, None, None,
                        ReconciliationStatus.UNVERIFIABLE,
                        "NO_NEW_TRADES_AND_DEFINE_TOLERANCE",
                    ))
                    continue
                difference = _decimal(broker_fact.value, "broker value") - _decimal(
                    internal_fact.value, "internal value"
                )
                status = (
                    ReconciliationStatus.MATCH if difference == 0
                    else ReconciliationStatus.TOLERANCE_MATCH
                    if abs(difference) <= tolerance else ReconciliationStatus.MISMATCH
                )
                result.append(ReconciliationItem(
                    target, dimension, broker_fact.value, internal_fact.value,
                    difference, tolerance, status,
                    None if status is not ReconciliationStatus.MISMATCH
                    else "NO_NEW_TRADES_AND_INVESTIGATE_DIFFERENCE",
                ))
            else:
                status = (
                    ReconciliationStatus.MATCH
                    if broker_fact.value == internal_fact.value
                    else ReconciliationStatus.MISMATCH
                )
                result.append(ReconciliationItem(
                    target, dimension, broker_fact.value, internal_fact.value, None, None,
                    status, None if status is ReconciliationStatus.MATCH
                    else "NO_NEW_TRADES_AND_INVESTIGATE_DIFFERENCE",
                ))
        return tuple(result)


def _broker_facts(payload: dict[str, object]) -> tuple[ReconciliationFact, ...]:
    facts: list[ReconciliationFact] = []
    holdings = _rows(payload.get("holdings"), "holdings")
    holding_symbols = []
    for row in holdings:
        symbol = _required_text(row, "symbol", "holdings")
        currency = _required_text(row, "currency", "holdings").upper()
        holding_symbols.append(symbol)
        facts.extend((
            ReconciliationFact(
                ReconciliationTarget.HOLDINGS, f"{symbol}:quantity",
                _required_text(row, "quantity", "holdings"), True, currency, symbol,
            ),
            ReconciliationFact(
                ReconciliationTarget.HOLDINGS, f"{symbol}:average_cost",
                _required_text(row, "averagePurchasePrice", "holdings"),
                True, currency, symbol,
            ),
        ))
    facts.append(_text_fact(ReconciliationTarget.HOLDINGS, "*", sorted(holding_symbols)))

    accounts = _rows(payload.get("accounts"), "accounts")
    for account in accounts:
        balances = account.get("cashBalances")
        if isinstance(balances, dict):
            for currency, value in sorted(balances.items()):
                facts.append(ReconciliationFact(
                    ReconciliationTarget.CASH, str(currency).upper(), str(value), True,
                    str(currency),
                ))
        elif account.get("cashBalance") is not None and account.get("currency"):
            currency = str(account["currency"]).upper()
            facts.append(ReconciliationFact(
                ReconciliationTarget.CASH, currency, str(account["cashBalance"]), True, currency
            ))

    open_orders = _rows(payload.get("open_orders"), "open_orders")
    closed_orders = _rows(payload.get("closed_orders"), "closed_orders")
    open_ids = sorted(_required_text(row, "orderId", "open_orders") for row in open_orders)
    facts.append(_text_fact(ReconciliationTarget.OPEN_ORDERS, "*", open_ids))
    for row in open_orders:
        order_id = _required_text(row, "orderId", "open_orders")
        for metric, field, numeric in (
            ("quantity", "quantity", True), ("side", "side", False),
            ("price", "price", True),
        ):
            if row.get(field) is not None:
                facts.append(ReconciliationFact(
                    ReconciliationTarget.OPEN_ORDERS, f"{order_id}:{metric}",
                    str(row[field]).upper() if not numeric else str(row[field]), numeric,
                    str(row.get("currency", "")) or None,
                    str(row.get("instrumentId", row.get("symbol", ""))) or None,
                ))
    order_rows = {
        _required_text(row, "orderId", "orders"): row
        for row in (*open_orders, *closed_orders)
    }
    details = _rows(payload.get("order_details"), "order_details")
    detail_rows = {
        _required_text(row, "orderId", "order_details"): row for row in details
    }
    all_order_ids = sorted(set(order_rows) | set(detail_rows))
    if not all_order_ids:
        for target in (
            ReconciliationTarget.ORDER_STATE,
            ReconciliationTarget.CUMULATIVE_EXECUTION,
            ReconciliationTarget.COMMISSION, ReconciliationTarget.TAX,
            ReconciliationTarget.SETTLEMENT_DATE,
        ):
            facts.append(_text_fact(target, "*", []))
    for order_id in all_order_ids:
        order = detail_rows.get(order_id, order_rows.get(order_id, {}))
        raw_state = order.get("status")
        if raw_state is not None:
            facts.append(ReconciliationFact(
                ReconciliationTarget.ORDER_STATE, order_id,
                normalize_broker_order_state(raw_state).value, False,
            ))
        execution = order.get("execution")
        if not isinstance(execution, dict):
            continue
        for target, metric, field in (
            (ReconciliationTarget.CUMULATIVE_EXECUTION, "quantity", "filledQuantity"),
            (ReconciliationTarget.CUMULATIVE_EXECUTION, "amount", "filledAmount"),
            (ReconciliationTarget.COMMISSION, "commission", "commission"),
            (ReconciliationTarget.TAX, "tax", "tax"),
        ):
            if execution.get(field) is not None:
                facts.append(ReconciliationFact(
                    target, f"{order_id}:{metric}", str(execution[field]), True,
                    str(order.get("currency", "")) or None,
                    str(order.get("instrumentId", order.get("symbol", ""))) or None,
                ))
        facts.append(ReconciliationFact(
            ReconciliationTarget.SETTLEMENT_DATE, order_id,
            str(execution.get("settlementDate") or "NONE"), False,
        ))

    sellable = _rows(payload.get("sellable_quantities"), "sellable_quantities")
    for row in sellable:
        symbol = _required_text(row, "symbol", "sellable_quantities")
        facts.append(ReconciliationFact(
            ReconciliationTarget.SELLABLE_QUANTITY, symbol,
            _required_text(row, "sellableQuantity", "sellable_quantities"),
            True, None, symbol,
        ))
    if not sellable:
        facts.append(_text_fact(ReconciliationTarget.SELLABLE_QUANTITY, "*", []))
    for row in _rows(payload.get("buying_power"), "buying_power"):
        currency = _required_text(row, "currency", "buying_power").upper()
        facts.append(ReconciliationFact(
            ReconciliationTarget.BUYING_POWER, currency,
            _required_text(row, "cashBuyingPower", "buying_power"), True, currency,
        ))
    return tuple(facts)


def _rows(value: object, field: str) -> tuple[dict, ...]:
    if not isinstance(value, (list, tuple)) or not all(isinstance(row, dict) for row in value):
        raise ReconciliationError(f"account snapshot {field} is invalid")
    return tuple(value)


def _required_text(row: dict, key: str, owner: str) -> str:
    value = row.get(key)
    if value is None or str(value) == "":
        raise ReconciliationError(f"account snapshot {owner}.{key} is missing")
    return str(value)


def _text_fact(
    target: ReconciliationTarget, dimension_key: str, value: object
) -> ReconciliationFact:
    return ReconciliationFact(target, dimension_key, _canonical(value), False)


def _fact_map(facts: Iterable[ReconciliationFact], owner: str):
    result = {}
    for fact in facts:
        key = (fact.target, fact.dimension_key)
        if key in result:
            raise ReconciliationError(f"duplicate {owner} reconciliation fact: {key}")
        result[key] = fact
    return result


def _decimal(value: object, field: str) -> Decimal:
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise ReconciliationError(f"invalid {field}") from None
    if not result.is_finite():
        raise ReconciliationError(f"{field} must be finite")
    return result


def _item_payload(item: ReconciliationItem) -> dict[str, object]:
    return {
        "target": item.target.value, "dimension_key": item.dimension_key,
        "broker_value": item.broker_value, "internal_value": item.internal_value,
        "difference": format(item.difference, "f") if item.difference is not None else None,
        "tolerance": format(item.tolerance, "f") if item.tolerance is not None else None,
        "status": item.status.value, "action_required": item.action_required,
    }


def _canonical(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _hash(payload: str) -> str:
    return sha256(payload.encode("utf-8")).hexdigest()


def _utc(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat()


__all__ = [
    "AccountReconciler", "ReconciliationFact", "ReconciliationItem",
    "ReconciliationPolicy", "ReconciliationRun", "ReconciliationStatus",
    "ReconciliationTarget", "ToleranceRule", "TradeGate",
]

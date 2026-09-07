"""Durable submission protocol. Transport injection does not enable a live adapter."""
from dataclasses import dataclass
from datetime import datetime
import json
import sqlite3

from asset_management.data.immutable import canonical, digest
from asset_management.domain.errors import DataQualityError
from asset_management.time.timezone import utc as utc_stamp
from .planner import PlannedOrderIntent


@dataclass(frozen=True)
class SubmissionReceipt:
    client_order_id: str
    state: str
    reason: str


class SubmissionJournal:
    """Dedicated connection: claim commits before the only permitted submit attempt."""

    def __init__(self, path):
        self.conn = sqlite3.connect(path)
        self.conn.execute('PRAGMA synchronous=FULL')
        self.conn.executescript('''
            CREATE TABLE IF NOT EXISTS submission (
                client TEXT PRIMARY KEY, economic_key TEXT UNIQUE NOT NULL,
                account TEXT NOT NULL, payload TEXT NOT NULL, payload_hash TEXT NOT NULL,
                started TEXT NOT NULL, expires TEXT NOT NULL, state TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS submission_audit (
                sequence INTEGER PRIMARY KEY AUTOINCREMENT, client TEXT NOT NULL,
                state TEXT NOT NULL, reason TEXT NOT NULL, evidence TEXT NOT NULL);
            CREATE TRIGGER IF NOT EXISTS submission_identity_no_update
                BEFORE UPDATE OF client,economic_key,account,payload,payload_hash,started,expires ON submission
                BEGIN SELECT RAISE(ABORT, 'immutable submission identity'); END;
            CREATE TRIGGER IF NOT EXISTS submission_no_delete BEFORE DELETE ON submission
                BEGIN SELECT RAISE(ABORT, 'immutable submission'); END;
            CREATE TRIGGER IF NOT EXISTS audit_no_update BEFORE UPDATE ON submission_audit
                BEGIN SELECT RAISE(ABORT, 'immutable audit'); END;
            CREATE TRIGGER IF NOT EXISTS audit_no_delete BEFORE DELETE ON submission_audit
                BEGIN SELECT RAISE(ABORT, 'immutable audit'); END;
        ''')

    def close(self):
        self.conn.close()

    def _record(self, client, state, reason, evidence):
        self.conn.execute('UPDATE submission SET state=? WHERE client=?', (state, client))
        self.conn.execute('INSERT INTO submission_audit(client,state,reason,evidence) VALUES (?,?,?,?)',
                          (client, state, reason, canonical(evidence).decode()))
        return SubmissionReceipt(client, state, reason)

    def _row(self, client):
        row = self.conn.execute('SELECT * FROM submission WHERE client=?', (client,)).fetchone()
        if row is None:
            raise DataQualityError('SUBMISSION_NOT_FOUND')
        if digest(row[3].encode()) != row[4]:
            raise DataQualityError('SUBMISSION_PAYLOAD_CORRUPT')
        return row

    def submit_once(self, *, intent: PlannedOrderIntent, account: str, at: datetime,
                    expires_at: datetime, submit):
        """The callback receives persisted identity. Retries return state, never re-send."""
        at, expires_at = utc_stamp(at), utc_stamp(expires_at)
        if (not isinstance(intent, PlannedOrderIntent) or not isinstance(account, str) or
                not account.strip() or at >= expires_at or not callable(submit)):
            raise DataQualityError('SUBMISSION_INPUT_INVALID')
        payload = dict(account=account, **intent.payload())
        encoded = canonical(payload).decode()
        client = intent.client_order_id
        source = intent.source_order_intent
        # Stable across quote refreshes and risk-decision revisions of the same economic target.
        economic = digest(canonical([account, source.run_id, source.portfolio_target_id,
                                     intent.instrument_id, intent.side.value]))
        self.conn.execute('BEGIN IMMEDIATE')
        try:
            existing = self.conn.execute('SELECT client FROM submission WHERE client=? OR economic_key=?',
                                         (client, economic)).fetchone()
            if existing:
                row = self._row(existing[0])
                if row[0] != client or row[3] != encoded:
                    raise DataQualityError('ECONOMIC_ORDER_CONFLICT')
                self.conn.commit()
                return SubmissionReceipt(client, row[7], 'ALREADY_RECORDED_NO_RESEND')
            unresolved = self.conn.execute("SELECT 1 FROM submission WHERE account=? AND state IN "
                                           "('SUBMITTING','UNKNOWN_BROKER_STATE')", (account,)).fetchone()
            if unresolved:
                raise DataQualityError('ACCOUNT_SUBMISSION_UNRESOLVED')
            self.conn.execute('INSERT INTO submission VALUES (?,?,?,?,?,?,?,?)',
                              (client, economic, account, encoded, digest(encoded.encode()),
                               at.isoformat(), expires_at.isoformat(), 'SUBMITTING'))
            self._record(client, 'SUBMITTING', 'DURABLE_BEFORE_SUBMIT', {'at': at.isoformat()})
            self.conn.commit()
        except BaseException:
            self.conn.rollback()
            raise
        try:
            response = submit(json.loads(encoded))
            # ACK alone cannot establish fill/account truth; retain it for reconciliation.
            safe_response = response if isinstance(response, dict) else {'invalid_response': True}
            safe_response = {'response_hash': digest(canonical(safe_response))}
        except Exception:
            safe_response = {'transport_unavailable': True}
        with self.conn:
            return self._record(client, 'UNKNOWN_BROKER_STATE', 'RECONCILIATION_REQUIRED', safe_response)

    def mark_ambiguous(self, client, *, operation: str):
        """Call before cancel/replace transport; a crash leaves an unresolved durable record."""
        if operation not in ('CANCEL', 'REPLACE', 'BOOT_RECOVERY'):
            raise DataQualityError('RECOVERY_OPERATION_INVALID')
        self._row(client)
        with self.conn:
            return self._record(client, 'UNKNOWN_BROKER_STATE', operation + '_RECONCILIATION_REQUIRED', {})

    def recover(self, client, *, lookup):
        """Read-only lookup supplies matched broker, snapshot and reconciliation evidence."""
        row = self._row(client)
        if row[7] not in ('SUBMITTING', 'UNKNOWN_BROKER_STATE'):
            return SubmissionReceipt(client, row[7], 'ALREADY_RECONCILED')
        payload = json.loads(row[3])
        query = dict(client_order_id=client, account=row[2], instrument_id=payload['instrument_id'],
                     quantity=payload['quantity'], side=payload['side'],
                     started_at=row[5], expires_at=row[6])
        try:
            evidence = lookup(query)
        except Exception:
            evidence = None
        reason, state = 'BROKER_LOOKUP_UNAVAILABLE_OR_UNPROVEN', 'UNKNOWN_BROKER_STATE'
        if isinstance(evidence, dict):
            match = all(evidence.get(k) == v for k, v in query.items())
            proof = all(isinstance(evidence.get(k), str) and evidence[k].strip()
                        for k in ('broker_order_id', 'snapshot_id', 'reconciliation_id', 'source_response_id'))
            status = evidence.get('status')
            if match and proof and evidence.get('reconciled') is True and status in (
                    'ACKNOWLEDGED', 'OPEN', 'PARTIALLY_FILLED', 'FILLED', 'CANCELED', 'REJECTED'):
                if status in ('PARTIALLY_FILLED', 'FILLED') and not evidence.get('fill_ledger_verified') is True:
                    reason = 'FILL_LEDGER_RECONCILIATION_REQUIRED'
                else:
                    state, reason = status, 'BROKER_STATE_RECONCILED'
            else:
                reason = 'BROKER_EVIDENCE_CONFLICT_OR_INCOMPLETE'
        # Persist identifiers only; untrusted transport data may contain credentials.
        audit = {k: evidence[k] for k in ('snapshot_id', 'reconciliation_id', 'source_response_id')
                 if isinstance(evidence, dict) and isinstance(evidence.get(k), str)}
        self.conn.execute('BEGIN IMMEDIATE')
        try:
            current = self._row(client)
            if current[7] != row[7]:
                self.conn.commit()
                return SubmissionReceipt(client, current[7], 'RECOVERY_STATE_CHANGED_REQUERY')
            result = self._record(client, state, reason, audit)
            self.conn.commit()
            return result
        except BaseException:
            self.conn.rollback()
            raise

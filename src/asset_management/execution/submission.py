"""Durable submission protocol. Transport injection does not enable a live adapter."""
from dataclasses import dataclass
from datetime import datetime
import json
import sqlite3

from asset_management.data.immutable import canonical, digest
from asset_management.data.raw_store import SQLiteRawResponseStore
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

    def __init__(self, path, *, authority_conn: sqlite3.Connection | None = None):
        self.conn = sqlite3.connect(path)
        self.authority_conn = authority_conn
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
            safe_response = response if isinstance(response, dict) else {'invalid_response': True}
            safe_response = {'response_hash': digest(canonical(safe_response))}
        except Exception:
            safe_response = {'transport_unavailable': True}
        with self.conn:
            return self._record(client, 'UNKNOWN_BROKER_STATE', 'RECONCILIATION_REQUIRED', safe_response)

    def mark_ambiguous(self, client, *, operation: str, at: datetime):
        """Persist the operation boundary before cancel/replace/recovery transport is attempted."""
        if operation not in ('CANCEL', 'REPLACE', 'BOOT_RECOVERY'):
            raise DataQualityError('RECOVERY_OPERATION_INVALID')
        instant = utc_stamp(at)
        self.conn.execute('BEGIN IMMEDIATE')
        try:
            row = self._row(client)
            if instant < self._recovery_cutoff(client, row):
                raise DataQualityError('RECOVERY_OPERATION_TIME_INVALID')
            result = self._record(
                client, 'UNKNOWN_BROKER_STATE', operation + '_RECONCILIATION_REQUIRED',
                {'ambiguous_at': instant.isoformat(), 'operation': operation},
            )
            self.conn.commit()
            return result
        except BaseException:
            self.conn.rollback()
            raise

    def _audit_sequence(self, client: str) -> int:
        return self.conn.execute(
            'SELECT coalesce(max(sequence), 0) FROM submission_audit WHERE client=?', (client,)
        ).fetchone()[0]

    def _recovery_cutoff(self, client: str, row) -> datetime:
        """Newest durable ambiguity boundary; evidence before it cannot clear recovery."""
        cutoff = utc_stamp(datetime.fromisoformat(str(row[5])))
        audits = self.conn.execute(
            'SELECT evidence FROM submission_audit WHERE client=? ORDER BY sequence DESC', (client,)
        ).fetchall()
        for (encoded,) in audits:
            try:
                evidence = json.loads(encoded)
                marker = evidence.get('ambiguous_at') if isinstance(evidence, dict) else None
                if isinstance(marker, str):
                    instant = utc_stamp(datetime.fromisoformat(marker))
                    if instant > cutoff:
                        cutoff = instant
                    # Inspect all markers: older journals may contain backdated later events.
            except (TypeError, ValueError, json.JSONDecodeError):
                continue
        return cutoff

    def _authority_verified(self, *, query: dict[str, str], evidence: dict[str, object],
                            order_intent_id: str, freshness_cutoff: datetime) -> tuple[bool, str]:
        """Verify candidate IDs against append-only authority and post-operation freshness."""
        authority = self.authority_conn
        if authority is None:
            return False, 'RECOVERY_AUTHORITY_UNAVAILABLE'
        required = ('broker_order_id', 'snapshot_id', 'reconciliation_id', 'source_response_id')
        if any(not isinstance(evidence.get(name), str) or not str(evidence[name]).strip()
               for name in required):
            return False, 'BROKER_EVIDENCE_CONFLICT_OR_INCOMPLETE'
        if not all(evidence.get(key) == value for key, value in query.items()):
            return False, 'BROKER_EVIDENCE_CONFLICT_OR_INCOMPLETE'
        broker_order_id = str(evidence['broker_order_id'])
        snapshot_id = str(evidence['snapshot_id'])
        reconciliation_id = str(evidence['reconciliation_id'])
        source_response_id = str(evidence['source_response_id'])
        status = evidence.get('status')
        allowed = {'ACKNOWLEDGED', 'OPEN', 'PARTIALLY_FILLED', 'FILLED', 'CANCELED', 'REJECTED'}
        if status not in allowed:
            return False, 'BROKER_EVIDENCE_CONFLICT_OR_INCOMPLETE'
        try:
            client_row = authority.execute(
                'SELECT order_intent_id FROM am_client_order WHERE client_order_id=?',
                (query['client_order_id'],),
            ).fetchone()
            link_row = authority.execute(
                'SELECT broker_order_id FROM am_order_link WHERE client_order_id=?',
                (query['client_order_id'],),
            ).fetchone()
            broker_row = authority.execute(
                'SELECT account_id FROM am_broker_order WHERE broker_order_id=?',
                (broker_order_id,),
            ).fetchone()
            state_row = authority.execute(
                '''SELECT state, source_response_id, observed_at_utc FROM am_order_state_event
                   WHERE broker_order_id=? ORDER BY sequence_no DESC LIMIT 1''',
                (broker_order_id,),
            ).fetchone()
            recon_row = authority.execute(
                '''SELECT account_snapshot_id, account_id, status, completed_at_utc
                   FROM am_account_reconciliation_v2 WHERE reconciliation_run_id=?''',
                (reconciliation_id,),
            ).fetchone()
            newest_recon = authority.execute(
                '''SELECT reconciliation_run_id FROM am_account_reconciliation_v2
                   WHERE account_id=? ORDER BY completed_at_utc DESC, reconciliation_run_id DESC LIMIT 1''',
                (query['account'],),
            ).fetchone()
            snapshot_row = authority.execute(
                'SELECT account_id, observed_at_utc FROM am_account_snapshot WHERE account_snapshot_id=?',
                (snapshot_id,),
            ).fetchone()
            raw_link = authority.execute(
                '''SELECT 1 FROM am_account_snapshot_raw
                   WHERE account_snapshot_id=? AND raw_response_id=?''',
                (snapshot_id, source_response_id),
            ).fetchone()
            bad_item = authority.execute(
                '''SELECT 1 FROM am_reconciliation_item_v2
                   WHERE reconciliation_run_id=? AND status IN ('MISMATCH','UNVERIFIABLE','BLOCKED') LIMIT 1''',
                (reconciliation_id,),
            ).fetchone()
            open_issue = authority.execute(
                '''SELECT 1 FROM am_reconciliation_issue_v2 issue
                   LEFT JOIN am_reconciliation_resolution_v2 resolution USING(issue_id)
                   WHERE issue.account_id=? AND resolution.issue_id IS NULL LIMIT 1''',
                (query['account'],),
            ).fetchone()
            raw = SQLiteRawResponseStore(authority).verified(source_response_id)
            recon_completed = utc_stamp(datetime.fromisoformat(str(recon_row[3]))) if recon_row is not None else None
            state_observed = utc_stamp(datetime.fromisoformat(str(state_row[2]))) if state_row is not None else None
            snapshot_observed = utc_stamp(datetime.fromisoformat(str(snapshot_row[1]))) if snapshot_row is not None else None
            requested_at, received_at = utc_stamp(raw.requested_at), utc_stamp(raw.received_at)
        except (sqlite3.Error, KeyError, ValueError, TypeError):
            return False, 'RECOVERY_AUTHORITY_UNAVAILABLE_OR_INVALID'
        if (client_row is None or str(client_row[0]) != order_intent_id or
                link_row is None or str(link_row[0]) != broker_order_id or
                broker_row is None or str(broker_row[0]) != query['account'] or
                state_row is None or str(state_row[0]) != status or str(state_row[1]) != source_response_id or
                recon_row is None or str(recon_row[0]) != snapshot_id or
                str(recon_row[1]) != query['account'] or str(recon_row[2]) not in {'MATCH', 'TOLERANCE_MATCH'} or
                newest_recon is None or str(newest_recon[0]) != reconciliation_id or
                snapshot_row is None or str(snapshot_row[0]) != query['account'] or raw_link is None or
                bad_item is not None or open_issue is not None or recon_completed is None or
                state_observed is None or snapshot_observed is None or
                requested_at < freshness_cutoff or received_at < requested_at or
                state_observed < freshness_cutoff or snapshot_observed < freshness_cutoff or
                recon_completed < max(freshness_cutoff, received_at, state_observed, snapshot_observed) or
                (raw.account_id is not None and raw.account_id != query['account'])):
            return False, 'BROKER_EVIDENCE_CONFLICT_OR_STALE'
        if status in {'PARTIALLY_FILLED', 'FILLED'}:
            try:
                posting = authority.execute(
                    '''SELECT 1 FROM am_execution_posting posting
                       JOIN am_execution_delta delta USING(execution_delta_id)
                       JOIN am_execution_snapshot snap ON snap.execution_snapshot_id=delta.to_snapshot_id
                       WHERE snap.broker_order_id=? LIMIT 1''',
                    (broker_order_id,),
                ).fetchone()
            except sqlite3.Error:
                return False, 'FILL_LEDGER_RECONCILIATION_REQUIRED'
            if posting is None:
                return False, 'FILL_LEDGER_RECONCILIATION_REQUIRED'
        return True, 'BROKER_STATE_RECONCILED'

    def recover(self, client, *, lookup):
        """Lookup discovers candidates; append-only ledger authority proves the recovered state."""
        row = self._row(client)
        if row[7] not in ('SUBMITTING', 'UNKNOWN_BROKER_STATE'):
            return SubmissionReceipt(client, row[7], 'ALREADY_RECONCILED')
        payload = json.loads(row[3])
        query = dict(client_order_id=client, account=row[2], instrument_id=payload['instrument_id'],
                     quantity=payload['quantity'], side=payload['side'],
                     started_at=row[5], expires_at=row[6])
        audit_sequence = self._audit_sequence(client)
        freshness_cutoff = self._recovery_cutoff(client, row)
        try:
            evidence = lookup(query)
        except Exception:
            evidence = None
        state, reason = 'UNKNOWN_BROKER_STATE', 'BROKER_LOOKUP_UNAVAILABLE_OR_UNPROVEN'
        if isinstance(evidence, dict):
            verified, reason = self._authority_verified(
                query=query, evidence=evidence, order_intent_id=payload['order_intent_id'],
                freshness_cutoff=freshness_cutoff,
            )
            if verified:
                state = str(evidence['status'])
        audit = {k: evidence[k] for k in ('broker_order_id', 'snapshot_id', 'reconciliation_id',
                                           'source_response_id')
                 if isinstance(evidence, dict) and isinstance(evidence.get(k), str)}
        audit['freshness_cutoff'] = freshness_cutoff.isoformat()
        self.conn.execute('BEGIN IMMEDIATE')
        try:
            current = self._row(client)
            # A new CANCEL/REPLACE can retain UNKNOWN; compare generation too.
            if current[7] != row[7] or self._audit_sequence(client) != audit_sequence:
                self.conn.commit()
                return SubmissionReceipt(client, current[7], 'RECOVERY_STATE_CHANGED_REQUERY')
            result = self._record(client, state, reason, audit)
            self.conn.commit()
            return result
        except BaseException:
            self.conn.rollback()
            raise

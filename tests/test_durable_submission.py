from datetime import timedelta
from dataclasses import replace
import hashlib
import json
from pathlib import Path
import sqlite3
import pytest
from test_phase_m6_order_intent_planner import kwargs, NOW
from asset_management.execution.planner import plan_order_intents
from asset_management.execution.submission import SubmissionJournal
from asset_management.domain.errors import DataQualityError


def args():
    return dict(intent=plan_order_intents(**kwargs())[0], account='paper:1', at=NOW,
                expires_at=NOW+timedelta(minutes=1))


def proof(query, status='OPEN', **changes):
    return query | dict(status=status, broker_order_id='broker:1', snapshot_id='snapshot:1',
                        reconciliation_id='reconcile:1', source_response_id='raw:1', reconciled=True) | changes


def _raw_hash(body):
    encoded=json.dumps(body,ensure_ascii=False,sort_keys=True,separators=(',',':'))
    return hashlib.sha256(encoded.encode()).hexdigest(), encoded


def authority(status='OPEN'):
    conn=sqlite3.connect(':memory:')
    conn.executescript('''
        CREATE TABLE am_client_order(client_order_id TEXT PRIMARY KEY, order_intent_id TEXT NOT NULL);
        CREATE TABLE am_order_link(client_order_id TEXT PRIMARY KEY, broker_order_id TEXT NOT NULL);
        CREATE TABLE am_broker_order(broker_order_id TEXT PRIMARY KEY, account_id TEXT NOT NULL);
        CREATE TABLE am_order_state_event(broker_order_id TEXT, sequence_no INTEGER, state TEXT, source_response_id TEXT, observed_at_utc TEXT);
        CREATE TABLE am_account_reconciliation_v2(reconciliation_run_id TEXT PRIMARY KEY, account_snapshot_id TEXT, account_id TEXT, status TEXT, completed_at_utc TEXT);
        CREATE TABLE am_account_snapshot(account_snapshot_id TEXT PRIMARY KEY, account_id TEXT, observed_at_utc TEXT);
        CREATE TABLE am_account_snapshot_raw(account_snapshot_id TEXT, raw_response_id TEXT);
        CREATE TABLE am_reconciliation_item_v2(reconciliation_run_id TEXT, status TEXT);
        CREATE TABLE am_reconciliation_issue_v2(issue_id TEXT, account_id TEXT);
        CREATE TABLE am_reconciliation_resolution_v2(issue_id TEXT);
        CREATE TABLE am_raw_api_response(raw_response_id TEXT PRIMARY KEY, source TEXT, endpoint TEXT, http_method TEXT, request_hash TEXT, status_code INTEGER, response_hash TEXT, body_json TEXT, requested_at_utc TEXT, received_at_utc TEXT, account_id TEXT, schema_version TEXT, headers_json TEXT);
        CREATE TABLE am_execution_snapshot(execution_snapshot_id TEXT PRIMARY KEY, broker_order_id TEXT);
        CREATE TABLE am_execution_delta(execution_delta_id TEXT PRIMARY KEY, to_snapshot_id TEXT);
        CREATE TABLE am_execution_posting(execution_delta_id TEXT PRIMARY KEY);
    ''')
    intent=args()['intent']
    conn.execute('INSERT INTO am_client_order VALUES (?,?)',(intent.client_order_id,intent.order_intent_id))
    conn.execute('INSERT INTO am_order_link VALUES (?,?)',(intent.client_order_id,'broker:1'))
    conn.execute('INSERT INTO am_broker_order VALUES (?,?)',('broker:1','paper:1'))
    _append_authority_snapshot(conn,status,NOW,sequence=1,suffix='1')
    conn.commit()
    return conn


def _append_authority_snapshot(conn, status, at, *, sequence, suffix):
    raw_id=f'raw:{suffix}'; snapshot_id=f'snapshot:{suffix}'; reconciliation_id=f'reconcile:{suffix}'
    conn.execute('INSERT INTO am_order_state_event VALUES (?,?,?,?,?)',('broker:1',sequence,status,raw_id,at.isoformat()))
    conn.execute('INSERT INTO am_account_reconciliation_v2 VALUES (?,?,?,?,?)',
                 (reconciliation_id,snapshot_id,'paper:1','MATCH',at.isoformat()))
    conn.execute('INSERT INTO am_account_snapshot VALUES (?,?,?)',(snapshot_id,'paper:1',at.isoformat()))
    conn.execute('INSERT INTO am_account_snapshot_raw VALUES (?,?)',(snapshot_id,raw_id))
    body_hash,body_json=_raw_hash({'status':status})
    conn.execute('INSERT INTO am_raw_api_response VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)',
                 (raw_id,'toss','/api/v1/orders/broker:1','GET',f'request:{suffix}',200,body_hash,body_json,
                  at.isoformat(),at.isoformat(),'paper:1','1.2.15','{}'))
    conn.commit()
    return dict(snapshot_id=snapshot_id,reconciliation_id=reconciliation_id,source_response_id=raw_id)


def seed_fill_posting(conn):
    conn.execute('INSERT INTO am_execution_snapshot VALUES (?,?)',('execution-snapshot:1','broker:1'))
    conn.execute('INSERT INTO am_execution_delta VALUES (?,?)',('execution-delta:1','execution-snapshot:1'))
    conn.execute('INSERT INTO am_execution_posting VALUES (?)',('execution-delta:1',))
    conn.commit()


def test_commit_precedes_transport_and_repeated_submission_never_resends(tmp_path):
    path = tmp_path/'submissions.db'
    ledger=authority('OPEN')
    journal = SubmissionJournal(path,authority_conn=ledger)
    calls = []
    def submit(payload):
        reader = SubmissionJournal(path)
        assert reader._row(payload['client_order_id'])[7] == 'SUBMITTING'
        reader.close()
        calls.append(payload)
        return {'status':'OPEN'}
    receipt = journal.submit_once(**args(), submit=submit)
    assert receipt.state == 'UNKNOWN_BROKER_STATE'
    journal.submit_once(**args(), submit=submit)
    journal.close()
    journal = SubmissionJournal(path,authority_conn=ledger)
    journal.submit_once(**args(), submit=submit)
    assert len(calls) == 1
    assert journal.recover(receipt.client_order_id, lookup=proof).state == 'OPEN'
    journal.close(); ledger.close()


@pytest.mark.parametrize('failure', [TimeoutError, SystemExit])
def test_timeout_or_process_crash_requires_lookup_before_any_retry(tmp_path, failure):
    journal = SubmissionJournal(tmp_path/'s.db')
    def broken(payload):
        raise failure()
    if failure is SystemExit:
        with pytest.raises(SystemExit):
            journal.submit_once(**args(), submit=broken)
    else:
        journal.submit_once(**args(), submit=broken)
    client = args()['intent'].client_order_id
    result = journal.recover(client, lookup=lambda _: None)
    assert result.state == 'UNKNOWN_BROKER_STATE'
    result = journal.submit_once(**args(), submit=lambda _: pytest.fail('duplicate transport'))
    assert result.reason == 'ALREADY_RECORDED_NO_RESEND'
    journal.close()


def test_fabricated_lookup_strings_cannot_reconcile_without_persisted_authority(tmp_path):
    journal=SubmissionJournal(tmp_path/'s.db')
    receipt=journal.submit_once(**args(),submit=lambda _: {})
    result=journal.recover(receipt.client_order_id,lookup=proof)
    assert result.state=='UNKNOWN_BROKER_STATE'
    assert result.reason=='RECOVERY_AUTHORITY_UNAVAILABLE'
    journal.close()


def test_changed_payload_and_account_risk_fail_closed(tmp_path):
    journal = SubmissionJournal(tmp_path/'s.db')
    journal.submit_once(**args(), submit=lambda _: {})
    current=kwargs()['current_quantities'] | {'SPY':kwargs()['current_quantities']['SPY']*0}
    changed = plan_order_intents(**kwargs(current_quantities=current))[0]
    with pytest.raises(DataQualityError, match='ECONOMIC_ORDER_CONFLICT'):
        journal.submit_once(**(args() | {'intent':changed}), submit=lambda _: pytest.fail())
    journal.close()


@pytest.mark.parametrize('status', ['PARTIALLY_FILLED','FILLED'])
def test_fills_and_cancel_timeout_need_post_operation_reconciliation(tmp_path, status):
    ledger=authority(status)
    journal = SubmissionJournal(tmp_path/'s.db',authority_conn=ledger)
    result = journal.submit_once(**args(), submit=lambda _: {'status':status})
    client = result.client_order_id
    first=journal.recover(client, lookup=lambda q: proof(q,status,fill_ledger_verified=True))
    assert first.state == 'UNKNOWN_BROKER_STATE'
    assert first.reason == 'FILL_LEDGER_RECONCILIATION_REQUIRED'
    seed_fill_posting(ledger)
    assert journal.recover(client, lookup=lambda q: proof(q,status,fill_ledger_verified=True)).state == status

    ambiguous_at=NOW+timedelta(seconds=10)
    assert journal.mark_ambiguous(client, operation='CANCEL', at=ambiguous_at).state == 'UNKNOWN_BROKER_STATE'
    stale=journal.recover(client, lookup=lambda q: proof(q,status,fill_ledger_verified=True))
    assert stale.state == 'UNKNOWN_BROKER_STATE'
    assert stale.reason == 'BROKER_EVIDENCE_CONFLICT_OR_STALE'
    assert journal.recover(client, lookup=lambda q: proof(q,account='other')).state == 'UNKNOWN_BROKER_STATE'

    fresh_at=ambiguous_at+timedelta(seconds=1)
    fresh_ids=_append_authority_snapshot(ledger,status,fresh_at,sequence=2,suffix='2')
    fresh=journal.recover(client, lookup=lambda q: proof(q,status,fill_ledger_verified=True,**fresh_ids))
    assert fresh.state == status
    journal.close(); ledger.close()


def test_schema_matches_receipt():
    from asset_management.execution.submission import SubmissionReceipt
    schema = json.loads((Path(__file__).parents[1]/'schemas/submission_receipt.schema.json').read_text())
    assert set(schema['required']) == set(SubmissionReceipt.__dataclass_fields__)


def test_lookup_unavailable_keeps_audit_and_identity_is_immutable(tmp_path):
    journal = SubmissionJournal(tmp_path/'s.db')
    receipt = journal.submit_once(**args(), submit=lambda _: {})
    def unavailable(query):
        raise TimeoutError()
    assert journal.recover(receipt.client_order_id, lookup=unavailable).state == 'UNKNOWN_BROKER_STATE'
    with pytest.raises(sqlite3.IntegrityError):
        journal.conn.execute("UPDATE submission SET payload='{}'")
    assert journal.conn.execute('SELECT count(*) FROM submission_audit').fetchone()[0] == 3
    journal.close()

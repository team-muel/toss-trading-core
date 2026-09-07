from datetime import timedelta
from dataclasses import replace
import json
from pathlib import Path
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


def test_commit_precedes_transport_and_repeated_submission_never_resends(tmp_path):
    path = tmp_path/'submissions.db'
    journal = SubmissionJournal(path)
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
    journal = SubmissionJournal(path)
    journal.submit_once(**args(), submit=submit)
    assert len(calls) == 1
    assert journal.recover(receipt.client_order_id, lookup=proof).state == 'OPEN'
    journal.close()


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


def test_changed_payload_and_account_risk_fail_closed(tmp_path):
    journal = SubmissionJournal(tmp_path/'s.db')
    journal.submit_once(**args(), submit=lambda _: {})
    changed = plan_order_intents(**kwargs(current_quantities={'SPY':kwargs()['current_quantities']['SPY']*0}))[0]
    with pytest.raises(DataQualityError, match='ECONOMIC_ORDER_CONFLICT'):
        journal.submit_once(**(args() | {'intent':changed}), submit=lambda _: pytest.fail())
    journal.close()


@pytest.mark.parametrize('status', ['PARTIALLY_FILLED','FILLED'])
def test_fills_and_cancel_timeout_need_reconciliation(tmp_path, status):
    journal = SubmissionJournal(tmp_path/'s.db')
    result = journal.submit_once(**args(), submit=lambda _: {'status':status})
    client = result.client_order_id
    assert journal.recover(client, lookup=lambda q: proof(q,status)).state == 'UNKNOWN_BROKER_STATE'
    assert journal.recover(client, lookup=lambda q: proof(q,status,fill_ledger_verified=True)).state == status
    assert journal.mark_ambiguous(client, operation='CANCEL').state == 'UNKNOWN_BROKER_STATE'
    assert journal.recover(client, lookup=lambda q: proof(q,account='other')).state == 'UNKNOWN_BROKER_STATE'
    journal.close()


def test_schema_matches_receipt():
    from asset_management.execution.submission import SubmissionReceipt
    schema = json.loads((Path(__file__).parents[1]/'schemas/submission_receipt.schema.json').read_text())
    assert set(schema['required']) == set(SubmissionReceipt.__dataclass_fields__)


def test_lookup_unavailable_keeps_audit_and_identity_is_immutable(tmp_path):
    import sqlite3
    journal = SubmissionJournal(tmp_path/'s.db')
    receipt = journal.submit_once(**args(), submit=lambda _: {})
    def unavailable(query):
        raise TimeoutError()
    assert journal.recover(receipt.client_order_id, lookup=unavailable).state == 'UNKNOWN_BROKER_STATE'
    with pytest.raises(sqlite3.IntegrityError):
        journal.conn.execute("UPDATE submission SET payload='{}'")
    assert journal.conn.execute('SELECT count(*) FROM submission_audit').fetchone()[0] == 3
    journal.close()

import tempfile
import unittest
import sqlite3
from pathlib import Path

from toss_trading.account import AccountLedger, FoundationSnapshotter
from toss_trading.cli.foundation_audit import audit_foundation_db
from toss_trading.data import load_instrument_mappings

from test_foundation_account_state import FakeTossAdapter


class FoundationAuditTest(unittest.TestCase):
    def test_audit_reports_incompatible_legacy_schema_without_traceback(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "legacy.sqlite"
            conn = sqlite3.connect(db_path)
            conn.execute("CREATE TABLE raw_api_response (id TEXT PRIMARY KEY)")
            conn.commit()
            conn.close()

            result = audit_foundation_db(db_path=db_path)

            self.assertFalse(result.ok)
            self.assertIn("incompatible_foundation_schema", result.as_text())
            self.assertIn("snapshot_run", result.as_text())

    def test_audit_passes_complete_foundation_snapshot(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "foundation.sqlite"
            ledger = AccountLedger(db_path)
            ledger.init_schema()
            ledger.load_instrument_mappings(
                load_instrument_mappings("data/instrument_master.csv")
            )
            ledger.save_raw_api_response(
                source="toss",
                source_type="broker",
                endpoint="/oauth2/token",
                http_method="POST",
                body={"access_token": "redacted"},
                status_code=200,
            )
            fake = FakeTossAdapter()
            fake.ledger = ledger
            FoundationSnapshotter(fake, ledger).snapshot(account_seq="1")
            ledger.close()

            result = audit_foundation_db(db_path=db_path)

            self.assertTrue(result.ok, result.as_text())
            self.assertIn("foundation_audit=ok", result.lines[0])

    def test_v1_audit_passes_funded_read_only_evidence(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "foundation.sqlite"
            ledger = AccountLedger(db_path)
            ledger.init_schema()
            ledger.load_instrument_mappings(
                load_instrument_mappings("data/instrument_master.csv")
            )
            ledger.save_raw_api_response(
                source="toss",
                source_type="broker",
                endpoint="/oauth2/token",
                http_method="POST",
                body={"access_token": "redacted"},
                status_code=200,
            )
            fake = FakeTossAdapter()
            fake.ledger = ledger
            FoundationSnapshotter(fake, ledger).snapshot(
                account_seq="1",
                target_order_id="closed-1",
            )
            ledger.close()

            result = audit_foundation_db(
                db_path=db_path,
                profile="v1-funded-read-only",
            )

            self.assertTrue(result.ok, result.as_text())
            self.assertIn("profile=v1-funded-read-only", result.as_text())

    def test_v1_audit_rejects_empty_account_evidence(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "foundation.sqlite"
            ledger = AccountLedger(db_path)
            ledger.init_schema()
            ledger.load_instrument_mappings(
                load_instrument_mappings("data/instrument_master.csv")
            )
            class EmptyFakeTossAdapter(FakeTossAdapter):
                def get_holdings(self):
                    return self._raw("/api/v1/holdings", {"result": {"items": []}})

            fake = EmptyFakeTossAdapter()
            fake.ledger = ledger
            FoundationSnapshotter(fake, ledger).snapshot(account_seq="1")
            ledger.close()

            result = audit_foundation_db(
                db_path=db_path,
                profile="v1-funded-read-only",
            )

            self.assertFalse(result.ok)
            self.assertIn("v1_requires_nonzero_holdings", result.as_text())
            self.assertIn("v1_requires_target_order_id", result.as_text())

    def test_v1_audit_ignores_commission_rows_without_amount(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "foundation.sqlite"
            ledger = AccountLedger(db_path)
            ledger.init_schema()
            ledger.load_instrument_mappings(
                load_instrument_mappings("data/instrument_master.csv")
            )
            ledger.save_raw_api_response(
                source="toss",
                source_type="broker",
                endpoint="/oauth2/token",
                http_method="POST",
                body={"access_token": "redacted"},
                status_code=200,
            )
            fake = FakeTossAdapter()
            fake.ledger = ledger
            FoundationSnapshotter(fake, ledger).snapshot(
                account_seq="1",
                target_order_id="closed-1",
            )
            ledger.conn.execute(
                """
                UPDATE broker_order_snapshot
                SET cumulative_commission = NULL,
                    cumulative_commission_decimal = NULL
                WHERE account_seq = '1' AND broker_order_id = 'closed-1'
                """
            )
            ledger.conn.commit()
            ledger.close()

            result = audit_foundation_db(
                db_path=db_path,
                profile="v1-funded-read-only",
            )

            self.assertFalse(result.ok)
            self.assertIn("commission_rows=0", result.as_text())
            self.assertIn("v1_requires_execution_commission", result.as_text())

    def test_audit_fails_when_latest_toss_health_is_blocked(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "foundation.sqlite"
            ledger = AccountLedger(db_path)
            ledger.init_schema()
            ledger.load_instrument_mappings(
                load_instrument_mappings("data/instrument_master.csv")
            )
            run_id = ledger.begin_snapshot_run(account_seq="1")
            ledger.record_source_health(
                source="toss",
                channel="rest:/oauth2/token",
                source_status="blocked",
                action="register_current_ip_in_toss_openapi_allowlist",
            )
            ledger.finish_snapshot_run(run_id, account_seq="1")
            ledger.close()

            result = audit_foundation_db(db_path=db_path)

            self.assertFalse(result.ok)
            self.assertIn("latest_source_health_not_ok", result.as_text())

    def test_audit_fails_when_reconciliation_block_exists(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "foundation.sqlite"
            ledger = AccountLedger(db_path)
            ledger.init_schema()
            ledger.load_instrument_mappings(
                load_instrument_mappings("data/instrument_master.csv")
            )
            ledger.save_raw_api_response(
                source="toss",
                source_type="broker",
                endpoint="/oauth2/token",
                http_method="POST",
                body={"access_token": "redacted"},
                status_code=200,
            )
            fake = FakeTossAdapter()
            fake.ledger = ledger
            FoundationSnapshotter(fake, ledger).snapshot(account_seq="1")
            ledger.conn.execute(
                """
                INSERT INTO broker_reconciliation_log (
                  id, ts, account_seq, item_type, broker_value, internal_value,
                  difference, status, action_required, created_at
                ) VALUES (
                  'block-1',
                  (SELECT started_at FROM snapshot_run ORDER BY completed_at DESC LIMIT 1), '1',
                  'execution_snapshot', '{}', '{}',
                  'negative_execution_delta', 'BLOCK',
                  'inspect_order_detail_before_new_orders',
                  (SELECT started_at FROM snapshot_run ORDER BY completed_at DESC LIMIT 1)
                )
                """
            )
            ledger.conn.commit()
            ledger.close()

            result = audit_foundation_db(db_path=db_path)

            self.assertFalse(result.ok)
            self.assertIn("reconciliation_block_rows=1", result.as_text())
            self.assertIn("broker_reconciliation_block:execution_snapshot", result.as_text())
            self.assertIn("negative_execution_delta", result.as_text())

    def test_audit_fails_when_unknown_order_status_exists(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "foundation.sqlite"
            ledger = AccountLedger(db_path)
            ledger.init_schema()
            ledger.load_instrument_mappings(
                load_instrument_mappings("data/instrument_master.csv")
            )
            ledger.save_raw_api_response(
                source="toss",
                source_type="broker",
                endpoint="/oauth2/token",
                http_method="POST",
                body={"access_token": "redacted"},
                status_code=200,
            )
            fake = FakeTossAdapter()
            fake.ledger = ledger
            FoundationSnapshotter(fake, ledger).snapshot(account_seq="1")
            ledger.conn.execute(
                """
                INSERT INTO broker_order_snapshot (
                  id, run_id, ts, account_seq, broker_order_id, symbol, status, created_at
                ) VALUES (
                  'unknown-status-1',
                  (SELECT run_id FROM snapshot_run ORDER BY completed_at DESC LIMIT 1),
                  '2026-07-09T00:00:00+00:00', '1',
                  'order-unknown', 'SPY', 'BROKER_NEW_STATUS',
                  '2026-07-09T00:00:00+00:00'
                )
                """
            )
            ledger.conn.commit()
            ledger.close()

            result = audit_foundation_db(db_path=db_path)

            self.assertFalse(result.ok)
            self.assertIn("unknown_order_statuses=['BROKER_NEW_STATUS']", result.as_text())
            self.assertIn("unknown_order_status:BROKER_NEW_STATUS", result.as_text())

    def test_audit_fails_when_review_order_status_exists(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "foundation.sqlite"
            ledger = AccountLedger(db_path)
            ledger.init_schema()
            ledger.load_instrument_mappings(
                load_instrument_mappings("data/instrument_master.csv")
            )
            ledger.save_raw_api_response(
                source="toss",
                source_type="broker",
                endpoint="/oauth2/token",
                http_method="POST",
                body={"access_token": "redacted"},
                status_code=200,
            )
            fake = FakeTossAdapter()
            fake.ledger = ledger
            FoundationSnapshotter(fake, ledger).snapshot(account_seq="1")
            ledger.conn.execute(
                """
                INSERT INTO broker_order_snapshot (
                  id, run_id, ts, account_seq, broker_order_id, symbol, status, created_at
                ) VALUES (
                  'review-status-1',
                  (SELECT run_id FROM snapshot_run ORDER BY completed_at DESC LIMIT 1),
                  '2026-07-09T00:00:00+00:00', '1',
                  'order-review', 'SPY', 'CANCEL_REJECTED',
                  '2026-07-09T00:00:00+00:00'
                )
                """
            )
            ledger.conn.commit()
            ledger.close()

            result = audit_foundation_db(db_path=db_path)

            self.assertFalse(result.ok)
            self.assertIn("review_order_statuses=['CANCEL_REJECTED']", result.as_text())
            self.assertIn(
                "review_order_status_requires_order_detail:CANCEL_REJECTED",
                result.as_text(),
            )


if __name__ == "__main__":
    unittest.main()

import tempfile
import unittest
from pathlib import Path

from toss_trading.account import AccountLedger, FoundationSnapshotter
from toss_trading.cli.foundation_audit import audit_foundation_db
from toss_trading.data import load_instrument_mappings

from test_foundation_account_state import FakeTossAdapter


class FoundationAuditTest(unittest.TestCase):
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

    def test_audit_fails_when_latest_toss_health_is_blocked(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "foundation.sqlite"
            ledger = AccountLedger(db_path)
            ledger.init_schema()
            ledger.load_instrument_mappings(
                load_instrument_mappings("data/instrument_master.csv")
            )
            ledger.record_source_health(
                source="toss",
                channel="rest:/oauth2/token",
                source_status="blocked",
                action="register_current_ip_in_toss_openapi_allowlist",
            )
            ledger.close()

            result = audit_foundation_db(db_path=db_path)

            self.assertFalse(result.ok)
            self.assertIn("latest_source_health_not_ok", result.as_text())


if __name__ == "__main__":
    unittest.main()

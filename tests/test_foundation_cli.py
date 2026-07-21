import unittest
import sqlite3
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from toss_trading.cli.foundation_snapshot import build_parser
from toss_trading.cli.foundation_snapshot import main


class FoundationCliTest(unittest.TestCase):
    def test_parser_defaults_to_runtime_outputs(self):
        args = build_parser().parse_args([])
        self.assertEqual(args.db, "runtime/foundation_account_state.sqlite")
        self.assertEqual(args.report, "runtime/foundation_account_state_report.txt")
        self.assertTrue(args.include_sellable_quantity)
        self.assertFalse(args.include_closed_orders)
        self.assertEqual(args.max_order_details, 20)

    def test_parser_can_skip_sellable_quantity(self):
        args = build_parser().parse_args(["--skip-sellable-quantity"])
        self.assertFalse(args.include_sellable_quantity)

    def test_parser_accepts_max_order_details(self):
        args = build_parser().parse_args(["--max-order-details", "1"])
        self.assertEqual(args.max_order_details, 1)

    def test_parser_can_include_closed_orders_explicitly(self):
        args = build_parser().parse_args(["--include-closed-orders"])
        self.assertTrue(args.include_closed_orders)

    def test_secret_manager_failure_does_not_hide_reason_with_missing_schema(self):
        with TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "foundation.sqlite"
            report_path = Path(tmp) / "report.txt"

            with patch(
                "toss_trading.cli.foundation_snapshot.load_gcp_secret_environment",
                side_effect=RuntimeError("secret manager unavailable"),
            ):
                with redirect_stdout(StringIO()):
                    exit_code = main(
                        [
                            "--db",
                            str(db_path),
                            "--report",
                            str(report_path),
                            "--load-gcp-secrets",
                        ]
                    )

            self.assertEqual(exit_code, 1)
            report = report_path.read_text(encoding="utf-8")
            self.assertIn("foundation_snapshot=failed", report)
            self.assertIn("reason=secret manager unavailable", report)
            self.assertNotIn("no such table", report)

            conn = sqlite3.connect(db_path)
            try:
                table = conn.execute(
                    """
                    SELECT name
                    FROM sqlite_master
                    WHERE type = 'table'
                      AND name = 'source_health_snapshot'
                    """
                ).fetchone()
            finally:
                conn.close()
            self.assertIsNotNone(table)


if __name__ == "__main__":
    unittest.main()

import tempfile
import unittest
from decimal import Decimal
from pathlib import Path

from toss_trading.broker import PaperBrokerAdapter


class PaperBrokerTest(unittest.TestCase):
    def test_partial_fill_updates_cash_position_cost_and_persists(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "paper.sqlite"
            broker = PaperBrokerAdapter(
                db_path,
                initial_cash={"USD": "1000"},
                commission_bps="10",
                slippage_bps="0",
            )
            submitted = broker.submit_order(
                {
                    "client_order_id": "paper-1",
                    "symbol": "SPY",
                    "currency": "USD",
                    "side": "BUY",
                    "qty": "2",
                    "order_amount": None,
                }
            )
            self.assertEqual(submitted["status"], "paper_submitted")

            partial = broker.process_order(
                "paper-1",
                market_price="100",
                fill_ratio="0.5",
                settlement_date="2026-07-27",
            )
            self.assertEqual(partial["status"], "paper_partial_filled")
            self.assertEqual(partial["filled_qty"], "1.0")
            self.assertEqual(
                Decimal(broker.get_balances()["cash"]["USD"]),
                Decimal("899.9"),
            )

            filled = broker.process_order(
                "paper-1",
                market_price="102",
                fill_ratio="1",
                settlement_date="2026-07-27",
            )
            self.assertEqual(filled["status"], "paper_filled")
            self.assertEqual(filled["filled_qty"], "2.0")
            positions = broker.get_positions()
            self.assertEqual(positions[0]["quantity_decimal"], "2.0")
            self.assertEqual(
                broker.conn.execute("SELECT COUNT(*) FROM paper_fill").fetchone()[0],
                2,
            )
            broker.close()

            reopened = PaperBrokerAdapter(db_path)
            self.assertEqual(
                Decimal(reopened.get_balances()["cash"]["USD"]),
                Decimal("797.798"),
            )
            self.assertEqual(
                reopened.get_positions()[0]["quantity_decimal"],
                "2.0",
            )
            reopened.close()

    def test_sell_cannot_exceed_position(self):
        broker = PaperBrokerAdapter(initial_cash={"USD": "1000"})
        broker.submit_order(
            {
                "client_order_id": "sell-1",
                "symbol": "SPY",
                "side": "SELL",
                "qty": "1",
                "order_amount": None,
            }
        )
        with self.assertRaisesRegex(ValueError, "position is insufficient"):
            broker.process_order("sell-1", market_price="100")
        broker.close()

    def test_amount_order_and_idempotent_submit(self):
        broker = PaperBrokerAdapter(initial_cash={"USD": "1000"})
        order = {
            "client_order_id": "amount-1",
            "symbol": "SPY",
            "side": "BUY",
            "qty": None,
            "order_amount": "100",
        }
        first = broker.submit_order(order)
        second = broker.submit_order(order)
        self.assertEqual(first, second)
        filled = broker.process_order("amount-1", market_price="100")
        self.assertEqual(filled["status"], "paper_filled")
        self.assertGreater(Decimal(filled["filled_qty"]), Decimal("0"))
        broker.close()

    def test_limit_order_waits_for_cross_and_never_fills_worse_than_limit(self):
        broker = PaperBrokerAdapter(
            initial_cash={"USD": "1000"},
            commission_bps="0",
            slippage_bps="100",
        )
        submitted = broker.submit_order(
            {
                "client_order_id": "limit-1",
                "symbol": "SPY",
                "side": "BUY",
                "order_type": "LIMIT",
                "limit_price": "100",
                "qty": "1",
                "order_amount": None,
            }
        )
        self.assertEqual(submitted["order_type"], "LIMIT")
        self.assertEqual(submitted["limit_price"], "100")

        waiting = broker.process_order("limit-1", market_price="101")
        self.assertEqual(waiting["status"], "paper_submitted")
        self.assertEqual(waiting["filled_qty"], "0")
        self.assertEqual(
            broker.conn.execute("SELECT COUNT(*) FROM paper_fill").fetchone()[0],
            0,
        )

        filled = broker.process_order("limit-1", market_price="99.5")
        self.assertEqual(filled["status"], "paper_filled")
        self.assertLessEqual(
            Decimal(filled["average_filled_price"]),
            Decimal("100"),
        )
        broker.close()

    def test_existing_paper_database_is_migrated_for_limit_orders(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "legacy-paper.sqlite"
            broker = PaperBrokerAdapter(db_path)
            broker.conn.execute(
                "ALTER TABLE paper_order RENAME TO old_paper_order"
            )
            broker.conn.execute(
                """
                CREATE TABLE paper_order (
                  client_order_id TEXT PRIMARY KEY,
                  payload_hash TEXT NOT NULL,
                  payload_json TEXT NOT NULL,
                  symbol TEXT NOT NULL,
                  currency TEXT NOT NULL,
                  side TEXT NOT NULL,
                  quantity_decimal TEXT,
                  order_amount_decimal TEXT,
                  filled_quantity_decimal TEXT NOT NULL DEFAULT '0',
                  filled_amount_decimal TEXT NOT NULL DEFAULT '0',
                  commission_decimal TEXT NOT NULL DEFAULT '0',
                  average_filled_price_decimal TEXT,
                  status TEXT NOT NULL,
                  created_at TEXT NOT NULL,
                  updated_at TEXT NOT NULL
                )
                """
            )
            broker.conn.execute("DROP TABLE old_paper_order")
            broker.conn.commit()
            broker.close()

            migrated = PaperBrokerAdapter(db_path)
            columns = {
                row["name"]
                for row in migrated.conn.execute(
                    "PRAGMA table_info(paper_order)"
                ).fetchall()
            }
            self.assertIn("order_type", columns)
            self.assertIn("limit_price_decimal", columns)
            migrated.close()


if __name__ == "__main__":
    unittest.main()

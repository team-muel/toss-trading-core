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


if __name__ == "__main__":
    unittest.main()

import json
import sqlite3
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from toss_trading.account import AccountLedger, replay_foundation_run
from toss_trading.account.replay import _accounts_for_bound_run


class FoundationReplayTest(unittest.TestCase):
    def test_redacted_multi_account_response_fails_closed(self):
        with self.assertRaisesRegex(ValueError, "ambiguous"):
            _accounts_for_bound_run(
                {"result": [{"accountSeq": "redacted"}, {"accountSeq": "redacted"}]},
                "1",
            )

    def _create_source(self, path: Path) -> tuple[str, str]:
        ledger = AccountLedger(path)
        ledger.init_schema()
        run_id = ledger.begin_snapshot_run(account_seq="1", target_order_id="order-1")

        def store(endpoint: str, body: dict, *, account_bound: bool = True) -> str:
            return ledger.save_raw_api_response(
                source="toss",
                source_type="broker",
                endpoint=endpoint,
                http_method="GET",
                body=body,
                account_seq="1" if account_bound else None,
                status_code=200,
                run_id=run_id,
            )

        store(
            "/oauth2/token",
            {"access_token": "[REDACTED]", "expires_in": 600},
            account_bound=False,
        )
        accounts = {
            "result": [
                {
                    "accountSeq": "***REDACTED***",
                    "accountNo": "********1234",
                    "accountType": "GENERAL",
                }
            ]
        }
        raw = store("/api/v1/accounts", accounts, account_bound=False)
        ledger.ingest_accounts(accounts, raw_ref=raw, run_id=run_id)

        holdings = {
            "result": {
                "items": [
                    {
                        "symbol": "SPY",
                        "quantity": "2",
                        "averagePurchasePrice": "500",
                        "lastPrice": "510",
                        "marketValue": {"amount": "1020"},
                        "profitLoss": {"amount": "20"},
                        "cost": {"commission": "0", "tax": "0"},
                        "currency": "USD",
                    }
                ]
            }
        }
        raw = store("/api/v1/holdings", holdings)
        ledger.ingest_holdings(holdings, account_seq="1", raw_ref=raw, run_id=run_id)

        orders = {
            "result": {
                "orders": [
                    {
                        "orderId": "order-1",
                        "clientOrderId": "client-1",
                        "symbol": "SPY",
                        "side": "BUY",
                        "orderType": "LIMIT",
                        "timeInForce": "DAY",
                        "status": "FILLED",
                        "quantity": "1",
                        "price": "500",
                        "currency": "USD",
                        "execution": {
                            "filledQuantity": "1",
                            "filledAmount": "500",
                            "averageFilledPrice": "500",
                            "commission": "0.1",
                            "tax": "0",
                            "settlementDate": "2026-07-24",
                        },
                    }
                ],
                "hasNext": False,
                "nextCursor": None,
            }
        }
        raw = store("/api/v1/orders?status=CLOSED&limit=100", orders)
        ledger.ingest_orders(
            orders,
            account_seq="1",
            raw_ref=raw,
            run_id=run_id,
            status_group="CLOSED",
        )
        ledger.ingest_execution_snapshots(
            orders,
            account_seq="1",
            raw_ref=raw,
            run_id=run_id,
            status_group="CLOSED",
        )

        detail = {"result": orders["result"]["orders"][0]}
        raw = store("/api/v1/orders/order-1", detail)
        ledger.ingest_orders(detail, account_seq="1", raw_ref=raw, run_id=run_id)
        ledger.ingest_execution_snapshots(
            detail,
            account_seq="1",
            raw_ref=raw,
            run_id=run_id,
        )

        open_orders = {
            "result": {"orders": [], "hasNext": False, "nextCursor": None}
        }
        raw = store("/api/v1/orders?status=OPEN&limit=100", open_orders)
        ledger.ingest_orders(
            open_orders,
            account_seq="1",
            raw_ref=raw,
            run_id=run_id,
            status_group="OPEN",
        )

        buying_power = {"result": {"currency": "USD", "cashBuyingPower": "2500"}}
        raw = store("/api/v1/buying-power?currency=USD", buying_power)
        ledger.ingest_buying_power(
            buying_power,
            account_seq="1",
            raw_ref=raw,
            run_id=run_id,
        )

        commissions = {
            "result": [
                {
                    "marketCountry": "US",
                    "commissionRate": "0.015",
                    "startDate": None,
                    "endDate": None,
                }
            ]
        }
        raw = store("/api/v1/commissions", commissions)
        ledger.ingest_commissions(
            commissions,
            account_seq="1",
            raw_ref=raw,
            run_id=run_id,
        )

        sellable = {"result": {"sellableQuantity": "2"}}
        raw = store("/api/v1/sellable-quantity?symbol=SPY", sellable)
        ledger.ingest_sellable_quantity(
            sellable,
            account_seq="1",
            raw_ref=raw,
            fallback_symbol="SPY",
            run_id=run_id,
        )
        ledger.finish_snapshot_run(run_id, account_seq="1")
        ledger.close()
        return run_id, "1"

    def test_replay_rebuilds_normalized_rows_from_raw_responses(self):
        with TemporaryDirectory() as tmp:
            source = Path(tmp) / "source.sqlite"
            destination = Path(tmp) / "replayed.sqlite"
            source_run_id, account_seq = self._create_source(source)

            result = replay_foundation_run(
                source_db_path=source,
                destination_db_path=destination,
                source_run_id=source_run_id,
            )

            self.assertEqual(result.source_run_id, source_run_id)
            self.assertEqual(result.account_seq, account_seq)
            self.assertEqual(result.raw_rows, 9)
            self.assertEqual(result.holding_rows, 1)
            self.assertEqual(result.order_rows, 2)
            self.assertEqual(result.execution_snapshot_rows, 1)
            self.assertEqual(result.execution_delta_rows, 1)
            self.assertEqual(result.cash_event_rows, 2)

            conn = sqlite3.connect(destination)
            try:
                replayed = conn.execute(
                    """
                    SELECT symbol, quantity_decimal, market_value_decimal, currency
                    FROM holding_snapshot
                    """
                ).fetchone()
                run_status = conn.execute(
                    "SELECT status FROM snapshot_run WHERE run_id = ?",
                    (result.replay_run_id,),
                ).fetchone()[0]
            finally:
                conn.close()
            self.assertEqual(replayed, ("SPY", "2", "1020", "USD"))
            self.assertEqual(run_status, "COMPLETE")

    def test_replay_rejects_tampered_raw_response_hash(self):
        with TemporaryDirectory() as tmp:
            source = Path(tmp) / "source.sqlite"
            destination = Path(tmp) / "replayed.sqlite"
            source_run_id, _ = self._create_source(source)
            conn = sqlite3.connect(source)
            try:
                conn.execute(
                    """
                    UPDATE raw_api_response
                    SET body_json = ?
                    WHERE run_id = ? AND endpoint = '/api/v1/holdings'
                    """,
                    (json.dumps({"result": {"items": []}}), source_run_id),
                )
                conn.commit()
            finally:
                conn.close()

            with self.assertRaisesRegex(ValueError, "response hash mismatch"):
                replay_foundation_run(
                    source_db_path=source,
                    destination_db_path=destination,
                    source_run_id=source_run_id,
                )
            self.assertFalse(destination.exists())

    def test_replay_never_overwrites_an_existing_destination(self):
        with TemporaryDirectory() as tmp:
            source = Path(tmp) / "source.sqlite"
            destination = Path(tmp) / "existing.sqlite"
            source_run_id, _ = self._create_source(source)
            destination.write_text("keep", encoding="utf-8")

            with self.assertRaises(FileExistsError):
                replay_foundation_run(
                    source_db_path=source,
                    destination_db_path=destination,
                    source_run_id=source_run_id,
                )
            self.assertEqual(destination.read_text(encoding="utf-8"), "keep")


if __name__ == "__main__":
    unittest.main()

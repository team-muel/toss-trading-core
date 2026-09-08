import unittest

from toss_trading.account import AccountLedger


def order_body(
    *,
    order_id: str,
    side: str,
    status: str,
    filled_quantity: str,
    filled_amount: str,
    commission: str,
    tax: str = "0",
) -> dict:
    return {
        "result": {
            "orderId": order_id,
            "clientOrderId": f"client-{order_id}",
            "symbol": "SPY",
            "side": side,
            "orderType": "LIMIT",
            "timeInForce": "DAY",
            "status": status,
            "quantity": "2",
            "price": "100",
            "currency": "USD",
            "execution": {
                "filledQuantity": filled_quantity,
                "filledAmount": filled_amount,
                "averageFilledPrice": "100",
                "commission": commission,
                "tax": tax,
                "settlementDate": "2026-07-27",
            },
        }
    }


class CashLedgerTest(unittest.TestCase):
    def setUp(self):
        self.ledger = AccountLedger()
        self.ledger.init_schema()

    def tearDown(self):
        self.ledger.close()

    def _ingest(self, body: dict, run_id: str) -> None:
        raw = self.ledger.save_raw_api_response(
            source="toss",
            source_type="broker",
            endpoint=f"/api/v1/orders/{body['result']['orderId']}",
            http_method="GET",
            body=body,
            account_seq="1",
            status_code=200,
            run_id=run_id,
        )
        self.ledger.ingest_orders(
            body,
            account_seq="1",
            raw_ref=raw,
            run_id=run_id,
        )
        self.ledger.ingest_execution_snapshots(
            body,
            account_seq="1",
            raw_ref=raw,
            run_id=run_id,
        )

    def test_partial_then_full_buy_posts_only_incremental_cash(self):
        first_run = self.ledger.begin_snapshot_run(account_seq="1")
        self._ingest(
            order_body(
                order_id="buy-1",
                side="BUY",
                status="PARTIAL_FILLED",
                filled_quantity="1",
                filled_amount="100",
                commission="1",
            ),
            first_run,
        )
        self.ledger.finish_snapshot_run(first_run, account_seq="1")
        self.assertEqual(
            self.ledger.post_execution_cash_events(account_seq="1", run_id=first_run),
            2,
        )

        second_run = self.ledger.begin_snapshot_run(account_seq="1")
        self._ingest(
            order_body(
                order_id="buy-1",
                side="BUY",
                status="FILLED",
                filled_quantity="2",
                filled_amount="200",
                commission="2",
            ),
            second_run,
        )
        self.ledger.finish_snapshot_run(second_run, account_seq="1")
        self.assertEqual(
            self.ledger.post_execution_cash_events(account_seq="1", run_id=second_run),
            2,
        )
        self.assertEqual(
            self.ledger.post_execution_cash_events(account_seq="1"),
            0,
        )

        rows = self.ledger.conn.execute(
            """
            SELECT c.event_type, c.amount_decimal, c.settlement_date
            FROM cash_ledger AS c
            JOIN execution_delta_log AS d ON d.id = c.source_ref
            ORDER BY d.created_at, d.id, c.event_type, c.id
            """
        ).fetchall()
        self.assertEqual(
            [(row["event_type"], row["amount_decimal"]) for row in rows],
            [
                ("COMMISSION_FEE", "-1"),
                ("TRADE_COST", "-100"),
                ("COMMISSION_FEE", "-1"),
                ("TRADE_COST", "-100"),
            ],
        )
        self.assertEqual({row["settlement_date"] for row in rows}, {"2026-07-27"})

    def test_sell_posts_principal_less_fee_and_tax_as_separate_events(self):
        run_id = self.ledger.begin_snapshot_run(account_seq="1")
        self._ingest(
            order_body(
                order_id="sell-1",
                side="SELL",
                status="FILLED",
                filled_quantity="2",
                filled_amount="200",
                commission="1.5",
                tax="0.5",
            ),
            run_id,
        )
        self.ledger.finish_snapshot_run(run_id, account_seq="1")

        self.assertEqual(
            self.ledger.post_execution_cash_events(account_seq="1", run_id=run_id),
            3,
        )
        rows = self.ledger.conn.execute(
            "SELECT event_type, amount_decimal FROM cash_ledger ORDER BY event_type"
        ).fetchall()
        self.assertEqual(
            [(row["event_type"], row["amount_decimal"]) for row in rows],
            [
                ("COMMISSION_FEE", "-1.5"),
                ("REGULATORY_FEE", "-0.5"),
                ("TRADE_PROCEEDS", "200"),
            ],
        )

    def test_cash_posting_fails_closed_without_order_side(self):
        run_id = self.ledger.begin_snapshot_run(account_seq="1")
        raw = self.ledger.save_raw_api_response(
            source="toss",
            source_type="broker",
            endpoint="/api/v1/orders/missing-side",
            http_method="GET",
            body={},
            account_seq="1",
            status_code=200,
            run_id=run_id,
        )
        self.ledger.conn.execute(
            """
            INSERT INTO execution_delta_log (
              id, run_id, ts, account_seq, order_id, broker_order_id,
              to_snapshot_id, delta_filled_qty, delta_filled_amount,
              delta_commission, delta_tax, created_at, currency,
              delta_filled_qty_decimal, delta_filled_amount_decimal,
              delta_commission_decimal, delta_tax_decimal
            ) VALUES (
              'delta-missing', ?, '2026-07-23T00:00:00Z', '1',
              'missing-side', 'missing-side', 'snapshot-missing',
              1, 100, 0, 0, '2026-07-23T00:00:00Z', 'USD',
              '1', '100', '0', '0'
            )
            """,
            (run_id,),
        )
        self.ledger.conn.commit()
        self.ledger.finish_snapshot_run(run_id, account_seq="1")

        with self.assertRaisesRegex(ValueError, "no matching broker order"):
            self.ledger.post_execution_cash_events(account_seq="1", run_id=run_id)
        count = self.ledger.conn.execute("SELECT COUNT(*) FROM cash_ledger").fetchone()[0]
        self.assertEqual(count, 0)

    def test_failed_run_execution_cash_is_backfilled_by_next_run(self):
        failed_run = self.ledger.begin_snapshot_run(account_seq="1")
        body = order_body(
            order_id="failed-before-cash",
            side="BUY",
            status="FILLED",
            filled_quantity="1",
            filled_amount="100",
            commission="1",
        )
        self._ingest(body, failed_run)
        self.ledger.fail_snapshot_run(failed_run, "injected failure before cash posting")

        next_run = self.ledger.begin_snapshot_run(account_seq="1")
        self._ingest(body, next_run)
        self.assertEqual(
            self.ledger.post_execution_cash_events(account_seq="1"),
            2,
        )
        self.ledger.finish_snapshot_run(next_run, account_seq="1")

        events = self.ledger.conn.execute(
            """
            SELECT c.event_type, c.amount_decimal, d.run_id
            FROM cash_ledger AS c
            JOIN execution_delta_log AS d ON d.id = c.source_ref
            ORDER BY c.event_type
            """
        ).fetchall()
        self.assertEqual(
            [(row["event_type"], row["amount_decimal"]) for row in events],
            [("COMMISSION_FEE", "-1"), ("TRADE_COST", "-100")],
        )
        self.assertEqual({row["run_id"] for row in events}, {failed_run})
        self.assertEqual(self.ledger.cash_event_gaps(account_seq="1"), [])

    def test_cash_posting_repairs_a_partial_event_set(self):
        run_id = self.ledger.begin_snapshot_run(account_seq="1")
        self._ingest(
            order_body(
                order_id="partial-cash-events",
                side="SELL",
                status="FILLED",
                filled_quantity="1",
                filled_amount="100",
                commission="1",
                tax="0.5",
            ),
            run_id,
        )
        self.ledger.finish_snapshot_run(run_id, account_seq="1")
        self.assertEqual(
            self.ledger.post_execution_cash_events(account_seq="1", run_id=run_id),
            3,
        )
        self.ledger.conn.execute(
            "DELETE FROM cash_ledger WHERE event_type = 'COMMISSION_FEE'"
        )
        self.ledger.conn.commit()

        self.assertEqual(
            self.ledger.post_execution_cash_events(account_seq="1", run_id=run_id),
            1,
        )
        self.assertEqual(self.ledger.cash_event_gaps(account_seq="1"), [])

    def test_cash_posting_updates_a_corrected_settlement_date(self):
        run_id = self.ledger.begin_snapshot_run(account_seq="1")
        body = order_body(
            order_id="settlement-correction",
            side="BUY",
            status="FILLED",
            filled_quantity="1",
            filled_amount="100",
            commission="0",
        )
        self._ingest(body, run_id)
        self.ledger.finish_snapshot_run(run_id, account_seq="1")
        self.assertEqual(
            self.ledger.post_execution_cash_events(account_seq="1", run_id=run_id),
            1,
        )
        self.ledger.conn.execute(
            """
            UPDATE broker_order_snapshot
            SET settlement_date = '2026-07-28'
            WHERE broker_order_id = 'settlement-correction'
            """
        )
        self.ledger.conn.commit()

        self.assertEqual(
            self.ledger.post_execution_cash_events(account_seq="1", run_id=run_id),
            1,
        )
        settlement_date = self.ledger.conn.execute(
            "SELECT settlement_date FROM cash_ledger"
        ).fetchone()[0]
        self.assertEqual(settlement_date, "2026-07-28")
        self.assertEqual(self.ledger.cash_event_gaps(account_seq="1"), [])

    def test_cash_ledger_genesis_requires_explicit_evidence(self):
        with self.assertRaisesRegex(ValueError, "evidence_ref"):
            self.ledger.record_cash_ledger_genesis(
                account_seq="1",
                currency="USD",
                as_of="2026-07-23T00:00:00Z",
                opening_balance="1000",
                evidence_ref="",
                approved_by="operator",
            )
        self.ledger.record_cash_ledger_genesis(
            account_seq="1",
            currency="usd",
            as_of="2026-07-23T00:00:00Z",
            opening_balance="1000.25",
            evidence_ref="manual-statement:2026-07-23",
            approved_by="operator",
        )
        row = self.ledger.conn.execute(
            "SELECT * FROM cash_ledger_genesis WHERE account_seq = '1'"
        ).fetchone()
        self.assertEqual(row["currency"], "USD")
        self.assertEqual(row["opening_balance_decimal"], "1000.25")

    def test_reserved_cash_uses_only_unfilled_open_buy_notional(self):
        run_id = self.ledger.begin_snapshot_run(account_seq="1")
        partial = order_body(
            order_id="open-buy",
            side="BUY",
            status="PARTIAL_FILLED",
            filled_quantity="1",
            filled_amount="100",
            commission="1",
        )
        self._ingest(partial, run_id)
        self._ingest(partial, run_id)

        sell = order_body(
            order_id="open-sell",
            side="SELL",
            status="PENDING",
            filled_quantity="0",
            filled_amount="0",
            commission="0",
        )
        self._ingest(sell, run_id)
        self.ledger.finish_snapshot_run(run_id, account_seq="1")

        result = self.ledger.reserved_open_buy_cash(
            account_seq="1",
            run_id=run_id,
        )
        self.assertEqual(result.amount_by_currency, {"USD": "100"})
        self.assertEqual(result.blockers, [])

    def test_reserved_cash_blocks_open_buy_without_resolvable_notional(self):
        run_id = self.ledger.begin_snapshot_run(account_seq="1")
        body = order_body(
            order_id="open-market",
            side="BUY",
            status="PENDING",
            filled_quantity="0",
            filled_amount="0",
            commission="0",
        )
        body["result"]["price"] = None
        self._ingest(body, run_id)
        self.ledger.finish_snapshot_run(run_id, account_seq="1")

        result = self.ledger.reserved_open_buy_cash(
            account_seq="1",
            run_id=run_id,
        )
        self.assertEqual(result.amount_by_currency, {})
        self.assertEqual(
            result.blockers,
            ["open_buy_notional_not_resolvable:open-market"],
        )

    def test_schema_migrates_legacy_cash_amount_to_decimal_text(self):
        ledger = AccountLedger()
        try:
            ledger.conn.execute(
                """
                CREATE TABLE cash_ledger (
                  id TEXT PRIMARY KEY,
                  ts TEXT NOT NULL,
                  account_seq TEXT NOT NULL,
                  currency TEXT NOT NULL,
                  event_type TEXT NOT NULL,
                  amount REAL NOT NULL,
                  settlement_date TEXT,
                  source_ref TEXT,
                  tax_relevant INTEGER NOT NULL DEFAULT 0,
                  created_at TEXT NOT NULL
                )
                """
            )
            ledger.conn.execute(
                """
                INSERT INTO cash_ledger (
                  id, ts, account_seq, currency, event_type, amount, created_at
                ) VALUES (
                  'legacy', '2026-07-23T00:00:00Z', '1', 'USD',
                  'OPENING_BALANCE', 12.5, '2026-07-23T00:00:00Z'
                )
                """
            )
            ledger.conn.execute("PRAGMA user_version = 1")
            ledger.conn.commit()

            ledger.init_schema()

            row = ledger.conn.execute(
                "SELECT amount_decimal FROM cash_ledger WHERE id = 'legacy'"
            ).fetchone()
            self.assertEqual(row["amount_decimal"], "12.5")
            self.assertEqual(
                ledger.conn.execute("PRAGMA user_version").fetchone()[0],
                5,
            )
        finally:
            ledger.close()

    def test_market_bars_primary_key_includes_interval_and_adjustment(self):
        self.ledger.conn.execute(
            """
            INSERT INTO market_bars (
              ts, symbol, source, interval, adjustment, ingested_at
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            ("2026-01-02T21:00:00Z", "SPY", "toss", "1d", "raw", "now"),
        )
        self.ledger.conn.execute(
            """
            INSERT INTO market_bars (
              ts, symbol, source, interval, adjustment, ingested_at
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                "2026-01-02T21:00:00Z",
                "SPY",
                "toss",
                "1d",
                "split_adjusted",
                "now",
            ),
        )
        self.assertEqual(
            self.ledger.conn.execute("SELECT COUNT(*) FROM market_bars").fetchone()[0],
            2,
        )

    def test_market_bars_legacy_key_is_rebuilt_without_losing_rows(self):
        ledger = AccountLedger()
        try:
            ledger.init_schema()
            ledger.conn.executescript(
                """
                ALTER TABLE market_bars RENAME TO market_bars_v5;
                CREATE TABLE market_bars AS
                  SELECT * FROM market_bars_v5 WHERE 0;
                DROP TABLE market_bars_v5;
                INSERT INTO market_bars (
                  ts, symbol, source, interval, adjustment, ingested_at,
                  schema_version, quality_flag
                ) VALUES (
                  '2026-01-02T21:00:00Z', 'SPY', 'toss', '1d', 'raw',
                  'now', 'market-bars-v1', 'ok'
                );
                PRAGMA user_version = 4;
                """
            )

            ledger.init_schema()
            ledger.conn.execute(
                """
                INSERT INTO market_bars (
                  ts, symbol, source, interval, adjustment, ingested_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    "2026-01-02T21:00:00Z",
                    "SPY",
                    "toss",
                    "1d",
                    "split_adjusted",
                    "now",
                ),
            )

            self.assertEqual(
                ledger.conn.execute("SELECT COUNT(*) FROM market_bars").fetchone()[0],
                2,
            )
            self.assertEqual(
                ledger.conn.execute("PRAGMA user_version").fetchone()[0],
                5,
            )
        finally:
            ledger.close()

    def test_cash_ledger_genesis_is_idempotent_but_immutable(self):
        values = {
            "account_seq": "1",
            "currency": "USD",
            "as_of": "2026-07-23T00:00:00Z",
            "opening_balance": "1000",
            "evidence_ref": "statement:2026-07-23",
            "approved_by": "operator",
        }
        self.ledger.record_cash_ledger_genesis(**values)
        self.ledger.record_cash_ledger_genesis(**values)
        with self.assertRaisesRegex(ValueError, "immutable"):
            self.ledger.record_cash_ledger_genesis(
                **{**values, "opening_balance": "999"}
            )
        row = self.ledger.conn.execute(
            "SELECT opening_balance_decimal FROM cash_ledger_genesis"
        ).fetchone()
        self.assertEqual(row["opening_balance_decimal"], "1000")

    def test_amount_based_order_does_not_require_quantity(self):
        run_id = self.ledger.begin_snapshot_run(account_seq="1")
        body = order_body(
            order_id="amount-order",
            side="BUY",
            status="PENDING",
            filled_quantity="0",
            filled_amount="0",
            commission="0",
        )
        body["result"]["quantity"] = None
        body["result"]["orderAmount"] = "100"
        raw = self.ledger.save_raw_api_response(
            source="toss",
            source_type="broker",
            endpoint="/api/v1/orders/amount-order",
            http_method="GET",
            body=body,
            account_seq="1",
            status_code=200,
            run_id=run_id,
        )
        self.assertEqual(
            self.ledger.ingest_orders(
                body,
                account_seq="1",
                raw_ref=raw,
                run_id=run_id,
            ),
            1,
        )
        row = self.ledger.conn.execute(
            "SELECT quantity_decimal, order_amount_decimal FROM broker_order_snapshot"
        ).fetchone()
        self.assertIsNone(row["quantity_decimal"])
        self.assertEqual(row["order_amount_decimal"], "100")


if __name__ == "__main__":
    unittest.main()

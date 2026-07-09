import unittest
import json
from io import BytesIO
from unittest.mock import patch

import urllib.error
import urllib.request

from toss_trading.account import AccountLedger, FoundationSnapshotter
from toss_trading.broker.credentials import TossCredentials
from toss_trading.broker.toss import TossReadOnlyAdapter
from toss_trading.broker.toss import TossApiResult
from toss_trading.data import (
    load_instrument_mappings,
    load_universe,
    validate_universe_mapping,
)


class FakeTossAdapter:
    def __init__(self):
        self.detail_order_ids = []

    def get_accounts(self):
        return TossApiResult(
            endpoint="/api/v1/accounts",
            status_code=200,
            raw_response_id=self.ledger.save_raw_api_response(
                source="toss",
                source_type="broker",
                endpoint="/api/v1/accounts",
                http_method="GET",
                body={
                    "accounts": [
                        {
                            "accountSeq": "1",
                            "accountNo": "1234567890",
                            "accountType": "GENERAL",
                        }
                    ]
                },
            ),
            body={
                "accounts": [
                    {
                        "accountSeq": "1",
                        "accountNo": "1234567890",
                        "accountType": "GENERAL",
                    }
                ]
            },
        )

    def get_holdings(self):
        body = {
            "holdings": [
                {
                    "symbol": "SPY",
                    "quantity": "2",
                    "averagePurchasePrice": "500",
                    "lastPrice": "510",
                    "marketValue": "1020",
                    "profitLoss": "20",
                    "cost": "1000",
                    "currency": "USD",
                }
            ]
        }
        return self._raw("/api/v1/holdings", body)

    def get_all_orders(self, status, **query):
        return [self.get_orders(status=status, **query)]

    def get_orders(self, status=None, **query):
        body = {
            "result": {
                "orders": [self._order(status)],
                "nextCursor": None,
                "hasNext": False,
            }
        }
        return self._raw(f"/api/v1/orders?status={status}&limit=100", body)

    def get_order(self, order_id):
        self.detail_order_ids.append(order_id)
        status = "OPEN" if order_id == "open-1" else "CLOSED"
        return self._raw(
            f"/api/v1/orders/{order_id}",
            {"result": self._order(status)},
        )

    def _order(self, status):
        return {
            "orderId": f"{status.lower()}-1",
            "clientOrderId": f"cid-{status.lower()}",
            "symbol": "SPY",
            "side": "BUY",
            "orderType": "LIMIT",
            "timeInForce": "DAY",
            "status": "PENDING" if status == "OPEN" else "FILLED",
            "quantity": "1",
            "price": "500",
            "orderedAt": "2026-06-24T09:30:00+09:00",
            "execution": {
                "filledQuantity": "0" if status == "OPEN" else "1",
                "filledAmount": "0" if status == "OPEN" else "500",
                "averageFilledPrice": None if status == "OPEN" else "500",
                "commission": "0",
                "tax": "0",
                "settlementDate": None if status == "OPEN" else "2026-06-26",
            },
        }

    def get_buying_power(self, **query):
        self.last_buying_power_query = query
        return self._raw(
            f"/api/v1/buying-power?currency={query.get('currency', '')}",
            {"currency": "USD", "cashBuyingPower": "2500"},
        )

    def get_commissions(self, **query):
        return self._raw(
            "/api/v1/commissions",
            {
                "commissions": [
                    {
                        "market": "US",
                        "symbol": "SPY",
                        "side": "BUY",
                        "orderAmount": "500",
                        "commission": "0",
                        "currency": "USD",
                    }
                ]
            },
        )

    def get_sellable_quantity(self, **query):
        symbol = query["symbol"]
        return self._raw(
            f"/api/v1/sellable-quantity?symbol={symbol}",
            {"symbol": symbol, "sellableQuantity": "2"},
        )

    def _raw(self, endpoint, body):
        return TossApiResult(
            endpoint=endpoint,
            status_code=200,
            raw_response_id=self.ledger.save_raw_api_response(
                source="toss",
                source_type="broker",
                endpoint=endpoint,
                http_method="GET",
                account_seq="1",
                body=body,
            ),
            body=body,
        )


class FoundationAccountStateTest(unittest.TestCase):
    def test_universe_has_instrument_mapping(self):
        universe = load_universe("data/universe.csv")
        mappings = load_instrument_mappings("data/instrument_master.csv")
        validate_universe_mapping(universe, mappings)
        missing_cik = [item.ticker for item in mappings if not item.cik]
        self.assertEqual(missing_cik, [])

    def test_schema_initialization_is_idempotent(self):
        ledger = AccountLedger()
        ledger.init_schema()
        ledger.init_schema()

        table_count = ledger.conn.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE type = 'table'"
        ).fetchone()[0]
        self.assertGreater(table_count, 0)

    def test_snapshotter_stores_and_explains_toss_account_state(self):
        ledger = AccountLedger()
        ledger.init_schema()
        fake = FakeTossAdapter()
        fake.ledger = ledger

        result = FoundationSnapshotter(fake, ledger).snapshot(
            account_seq="1",
            include_sellable_quantity=True,
        )

        self.assertEqual(result.accounts, 1)
        self.assertEqual(result.holdings, 1)
        self.assertEqual(result.open_orders, 1)
        self.assertEqual(result.closed_orders, 1)
        self.assertEqual(result.buying_power_rows, 1)
        self.assertEqual(result.commission_rows, 1)
        self.assertEqual(result.sellable_quantity_rows, 1)
        self.assertEqual(result.order_detail_rows, 2)
        self.assertEqual(result.execution_snapshot_rows, 2)
        self.assertEqual(result.execution_delta_rows, 1)
        self.assertEqual(result.explanation.holdings_count, 1)
        self.assertEqual(result.explanation.open_orders_count, 1)
        self.assertEqual(result.explanation.buying_power_by_currency["USD"], 2500.0)
        self.assertEqual(result.explanation.blockers, [])
        self.assertEqual(fake.last_buying_power_query, {"currency": "USD"})

        raw_count = ledger.conn.execute("SELECT COUNT(*) FROM raw_api_response").fetchone()[0]
        self.assertEqual(raw_count, 9)

        detail_count = ledger.conn.execute(
            "SELECT COUNT(*) FROM raw_api_response WHERE endpoint LIKE '/api/v1/orders/%'"
        ).fetchone()[0]
        self.assertEqual(detail_count, 2)

    def test_execution_delta_is_not_duplicated_for_same_cumulative_snapshot(self):
        ledger = AccountLedger()
        ledger.init_schema()
        fake = FakeTossAdapter()
        fake.ledger = ledger

        first = FoundationSnapshotter(fake, ledger).snapshot(account_seq="1")
        second = FoundationSnapshotter(fake, ledger).snapshot(account_seq="1")

        self.assertEqual(first.execution_delta_rows, 1)
        self.assertEqual(second.execution_delta_rows, 0)
        delta_count = ledger.conn.execute("SELECT COUNT(*) FROM execution_delta_log").fetchone()[0]
        self.assertEqual(delta_count, 1)

    def test_snapshotter_limits_order_detail_calls_and_prefers_closed_orders(self):
        ledger = AccountLedger()
        ledger.init_schema()
        fake = FakeTossAdapter()
        fake.ledger = ledger

        result = FoundationSnapshotter(fake, ledger).snapshot(
            account_seq="1",
            max_order_details=1,
        )

        self.assertEqual(result.order_detail_rows, 1)
        self.assertEqual(fake.detail_order_ids, ["closed-1"])
        detail_count = ledger.conn.execute(
            "SELECT COUNT(*) FROM raw_api_response WHERE endpoint LIKE '/api/v1/orders/%'"
        ).fetchone()[0]
        self.assertEqual(detail_count, 1)

    def test_execution_snapshot_updates_when_average_filled_price_changes(self):
        ledger = AccountLedger()
        ledger.init_schema()
        first_raw = ledger.save_raw_api_response(
            source="toss",
            source_type="broker",
            endpoint="/api/v1/orders",
            http_method="GET",
            account_seq="1",
            body={},
            status_code=200,
        )
        second_raw = ledger.save_raw_api_response(
            source="toss",
            source_type="broker",
            endpoint="/api/v1/orders",
            http_method="GET",
            account_seq="1",
            body={},
            status_code=200,
        )
        first = {
            "orders": [
                {
                    "orderId": "order-avg",
                    "status": "CLOSED",
                    "execution": {
                        "filledQuantity": "2",
                        "filledAmount": "200",
                        "averageFilledPrice": "100",
                        "commission": "1",
                        "tax": "0",
                    },
                }
            ]
        }
        corrected = {
            "orders": [
                {
                    "orderId": "order-avg",
                    "status": "CLOSED",
                    "execution": {
                        "filledQuantity": "2",
                        "filledAmount": "200",
                        "averageFilledPrice": "99.99",
                        "commission": "1",
                        "tax": "0",
                    },
                }
            ]
        }

        first_counts = ledger.ingest_execution_snapshots(
            first,
            account_seq="1",
            raw_ref=first_raw,
        )
        corrected_counts = ledger.ingest_execution_snapshots(
            corrected,
            account_seq="1",
            raw_ref=second_raw,
        )

        self.assertEqual(first_counts, (1, 1))
        self.assertEqual(corrected_counts, (1, 0))
        snapshot_count = ledger.conn.execute(
            "SELECT COUNT(*) FROM execution_snapshot_log WHERE order_id = 'order-avg'"
        ).fetchone()[0]
        delta_count = ledger.conn.execute(
            "SELECT COUNT(*) FROM execution_delta_log WHERE order_id = 'order-avg'"
        ).fetchone()[0]
        latest_average = ledger.conn.execute(
            """
            SELECT average_filled_price
            FROM execution_snapshot_log
            WHERE order_id = 'order-avg'
            ORDER BY snapshot_seq DESC
            LIMIT 1
            """
        ).fetchone()["average_filled_price"]
        self.assertEqual(snapshot_count, 2)
        self.assertEqual(delta_count, 1)
        self.assertEqual(latest_average, 99.99)

    def test_empty_commission_response_does_not_create_snapshot_row(self):
        ledger = AccountLedger()
        ledger.init_schema()
        raw_ref = ledger.save_raw_api_response(
            source="toss",
            source_type="broker",
            endpoint="/api/v1/commissions",
            http_method="GET",
            account_seq="1",
            body={"commissions": []},
            status_code=200,
        )

        empty_array_rows = ledger.ingest_commissions(
            {"commissions": []},
            account_seq="1",
            raw_ref=raw_ref,
        )
        empty_object_rows = ledger.ingest_commissions(
            {},
            account_seq="1",
            raw_ref=raw_ref,
        )

        self.assertEqual(empty_array_rows, 0)
        self.assertEqual(empty_object_rows, 0)
        row_count = ledger.conn.execute("SELECT COUNT(*) FROM commission_snapshot").fetchone()[0]
        self.assertEqual(row_count, 0)

    def test_latest_empty_snapshots_do_not_reuse_stale_holdings_or_open_orders(self):
        ledger = AccountLedger()
        ledger.init_schema()
        old_holdings_raw = ledger.save_raw_api_response(
            source="toss",
            source_type="broker",
            endpoint="/api/v1/holdings",
            http_method="GET",
            account_seq="1",
            body={"holdings": [{"symbol": "SPY", "quantity": "2"}]},
            status_code=200,
            ts="2026-06-24T00:00:00+00:00",
        )
        ledger.ingest_holdings(
            {"holdings": [{"symbol": "SPY", "quantity": "2", "marketValue": "1000"}]},
            account_seq="1",
            raw_ref=old_holdings_raw,
            ts="2026-06-24T00:00:01+00:00",
        )
        old_orders_raw = ledger.save_raw_api_response(
            source="toss",
            source_type="broker",
            endpoint="/api/v1/orders?status=OPEN",
            http_method="GET",
            account_seq="1",
            body={"orders": [{"orderId": "old-open", "symbol": "SPY", "status": "PENDING"}]},
            status_code=200,
            ts="2026-06-24T00:00:02+00:00",
        )
        ledger.ingest_orders(
            {"orders": [{"orderId": "old-open", "symbol": "SPY", "status": "PENDING"}]},
            account_seq="1",
            raw_ref=old_orders_raw,
            ts="2026-06-24T00:00:03+00:00",
        )
        ledger.save_raw_api_response(
            source="toss",
            source_type="broker",
            endpoint="/api/v1/holdings",
            http_method="GET",
            account_seq="1",
            body={"holdings": []},
            status_code=200,
            ts="2026-06-24T00:01:00+00:00",
        )
        ledger.save_raw_api_response(
            source="toss",
            source_type="broker",
            endpoint="/api/v1/orders?status=OPEN",
            http_method="GET",
            account_seq="1",
            body={"orders": []},
            status_code=200,
            ts="2026-06-24T00:01:01+00:00",
        )
        buying_power_raw = ledger.save_raw_api_response(
            source="toss",
            source_type="broker",
            endpoint="/api/v1/buying-power",
            http_method="GET",
            account_seq="1",
            body={"currency": "USD", "cashBuyingPower": "2500"},
            status_code=200,
            ts="2026-06-24T00:01:02+00:00",
        )
        ledger.ingest_buying_power(
            {"currency": "USD", "cashBuyingPower": "2500"},
            account_seq="1",
            raw_ref=buying_power_raw,
            ts="2026-06-24T00:01:03+00:00",
        )

        explanation = ledger.explain_account_state("1")

        self.assertEqual(explanation.holdings_count, 0)
        self.assertEqual(explanation.open_orders_count, 0)
        self.assertEqual(explanation.blockers, [])

    def test_toss_adapter_persists_account_seq_for_account_bound_raw_response(self):
        class FakeResponse:
            status = 200
            headers = {"X-Request-Id": "req-1"}

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, traceback):
                return False

            def read(self):
                return b'{"holdings":[]}'

        ledger = AccountLedger()
        ledger.init_schema()
        adapter = TossReadOnlyAdapter(
            TossCredentials(
                client_id="client",
                client_secret="secret",
                account_seq="1",
                base_url="https://example.invalid",
            ),
            ledger,
        )
        request = urllib.request.Request(
            "https://example.invalid/api/v1/holdings",
            headers={"X-Tossinvest-Account": "1"},
            method="GET",
        )

        with patch("urllib.request.urlopen", return_value=FakeResponse()):
            result = adapter._send(request, "/api/v1/holdings")

        row = ledger.conn.execute(
            "SELECT account_seq, request_id FROM raw_api_response WHERE id = ?",
            (result.raw_response_id,),
        ).fetchone()
        self.assertEqual(row["account_seq"], "1")
        self.assertEqual(row["request_id"], "req-1")

        health = ledger.latest_source_health("toss", "rest:/api/v1/holdings")
        self.assertIsNotNone(health)
        self.assertEqual(health["source_status"], "ok")
        self.assertIsNotNone(health["last_success_at"])

    def test_toss_adapter_redacts_account_identifiers_in_raw_body(self):
        class FakeResponse:
            status = 200
            headers = {}

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, traceback):
                return False

            def read(self):
                return json.dumps(
                    {
                        "accounts": [
                            {
                                "accountSeq": "1",
                                "accountNo": "1234567890",
                                "accountNumber": "9876543210",
                                "account_no": "555544443333",
                            }
                        ]
                    }
                ).encode("utf-8")

        ledger = AccountLedger()
        ledger.init_schema()
        adapter = TossReadOnlyAdapter(
            TossCredentials(
                client_id="client",
                client_secret="secret",
                account_seq=None,
                base_url="https://example.invalid",
            ),
            ledger,
        )
        request = urllib.request.Request(
            "https://example.invalid/api/v1/accounts",
            method="GET",
        )

        with patch("urllib.request.urlopen", return_value=FakeResponse()):
            result = adapter._send(request, "/api/v1/accounts")

        body = ledger.conn.execute(
            "SELECT body_json FROM raw_api_response WHERE id = ?",
            (result.raw_response_id,),
        ).fetchone()["body_json"]

        self.assertNotIn("1234567890", body)
        self.assertNotIn("9876543210", body)
        self.assertNotIn("555544443333", body)
        self.assertNotIn('"accountSeq": "1"', body)
        self.assertIn("******7890", body)
        self.assertIn("***REDACTED***", body)

    def test_toss_adapter_records_ip_allowlist_failure_as_source_health(self):
        ledger = AccountLedger()
        ledger.init_schema()
        adapter = TossReadOnlyAdapter(
            TossCredentials(
                client_id="client",
                client_secret="secret",
                account_seq=None,
                base_url="https://example.invalid",
            ),
            ledger,
        )
        request = urllib.request.Request(
            "https://example.invalid/oauth2/token",
            method="POST",
        )
        failure = urllib.error.HTTPError(
            request.full_url,
            403,
            "Forbidden",
            {"X-Request-Id": "req-403"},
            BytesIO(
                b'{"error":"access_denied","error_description":"IP address not allowed"}'
            ),
        )

        with patch("urllib.request.urlopen", side_effect=failure):
            with self.assertRaisesRegex(RuntimeError, "IP address not allowed"):
                adapter._send(request, "/oauth2/token")

        raw = ledger.conn.execute(
            """
            SELECT status_code, channel, body_json
            FROM raw_api_response
            WHERE endpoint = '/oauth2/token'
            """
        ).fetchone()
        self.assertEqual(raw["status_code"], 403)
        self.assertEqual(raw["channel"], "rest:/oauth2/token")
        self.assertIn("IP address not allowed", raw["body_json"])

        health = ledger.latest_source_health("toss", "rest:/oauth2/token")
        self.assertIsNotNone(health)
        self.assertEqual(health["source_status"], "blocked")
        self.assertEqual(
            health["action"],
            "register_current_ip_in_toss_openapi_allowlist",
        )

    def test_toss_adapter_stores_non_json_error_body_before_raising(self):
        ledger = AccountLedger()
        ledger.init_schema()
        adapter = TossReadOnlyAdapter(
            TossCredentials(
                client_id="client",
                client_secret="secret",
                account_seq=None,
                base_url="https://example.invalid",
            ),
            ledger,
        )
        request = urllib.request.Request(
            "https://example.invalid/api/v1/holdings",
            method="GET",
        )
        failure = urllib.error.HTTPError(
            request.full_url,
            503,
            "Service Unavailable",
            {"Content-Type": "text/html"},
            BytesIO(b"<html><body>temporarily unavailable\xff</body></html>"),
        )

        with patch("urllib.request.urlopen", side_effect=failure):
            with self.assertRaisesRegex(RuntimeError, "Toss API error 503"):
                adapter._send(request, "/api/v1/holdings")

        raw = ledger.conn.execute(
            """
            SELECT status_code, channel, body_json
            FROM raw_api_response
            WHERE endpoint = '/api/v1/holdings'
            """
        ).fetchone()
        self.assertEqual(raw["status_code"], 503)
        self.assertEqual(raw["channel"], "rest:/api/v1/holdings")
        self.assertIn("non_json_response", raw["body_json"])
        self.assertIn("temporarily unavailable", raw["body_json"])

    def test_toss_adapter_uses_error_body_request_id_when_header_is_absent(self):
        ledger = AccountLedger()
        ledger.init_schema()
        adapter = TossReadOnlyAdapter(
            TossCredentials(
                client_id="client",
                client_secret="secret",
                account_seq=None,
                base_url="https://example.invalid",
            ),
            ledger,
        )
        request = urllib.request.Request(
            "https://example.invalid/api/v1/orders",
            method="GET",
        )
        failure = urllib.error.HTTPError(
            request.full_url,
            400,
            "Bad Request",
            {},
            BytesIO(
                json.dumps(
                    {
                        "error": {
                            "requestId": "body-req-400",
                            "code": "BAD_REQUEST",
                            "message": "bad request",
                            "data": {"field": "cursor"},
                        }
                    }
                ).encode("utf-8")
            ),
        )

        with patch("urllib.request.urlopen", side_effect=failure):
            with self.assertRaisesRegex(RuntimeError, "request_id=body-req-400"):
                adapter._send(request, "/api/v1/orders")

        raw = ledger.conn.execute(
            """
            SELECT request_id, channel
            FROM raw_api_response
            WHERE endpoint = '/api/v1/orders'
            """
        ).fetchone()
        self.assertEqual(raw["request_id"], "body-req-400")
        self.assertEqual(raw["channel"], "rest:/api/v1/orders")

    def test_toss_adapter_follows_order_pagination(self):
        class FakeResponse:
            status = 200
            headers = {}

            def __init__(self, body):
                self.body = body

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, traceback):
                return False

            def read(self):
                return json.dumps(self.body).encode("utf-8")

        calls = []

        def fake_urlopen(request, timeout):
            calls.append(request.full_url)
            body = (
                {
                    "result": {
                        "orders": [{"orderId": "closed-1"}],
                        "hasNext": True,
                        "nextCursor": "cursor-2",
                    }
                }
                if len(calls) == 1
                else {
                    "result": {
                        "orders": [{"orderId": "closed-2"}],
                        "hasNext": False,
                        "nextCursor": None,
                    }
                }
            )
            return FakeResponse(body)

        adapter = TossReadOnlyAdapter(
            TossCredentials(
                client_id="client",
                client_secret="secret",
                account_seq="1",
                base_url="https://example.invalid",
            )
        )
        adapter._access_token = "token"

        with patch("urllib.request.urlopen", side_effect=fake_urlopen):
            results = adapter.get_all_orders(status="CLOSED", limit=100)

        self.assertEqual(len(results), 2)
        self.assertIn("status=CLOSED", calls[0])
        self.assertIn("limit=100", calls[0])
        self.assertIn("cursor=cursor-2", calls[1])


if __name__ == "__main__":
    unittest.main()

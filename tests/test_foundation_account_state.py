import unittest
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

    def get_orders(self, status=None):
        body = {
            "orders": [
                {
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
            ]
        }
        return self._raw(f"/api/v1/orders?status={status}", body)

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
        self.assertEqual(result.explanation.holdings_count, 1)
        self.assertEqual(result.explanation.open_orders_count, 1)
        self.assertEqual(result.explanation.buying_power_by_currency["USD"], 2500.0)
        self.assertEqual(result.explanation.blockers, [])
        self.assertEqual(fake.last_buying_power_query, {"currency": "USD"})

        raw_count = ledger.conn.execute("SELECT COUNT(*) FROM raw_api_response").fetchone()[0]
        self.assertEqual(raw_count, 7)

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
            SELECT status_code, body_json
            FROM raw_api_response
            WHERE endpoint = '/oauth2/token'
            """
        ).fetchone()
        self.assertEqual(raw["status_code"], 403)
        self.assertIn("IP address not allowed", raw["body_json"])

        health = ledger.latest_source_health("toss", "rest:/oauth2/token")
        self.assertIsNotNone(health)
        self.assertEqual(health["source_status"], "blocked")
        self.assertEqual(
            health["action"],
            "register_current_ip_in_toss_openapi_allowlist",
        )


if __name__ == "__main__":
    unittest.main()

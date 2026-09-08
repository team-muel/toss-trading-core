import unittest

from toss_trading.broker.credentials import TossCredentials
from toss_trading.broker.toss import TossApiResult, TossReadOnlyAdapter


class RecordingAdapter(TossReadOnlyAdapter):
    def __init__(self):
        super().__init__(
            TossCredentials(
                client_id="id",
                client_secret="secret",
                account_seq="1",
                base_url="https://example.invalid",
                api_env="production",
            )
        )
        self.calls = []

    def _get(self, endpoint, *, account_bound):
        self.calls.append((endpoint, account_bound))
        return TossApiResult(
            endpoint=endpoint,
            status_code=200,
            body={"result": []},
            raw_response_id="",
        )


class TossReferenceAdapterTest(unittest.TestCase):
    def test_live_and_conditional_write_capabilities_remain_disabled(self):
        capabilities = RecordingAdapter().capabilities

        self.assertFalse(capabilities.order_entry)
        self.assertFalse(capabilities.order_cancel)
        self.assertFalse(capabilities.conditional_order_entry)
        self.assertFalse(capabilities.conditional_order_modify)

    def test_reference_endpoints_are_read_only_and_not_account_bound(self):
        adapter = RecordingAdapter()

        adapter.get_stocks(["SPY", "QQQ"])
        adapter.get_stock_warnings("SPY")
        adapter.get_exchange_rate(
            base_currency="USD",
            quote_currency="KRW",
        )
        adapter.get_market_calendar("US", calendar_date="2026-07-24")
        adapter.get_rankings(
            ranking_type="MARKET_TRADING_AMOUNT",
            market_country="US",
            duration="1d",
        )
        adapter.get_market_indicator_prices(["KOSPI", "KR_BOND_10Y"])
        adapter.get_market_indicator_candles("KR_BOND_10Y")
        adapter.get_market_indicator_investor_trading("KOSPI")

        self.assertEqual(len(adapter.calls), 8)
        self.assertTrue(all(not account_bound for _, account_bound in adapter.calls))
        self.assertTrue(all(endpoint.startswith("/api/v1/") for endpoint, _ in adapter.calls))

    def test_invalid_reference_requests_are_rejected_locally(self):
        adapter = RecordingAdapter()

        with self.assertRaises(ValueError):
            adapter.get_market_calendar("JP")
        with self.assertRaises(ValueError):
            adapter.get_rankings(
                ranking_type="TOP_GAINERS",
                market_country="US",
                duration="realtime",
            )
        with self.assertRaises(ValueError):
            adapter.get_market_indicator_candles(
                "KR_BOND_10Y",
                interval="1m",
            )
        with self.assertRaises(ValueError):
            adapter.get_market_indicator_investor_trading("KR_BOND_10Y")


if __name__ == "__main__":
    unittest.main()

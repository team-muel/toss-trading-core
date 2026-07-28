import importlib.util
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from dataclasses import dataclass
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from toss_trading.broker.toss import TossApiError
from toss_trading.cli import research_collect_toss_reference
from toss_trading.cli.research_validate_bars import (
    main as validate_bars_main,
    validate_parquet,
)
from toss_trading.research import DataLake
from toss_trading.research.providers import (
    SecEdgarClient,
    TiingoEodClient,
    collect_sec_reference_data,
    collect_toss_candle_bundle,
    ingest_tiingo_eod_response,
    ingest_toss_candle_bundle,
)


@dataclass
class FakeResult:
    body: dict
    raw_response_id: str = "response-1"


class FakeTossAdapter:
    def __init__(self) -> None:
        self.calls = []

    def get_candles(self, symbol, *, interval, count, before, adjusted):
        self.calls.append((symbol, interval, count, before, adjusted))
        if before is None:
            return FakeResult(
                {
                    "result": {
                        "candles": [
                            {
                                "timestamp": "2026-01-03T14:00:00+09:00",
                                "openPrice": "100",
                                "highPrice": "102",
                                "lowPrice": "99",
                                "closePrice": "101",
                                "volume": "1000",
                                "currency": "USD",
                            }
                        ],
                        "nextBefore": "cursor-2",
                    }
                }
            )
        return FakeResult(
            {
                "result": {
                    "candles": [
                        {
                            "timestamp": "2026-01-01T14:00:00+09:00",
                            "openPrice": "98",
                            "highPrice": "101",
                            "lowPrice": "97",
                            "closePrice": "100",
                            "volume": "900",
                            "currency": "USD",
                        }
                    ],
                    "nextBefore": None,
                }
            },
            raw_response_id="response-2",
        )


class MissingSymbolError(RuntimeError):
    status_code = 404
    error = {"code": "stock-not-found"}


class MissingSymbolAdapter:
    def get_candles(self, symbol, *, interval, count, before, adjusted):
        raise MissingSymbolError(symbol)


class FakeHttpResponse:
    def __init__(self, body: bytes) -> None:
        self.body = body
        self.closed = False

    def read(self):
        return self.body

    def close(self):
        self.closed = True


class ResearchProviderTest(unittest.TestCase):
    def test_toss_reference_records_unavailable_symbols_as_raw_evidence(self):
        class ReferenceAdapter:
            @staticmethod
            def _result():
                return FakeResult({"result": {}})

            def _batch(self, symbols):
                if "BAD" in symbols:
                    raise TossApiError(
                        endpoint="/test",
                        status_code=404,
                        body={"error": {"code": "stock-not-found"}},
                    )
                return self._result()

            get_stocks = _batch
            get_prices = _batch

            def get_stock_warnings(self, symbol):
                if symbol == "BAD":
                    raise TossApiError(
                        endpoint="/test",
                        status_code=404,
                        body={"error": {"code": "stock-not-found"}},
                    )
                return self._result()

            def __getattr__(self, name):
                if name.startswith("get_"):
                    return lambda *args, **kwargs: self._result()
                raise AttributeError(name)

        with tempfile.TemporaryDirectory() as tmp:
            universe = Path(tmp) / "universe.csv"
            universe.write_text(
                "symbol,enabled\nGOOD,true\nBAD,true\n",
                encoding="utf-8",
            )
            output = StringIO()
            with (
                patch.object(
                    research_collect_toss_reference,
                    "load_toss_credentials_from_env",
                    return_value=object(),
                ),
                patch.object(
                    research_collect_toss_reference,
                    "TossReadOnlyAdapter",
                    return_value=ReferenceAdapter(),
                ),
                patch.object(
                    research_collect_toss_reference,
                    "utc_now",
                    return_value="2026-07-28T00:00:00+00:00",
                ),
                redirect_stdout(output),
            ):
                result = research_collect_toss_reference.main(
                    [
                        "--universe",
                        str(universe),
                        "--output-root",
                        tmp,
                        "--code-revision",
                        "abc123",
                    ]
                )

            payload = json.loads(output.getvalue())
            self.assertEqual(result, 0)
            self.assertEqual(len(payload["failures"]), 3)
            self.assertIn("reference-collection-failures", payload["datasets"])
            manifest_id = payload["manifest_ids"][-1]
            manifests = list(
                Path(tmp).glob(f"catalog/manifests/{manifest_id}.json")
            )
            self.assertEqual(len(manifests), 1)

    def test_toss_collection_paginates_and_stops_at_start_date(self):
        adapter = FakeTossAdapter()
        bundle = collect_toss_candle_bundle(
            adapter,
            symbols=["spy"],
            start_date="2026-01-01",
            adjusted=True,
            retrieved_at=lambda: "2026-07-24T01:00:00+00:00",
        )

        self.assertEqual(len(bundle["pages"]), 2)
        self.assertEqual(adapter.calls[1][3], "cursor-2")
        self.assertNotIn("client_secret", json.dumps(bundle))

    def test_toss_collection_records_only_explicitly_unavailable_symbols(self):
        bundle = collect_toss_candle_bundle(
            MissingSymbolAdapter(),
            symbols=["SPLG"],
            start_date="2026-01-01",
            adjusted=False,
            skip_unavailable_symbols=True,
            retrieved_at=lambda: "2026-07-24T01:00:00+00:00",
        )

        self.assertEqual(bundle["pages"], [])
        self.assertEqual(
            bundle["failures"],
            [
                {
                    "symbol": "SPLG",
                    "status_code": 404,
                    "code": "stock-not-found",
                    "reason": "provider_symbol_unavailable",
                }
            ],
        )

    @unittest.skipUnless(
        importlib.util.find_spec("duckdb") is not None,
        "research extra is not installed",
    )
    def test_toss_adjusted_history_is_estimated_not_total_return(self):
        bundle = collect_toss_candle_bundle(
            FakeTossAdapter(),
            symbols=["SPY"],
            start_date="2026-01-01",
            adjusted=True,
            retrieved_at=lambda: "2026-07-24T01:00:00+00:00",
        )
        with tempfile.TemporaryDirectory() as tmp:
            lake = DataLake(tmp)
            raw, normalized, rows = ingest_toss_candle_bundle(
                lake,
                bundle,
                code_revision="abc123",
                through_date="2026-01-02",
            )
            self.assertEqual(len(raw), 2)
            self.assertEqual(rows, 1)
            self.assertTrue(normalized)
            self.assertTrue(
                all("adjustment=split_adjusted" in item.relative_path for item in normalized)
            )
            self.assertFalse(any("total_return" in item.relative_path for item in normalized))

    @unittest.skipUnless(
        importlib.util.find_spec("duckdb") is not None,
        "research extra is not installed",
    )
    def test_tiingo_writes_raw_and_total_return_partitions(self):
        body = json.dumps(
            [
                {
                    "date": "2026-01-02T00:00:00.000Z",
                    "open": 100,
                    "high": 102,
                    "low": 99,
                    "close": 101,
                    "volume": 1000,
                    "adjOpen": 90,
                    "adjHigh": 91.8,
                    "adjLow": 89.1,
                    "adjClose": 90.9,
                    "adjVolume": 1111.111,
                    "divCash": 1.5,
                    "splitFactor": 1,
                }
            ]
        ).encode()
        with tempfile.TemporaryDirectory() as tmp:
            raw, normalized, rows = ingest_tiingo_eod_response(
                DataLake(tmp),
                symbol="SPY",
                body=body,
                start_date="2026-01-01",
                end_date="2026-01-03",
                retrieved_at="2026-01-04T00:00:00+00:00",
                code_revision="abc123",
            )
            self.assertEqual(raw.dataset, "daily-prices")
            self.assertEqual(rows, 2)
            paths = {item.relative_path for item in normalized}
            self.assertTrue(any("adjustment=raw" in path for path in paths))
            self.assertTrue(any("adjustment=total_return" in path for path in paths))
            output = StringIO()
            with redirect_stdout(output):
                self.assertEqual(
                    validate_bars_main(
                        [
                            "--parquet",
                            str(Path(tmp) / "silver" / "**" / "*.parquet"),
                            "--expected-symbol",
                            "SPY",
                            "--require-adjustment",
                            "total_return",
                        ]
                    ),
                    0,
                )
            self.assertTrue(json.loads(output.getvalue())["ok"])

    @unittest.skipUnless(
        importlib.util.find_spec("duckdb") is not None,
        "research extra is not installed",
    )
    def test_parquet_qa_counts_null_market_values_as_invalid(self):
        import duckdb

        with tempfile.TemporaryDirectory() as tmp:
            parquet = Path(tmp) / "null-close.parquet"
            connection = duckdb.connect()
            try:
                connection.execute(
                    """
                    CREATE TABLE bars AS SELECT
                      'SPY'::VARCHAR AS symbol,
                      TIMESTAMPTZ '2026-01-02 21:00:00+00' AS event_time_utc,
                      TIMESTAMPTZ '2026-01-02 21:05:00+00' AS available_at,
                      DATE '2026-01-02' AS exchange_local_date,
                      '1d'::VARCHAR AS interval,
                      100::DOUBLE AS open,
                      102::DOUBLE AS high,
                      99::DOUBLE AS low,
                      NULL::DOUBLE AS close,
                      1000::DOUBLE AS volume,
                      'USD'::VARCHAR AS currency,
                      'regular'::VARCHAR AS session,
                      'raw'::VARCHAR AS adjustment,
                      'test-provider'::VARCHAR AS source,
                      'v1'::VARCHAR AS source_revision,
                      'raw-1'::VARCHAR AS raw_manifest_id,
                      'ok'::VARCHAR AS quality_flag
                    """
                )
                connection.execute(
                    f"COPY bars TO '{str(parquet).replace(chr(39), chr(39) * 2)}' "
                    "(FORMAT PARQUET)"
                )
            finally:
                connection.close()

            result = validate_parquet(str(parquet))
            self.assertFalse(result["ok"])
            self.assertEqual(result["invalid_rows"], 1)

    def test_tiingo_token_is_sent_only_in_authorization_header(self):
        captured = {}

        def opener(request, timeout):
            captured["url"] = request.full_url
            captured["authorization"] = request.get_header("Authorization")
            captured["timeout"] = timeout
            return FakeHttpResponse(b"[]")

        client = TiingoEodClient("secret-token", opener=opener)
        self.assertEqual(
            client.fetch("SPY", start_date="2026-01-01", end_date="2026-01-02"),
            b"[]",
        )
        self.assertEqual(captured["authorization"], "Token secret-token")
        self.assertNotIn("secret-token", captured["url"])

    def test_sec_collection_deduplicates_ciks_and_stores_raw_json(self):
        calls = []

        def opener(request, timeout):
            calls.append(request.full_url)
            return FakeHttpResponse(b'{"ok":true}')

        client = SecEdgarClient(
            "test application test@example.com",
            opener=opener,
            minimum_interval_seconds=0,
        )
        with tempfile.TemporaryDirectory() as tmp:
            manifests = collect_sec_reference_data(
                DataLake(tmp),
                client,
                ciks=["0000001234", "1234"],
                code_revision="abc123",
                retrieved_at=lambda: "2026-07-24T01:00:00+00:00",
            )
            self.assertEqual(len(manifests), 2)
            self.assertEqual(len(calls), 2)
            self.assertTrue(
                (Path(tmp) / manifests[0].relative_path).read_bytes().startswith(b"{")
            )

    def test_sec_collection_can_include_companyfacts(self):
        calls = []

        def opener(request, timeout):
            calls.append(request.full_url)
            return FakeHttpResponse(b'{"ok":true}')

        client = SecEdgarClient(
            "test application test@example.com",
            opener=opener,
            minimum_interval_seconds=0,
        )
        with tempfile.TemporaryDirectory() as tmp:
            manifests = collect_sec_reference_data(
                DataLake(tmp),
                client,
                ciks=["1234"],
                code_revision="abc123",
                retrieved_at=lambda: "2026-07-24T01:00:00+00:00",
                include_companyfacts=True,
            )

            self.assertEqual(len(manifests), 3)
            self.assertEqual(
                {manifest.dataset for manifest in manifests},
                {"company-tickers", "submissions", "companyfacts"},
            )
            self.assertTrue(any("/api/xbrl/companyfacts/" in url for url in calls))


if __name__ == "__main__":
    unittest.main()

import io
import urllib.error
import unittest
from datetime import date
import tempfile
from pathlib import Path

from toss_trading.cli.research_collect_fred import (
    MAX_REALTIME_WINDOW_DAYS,
    FredObservationsClient,
    _atomic_cache_json,
    _cache_is_complete,
    _realtime_windows,
    _write_history_cache,
)


class ResearchFredTest(unittest.TestCase):
    def test_vintage_cache_requires_contiguous_full_history(self):
        with tempfile.TemporaryDirectory() as tmp:
            cache = Path(tmp)
            envelope = {
                "retrieved_at": "2026-08-09T00:00:00+00:00",
                "request": {"series_id": "UNRATE"},
                "response": {"observations": []},
            }
            _write_history_cache(cache, "UNRATE", [envelope])
            _atomic_cache_json(
                cache / "complete.json",
                {
                    "realtime_start": "2004-01-01",
                    "realtime_end": "2026-08-09",
                    "series": ["UNRATE"],
                },
            )

            self.assertTrue(
                _cache_is_complete(
                    cache,
                    series_ids=["UNRATE"],
                    observation_start="2004-01-01",
                    incremental_start="2026-05-12",
                )
            )
            self.assertFalse(
                _cache_is_complete(
                    cache,
                    series_ids=["UNRATE", "CPIAUCSL"],
                    observation_start="2004-01-01",
                    incremental_start="2026-05-12",
                )
            )

    def test_long_realtime_range_is_split_without_gaps(self):
        windows = _realtime_windows("2004-01-01", "2010-01-01")

        self.assertEqual(windows[0][0], "2004-01-01")
        self.assertEqual(windows[-1][1], "2010-01-01")
        for index, (start, end) in enumerate(windows):
            span = (date.fromisoformat(end) - date.fromisoformat(start)).days + 1
            self.assertLessEqual(span, MAX_REALTIME_WINDOW_DAYS)
            if index:
                previous_end = date.fromisoformat(windows[index - 1][1])
                self.assertEqual(
                    date.fromisoformat(start).toordinal(),
                    previous_end.toordinal() + 1,
                )

    def test_http_error_reports_status_without_disclosing_api_key(self):
        api_key = "a" * 32

        def opener(request, timeout):
            raise urllib.error.HTTPError(
                request.full_url,
                400,
                "Bad Request",
                {},
                io.BytesIO(b'{"error_message":"bad request"}'),
            )

        client = FredObservationsClient(api_key, opener=opener)
        with self.assertRaisesRegex(RuntimeError, "HTTP status 400") as error:
            client.fetch_revisions(
                "DTB3",
                realtime_start="2026-01-01",
                realtime_end="2026-01-02",
                observation_start="2004-01-01",
            )

        self.assertNotIn(api_key, str(error.exception))

    def test_transient_timeout_is_retried_with_longer_read_timeout(self):
        calls = []
        delays = []

        def opener(request, timeout):
            calls.append(timeout)
            if len(calls) < 3:
                raise TimeoutError("temporary read timeout")
            return io.BytesIO(b'{"observations":[]}')

        client = FredObservationsClient(
            "a" * 32,
            opener=opener,
            sleeper=delays.append,
        )
        body = client.fetch_revisions(
            "DTB3",
            realtime_start="2026-01-01",
            realtime_end="2026-01-02",
            observation_start="2004-01-01",
        )

        self.assertEqual(body, b'{"observations":[]}')
        self.assertEqual(calls, [90, 90, 90])
        self.assertEqual(delays, [1.0, 2.0])


if __name__ == "__main__":
    unittest.main()

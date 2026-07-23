import unittest

from toss_trading.alpha.datafields import (
    attention_datafield,
    sentiment_datafield,
)


def make_fetcher(responses):
    """Build an offline fetcher returning canned bodies keyed by query."""

    def fetcher(search_type, query, display, sort):
        return responses[query]

    return fetcher


class NaverDatafieldTest(unittest.TestCase):
    def test_attention_is_log1p_of_total(self):
        fetcher = make_fetcher(
            {
                "삼성전자": {"total": 999.0, "items": []},
                "현대차": {"total": 0.0, "items": []},
            }
        )
        snapshot = attention_datafield(
            {"005930": "삼성전자", "005380": "현대차"},
            as_of="2026-07-22T00:00:00Z",
            fetcher=fetcher,
        )
        self.assertAlmostEqual(snapshot.values["005930"], __import__("math").log1p(999.0))
        self.assertAlmostEqual(snapshot.values["005380"], 0.0)
        self.assertFalse(snapshot.stale)
        self.assertEqual(snapshot.source, "naver_hub")

    def test_attention_marks_missing_and_stale(self):
        fetcher = make_fetcher({"삼성전자": {"items": []}})  # no total field
        snapshot = attention_datafield(
            {"005930": "삼성전자"},
            as_of="2026-07-22T00:00:00Z",
            fetcher=fetcher,
        )
        self.assertIn("005930", snapshot.missing)
        self.assertTrue(snapshot.stale)

    def test_sentiment_polarity_direction(self):
        fetcher = make_fetcher(
            {
                "삼성전자": {
                    "items": [
                        {"title": "삼성전자 <b>급등</b> 신고가 돌파", "description": "호재"},
                        {"title": "삼성전자 실적 개선 기대", "description": "성장"},
                    ]
                },
                "한전": {
                    "items": [
                        {"title": "한전 <b>급락</b> 적자 쇼크", "description": "악재"},
                    ]
                },
            }
        )
        snapshot = sentiment_datafield(
            {"005930": "삼성전자", "015760": "한전"},
            as_of="2026-07-22T00:00:00Z",
            fetcher=fetcher,
        )
        self.assertGreater(snapshot.values["005930"], 0.0)
        self.assertLess(snapshot.values["015760"], 0.0)
        # squashed into (-1, 1)
        self.assertLessEqual(abs(snapshot.values["005930"]), 1.0)

    def test_no_credentials_default_fetcher_is_not_invoked_when_injected(self):
        # Injecting a fetcher must avoid any environment/credential lookup.
        called = {"n": 0}

        def fetcher(search_type, query, display, sort):
            called["n"] += 1
            return {"total": 1.0, "items": []}

        attention_datafield({"x": "쿼리"}, as_of="t", fetcher=fetcher)
        self.assertEqual(called["n"], 1)


if __name__ == "__main__":
    unittest.main()

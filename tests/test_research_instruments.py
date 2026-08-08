from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from toss_trading.data.universe import load_instrument_mappings, load_universe
from toss_trading.research.instruments import (
    build_instrument_lifetime_index,
    load_corporate_actions,
    load_instrument_history,
    observation_within_instrument_lifetime,
    resolve_provider_symbol,
    validate_instrument_history,
    validate_point_in_time_dates,
)


class ResearchInstrumentTests(unittest.TestCase):
    def setUp(self) -> None:
        self.mappings = load_instrument_mappings("data/instrument_master.csv")
        self.aliases = load_instrument_history("data/instrument_history.csv")
        self.actions = load_corporate_actions("data/corporate_actions.csv")

    def test_registry_resolves_spym_provider_aliases_without_lookahead(self) -> None:
        validate_instrument_history(self.mappings, self.aliases, self.actions)
        self.assertEqual(
            resolve_provider_symbol(
                self.aliases,
                canonical_symbol="SPYM",
                provider="tiingo-eod",
                as_of="2026-08-08",
            ),
            "SPLG",
        )
        self.assertEqual(
            resolve_provider_symbol(
                self.aliases,
                canonical_symbol="SPYM",
                provider="toss-openapi",
                as_of="2026-08-08",
            ),
            "SPYM",
        )
        with self.assertRaisesRegex(ValueError, "not uniquely resolvable"):
            resolve_provider_symbol(
                self.aliases,
                canonical_symbol="SPYM",
                provider="toss-openapi",
                as_of="2025-10-30",
            )

    def test_observation_before_listing_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "outside instrument lifetime"):
            validate_point_in_time_dates(self.mappings, [("SGOV", "2020-05-25")])

    def test_predecessor_history_is_excluded_from_current_fund_lifetime(self) -> None:
        lifetimes = build_instrument_lifetime_index(self.mappings)
        self.assertFalse(
            observation_within_instrument_lifetime(
                lifetimes,
                "SMH",
                "2004-01-02",
            )
        )
        self.assertTrue(
            observation_within_instrument_lifetime(
                lifetimes,
                "SMH",
                "2011-12-20",
            )
        )

    def test_registry_contains_every_enabled_universe_member(self) -> None:
        enabled = {item.symbol for item in load_universe("data/universe.csv") if item.enabled}
        canonical = {item.ticker for item in self.mappings}
        self.assertEqual(enabled, canonical)
        self.assertEqual(len(enabled), 15)

    def test_open_ended_interval_must_be_last(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "history.csv"
            path.write_text(
                "instrument_id,canonical_symbol,provider,provider_symbol,effective_from,effective_to,event_type,source_url,reviewed_at\n"
                "US:SPY,SPY,all,SPY,1993-01-22,,listing,https://example.test/a,2026-08-08\n"
                "US:SPY,SPY,all,SPY,2020-01-01,,ticker,https://example.test/b,2026-08-08\n",
                encoding="utf-8",
            )
            aliases = load_instrument_history(path)
            with self.assertRaisesRegex(ValueError, "open-ended"):
                validate_instrument_history([self.mappings[0]], aliases)


if __name__ == "__main__":
    unittest.main()

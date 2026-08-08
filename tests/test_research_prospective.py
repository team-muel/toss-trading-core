import tempfile
import unittest
from pathlib import Path

from toss_trading.research.prospective import (
    append_collection_observation,
    append_run_completion,
    assess_collection_continuity,
    load_collection_observations,
)


class ResearchProspectiveTest(unittest.TestCase):
    def test_append_observation_is_idempotent_and_uses_common_latest_date(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "observations.jsonl"
            kwargs = {
                "provider": "tiingo-eod",
                "adjustment": "total_return",
                "run_id": "daily-one",
                "code_revision": "abc123",
                "requested_through_date": "2026-08-07",
                "collected_at": "2026-08-08T05:10:11+00:00",
                "latest_dates_by_symbol": {
                    "SPY": "2026-08-07",
                    "QQQ": "2026-08-06",
                },
            }

            first = append_collection_observation(path, **kwargs)
            second = append_collection_observation(path, **kwargs)
            append_run_completion(
                path,
                run_id="daily-one",
                code_revision="abc123",
                completed_at="2026-08-08T05:11:00+00:00",
            )

            self.assertEqual(first, second)
            self.assertEqual(first["complete_through_date"], "2026-08-06")
            self.assertEqual(len(load_collection_observations([path])), 2)

    def test_continuity_accepts_bounded_recovery_and_rejects_late_backfill(self):
        protocol = {
            "prospective_collection_policy": {
                "provider": "tiingo-eod",
                "adjustment": "total_return",
                "maximum_collection_lag_calendar_days": 3,
                "invalid_intervals": [],
            },
            "initial_collection_evidence": [],
        }
        bounded = {
            "provider": "tiingo-eod",
            "adjustment": "total_return",
            "run_id": "recovery",
            "complete_through_date": "2026-08-07",
            "collected_at": "2026-08-08T05:10:11+00:00",
            "state": "collected",
        }
        completed = {
            "run_id": "recovery",
            "completed_at": "2026-08-08T05:11:00+00:00",
            "state": "run_completed",
        }

        valid = assess_collection_continuity(
            protocol,
            ["2026-08-06", "2026-08-07"],
            [bounded, completed],
        )
        late = dict(bounded, collected_at="2026-08-11T05:10:11+00:00")
        invalid = assess_collection_continuity(
            protocol,
            ["2026-08-06", "2026-08-07"],
            [late, completed],
        )

        self.assertEqual(valid["state"], "verified")
        self.assertEqual(valid["verified_trading_days"], 2)
        self.assertEqual(invalid["state"], "invalid_data_gap")
        self.assertEqual(invalid["first_invalid_date"], "2026-08-06")

    def test_uncommitted_failed_run_evidence_is_ignored(self):
        protocol = {
            "prospective_collection_policy": {
                "provider": "tiingo-eod",
                "adjustment": "total_return",
                "maximum_collection_lag_calendar_days": 3,
                "invalid_intervals": [],
            },
            "initial_collection_evidence": [],
        }
        collected_only = {
            "provider": "tiingo-eod",
            "adjustment": "total_return",
            "run_id": "failed-run",
            "complete_through_date": "2026-08-07",
            "collected_at": "2026-08-08T05:10:11+00:00",
            "state": "collected",
        }

        status = assess_collection_continuity(
            protocol,
            ["2026-08-07"],
            [collected_only],
        )

        self.assertEqual(status["state"], "invalid_data_gap")
        self.assertEqual(status["verified_trading_days"], 0)


if __name__ == "__main__":
    unittest.main()

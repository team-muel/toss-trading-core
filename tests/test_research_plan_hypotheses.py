from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from toss_trading.cli.research_plan_hypotheses import plan_hypotheses
from toss_trading.research.hypotheses import HypothesisLedger


class _Planner:
    def __init__(self, proposals: list[dict]) -> None:
        self.proposals = proposals
        self.calls = []

    def propose(self, **kwargs):
        self.calls.append(kwargs)
        return self.proposals


def _proposal() -> dict:
    return {
        "thesis": "다양한 자산의 중기 상대 모멘텀이 비용 후에도 지속되는지 검증한다.",
        "falsification_criteria": ["walk-forward 기준선 미달 시 기각한다."],
        "config": {
            "candidate_symbols": ["SPY", "QQQ", "TLT", "GLD"],
            "cash_symbol": "SGOV",
            "lookback_trading_days": 252,
            "skip_recent_trading_days": 21,
            "top_k": 1,
            "minimum_absolute_momentum": 0.0,
            "walk_forward_train_days": 504,
            "walk_forward_test_days": 126,
        },
    }


def _factor_proposal(family: str) -> dict:
    active = {
        "short_term_reversal": "short_term_reversal",
        "low_volatility": "low_volatility",
    }[family]
    weights = {
        "momentum": 0.0,
        "risk_adjusted_momentum": 0.0,
        "short_term_reversal": 0.0,
        "low_volatility": 0.0,
        "trend_acceleration": 0.0,
    }
    weights[active] = 1.0
    return {
        "strategy_family": family,
        "thesis": "서로 다른 가격 이상현상을 사전 등록 규칙으로 검증한다.",
        "falsification_criteria": ["비용 후 벤치마크 초과 성과가 없으면 폐기한다."],
        "config": {
            "candidate_symbols": ["SPY", "QQQ", "TLT", "GLD"],
            "cash_symbol": "SGOV",
            "factor_weights": weights,
            "long_lookback_trading_days": 126,
            "short_lookback_trading_days": 21,
            "volatility_window_trading_days": 63,
            "skip_recent_trading_days": 0,
            "top_k": 2,
            "weighting": "equal",
            "rebalance_frequency": "monthly",
            "regime_filter": "none",
            "minimum_composite_score": 0.0,
            "walk_forward_train_days": 504,
            "walk_forward_test_days": 126,
        },
    }


class ResearchPlanHypothesesTests(unittest.TestCase):
    def test_planner_registers_once_and_reuses_duplicate_config(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            universe = root / "universe.csv"
            universe.write_text("symbol\nSPY\nQQQ\nTLT\nGLD\nSGOV\n", encoding="utf-8")
            ledger = root / "ledger"
            output = root / "run"
            planner = _Planner([_proposal()])

            first = plan_hypotheses(
                policy_path="config/autonomous_research_policy.json",
                universe_path=universe,
                ledger_dir=ledger,
                output_dir=output,
                project_id="project",
                location="global",
                model="gemini-test",
                planner=planner,
            )
            second = plan_hypotheses(
                policy_path="config/autonomous_research_policy.json",
                universe_path=universe,
                ledger_dir=ledger,
                output_dir=output,
                project_id="project",
                location="global",
                model="gemini-test",
                planner=planner,
                now=datetime(2026, 7, 31, tzinfo=timezone.utc),
            )

            self.assertEqual(len(first["created"]), 1)
            self.assertEqual(second["created"], [])
            self.assertEqual(second["reused"], first["created"])
            self.assertEqual(len(HypothesisLedger(ledger).registered()), 1)

    def test_weekly_limit_is_enforced_across_repeated_runs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            universe = root / "universe.csv"
            universe.write_text("symbol\nSPY\nQQQ\nTLT\nGLD\nSGOV\n", encoding="utf-8")
            planner = _Planner([_proposal(), _proposal(), _proposal()])
            first = plan_hypotheses(
                policy_path="config/autonomous_research_policy.json",
                universe_path=universe,
                ledger_dir=root / "ledger",
                output_dir=root / "run-1",
                project_id="project",
                location="global",
                model="gemini-test",
                planner=planner,
                now=datetime(2026, 7, 27, tzinfo=timezone.utc),
            )
            # Duplicate configs collapse to one registration; pre-fill four more
            # distinct registrations with timestamps in the same ISO week.
            registered = HypothesisLedger(root / "ledger").registered()
            base = registered[0]
            for index in (1, 2, 3, 4):
                clone = dict(base)
                clone["hypothesis_id"] = f"00000000-0000-5000-8000-00000000000{index}"
                clone["config"] = dict(base["config"])
                clone["config"]["lookback_trading_days"] = 126 + 21 * index
                path = root / "ledger" / "hypotheses" / f"{clone['hypothesis_id']}.json"
                path.write_text(
                    __import__("json").dumps(clone, ensure_ascii=False, sort_keys=True, indent=2)
                    + "\n",
                    encoding="utf-8",
                )
            second = plan_hypotheses(
                policy_path="config/autonomous_research_policy.json",
                universe_path=universe,
                ledger_dir=root / "ledger",
                output_dir=root / "run-2",
                project_id="project",
                location="global",
                model="gemini-test",
                planner=planner,
                now=datetime(2026, 7, 31, tzinfo=timezone.utc),
            )

            self.assertEqual(first["created"], [registered[0]["hypothesis_id"]])
            self.assertEqual(second["state"], "weekly_limit_reached")
            self.assertEqual(second["registered_this_week"], 5)

    def test_zero_run_limit_performs_audit_without_calling_model(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            universe = root / "universe.csv"
            universe.write_text("symbol\nSPY\nSGOV\n", encoding="utf-8")
            planner = _Planner([_proposal()])

            result = plan_hypotheses(
                policy_path="config/autonomous_research_policy.json",
                universe_path=universe,
                ledger_dir=root / "ledger",
                output_dir=root / "run",
                project_id="project",
                location="global",
                model="gemini-test",
                planner=planner,
                max_new=0,
            )

            self.assertEqual(result["state"], "cadence_audit")
            self.assertEqual(result["created"], [])
            self.assertEqual(planner.calls, [])

    def test_invalid_model_proposal_is_rejected_without_failing_run(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            universe = root / "universe.csv"
            universe.write_text(
                "symbol\nSPY\nQQQ\nTLT\nGLD\nSGOV\n", encoding="utf-8"
            )
            unsafe = _proposal()
            unsafe["config"]["python_code"] = "import os"
            result = plan_hypotheses(
                policy_path="config/autonomous_research_policy.json",
                universe_path=universe,
                ledger_dir=root / "ledger",
                output_dir=root / "run",
                project_id="project",
                location="global",
                model="gemini-test",
                planner=_Planner([unsafe]),
                max_new=1,
            )

            self.assertEqual(result["state"], "completed")
            self.assertEqual(result["created"], [])
            self.assertEqual(len(result["rejected_invalid"]), 1)

    def test_daily_family_rotation_starts_with_least_researched_direction(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            universe = root / "universe.csv"
            universe.write_text(
                "symbol\nSPY\nQQQ\nTLT\nGLD\nSGOV\n", encoding="utf-8"
            )
            planner = _Planner([_factor_proposal("short_term_reversal")])
            result = plan_hypotheses(
                policy_path="config/autonomous_research_policy.json",
                universe_path=universe,
                ledger_dir=root / "ledger",
                output_dir=root / "run",
                project_id="project",
                location="global",
                model="gemini-test",
                planner=planner,
                max_new=1,
            )

            self.assertEqual(
                result["target_strategy_families"], ["short_term_reversal"]
            )
            self.assertEqual(len(result["created"]), 1)
            created_id = result["created"][0]
            self.assertEqual(
                result["created_families"][created_id], "short_term_reversal"
            )


if __name__ == "__main__":
    unittest.main()

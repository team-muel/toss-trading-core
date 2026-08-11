from __future__ import annotations

import json
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

from toss_trading.cli.research_evaluate_hypotheses import (
    evaluate_registered_hypotheses,
)
from toss_trading.research import PricePoint
from toss_trading.research.candidate_evaluation import block_bootstrap_test
from toss_trading.research.costs import ExecutionCostModel, SlippageTier
from toss_trading.research.hypotheses import (
    HypothesisLedger,
    hypothesis_from_proposal,
    load_research_policy,
)


def _proposal() -> dict:
    return {
        "thesis": "상대적으로 강한 위험자산을 선택하면 비용 후에도 SPY를 웃도는지 검증한다.",
        "falsification_criteria": ["다중검정 보정 후 SPY 대비 우위가 없으면 탈락한다."],
        "config": {
            "candidate_symbols": ["QQQ", "TLT"],
            "cash_symbol": "SGOV",
            "lookback_trading_days": 126,
            "skip_recent_trading_days": 21,
            "top_k": 1,
            "minimum_absolute_momentum": 0.0,
            "walk_forward_train_days": 504,
            "walk_forward_test_days": 126,
        },
    }


def _factor_proposal() -> dict:
    return {
        "strategy_family": "risk_adjusted_momentum",
        "thesis": "변동성 대비 추세가 강한 자산의 상대적 지속성을 검증한다.",
        "falsification_criteria": ["비용 후 SPY 초과 성과가 없으면 폐기한다."],
        "config": {
            "candidate_symbols": ["SPY", "QQQ", "TLT"],
            "cash_symbol": "SGOV",
            "factor_weights": {
                "momentum": 0.0,
                "risk_adjusted_momentum": 1.0,
                "short_term_reversal": 0.0,
                "low_volatility": 0.0,
                "trend_acceleration": 0.0,
            },
            "long_lookback_trading_days": 126,
            "short_lookback_trading_days": 21,
            "volatility_window_trading_days": 63,
            "skip_recent_trading_days": 0,
            "top_k": 1,
            "weighting": "inverse_volatility",
            "rebalance_frequency": "monthly",
            "regime_filter": "none",
            "minimum_composite_score": 0.0,
            "walk_forward_train_days": 504,
            "walk_forward_test_days": 126,
        },
    }


def _points(days: int = 1150) -> list[PricePoint]:
    points: list[PricePoint] = []
    current = date(2018, 1, 1)
    index = 0
    while index < days:
        current += timedelta(days=1)
        if current.weekday() >= 5:
            continue
        index += 1
        values = {
            "SPY": 100 * (1.0004**index),
            "QQQ": 100 * (1.0012**index),
            "TLT": 100 * (0.9998**index),
            "SGOV": 100 * (1.00005**index),
        }
        for symbol, value in values.items():
            points.append(
                PricePoint(
                    date=current.isoformat(),
                    symbol=symbol,
                    total_return_index=f"{value:.12f}",
                    available_at=f"{current.isoformat()}T22:00:00+00:00",
                )
            )
    return points


def _cost_model() -> ExecutionCostModel:
    return ExecutionCostModel(
        schema_version="execution-cost-model-v1",
        commission_bps=10.0,
        minimum_commission_usd=0.0,
        portfolio_notional_usd=10_000.0,
        slippage_tiers=(SlippageTier(None, 2.0),),
        commission_source="test_schedule",
        slippage_source="test_policy",
    )


class CandidateEvaluationTests(unittest.TestCase):
    def test_block_bootstrap_applies_familywise_adjustment(self) -> None:
        result = block_bootstrap_test(
            [0.001] * 300,
            samples=200,
            block_days=21,
            family_size=10,
            familywise_alpha=0.05,
            seed_material="fixed",
        )

        self.assertEqual(result["bonferroni_family_size"], 10)
        self.assertLessEqual(result["bonferroni_adjusted_p_value"], 0.05)
        self.assertTrue(result["passes_adjusted_test"])

    def test_registered_candidate_is_evaluated_but_never_promoted(self) -> None:
        policy = load_research_policy("config/autonomous_research_policy.json")
        hypothesis = hypothesis_from_proposal(
            _proposal(), policy=policy, model="test", registered_at="2026-01-01T00:00:00Z"
        )
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            ledger = HypothesisLedger(root / "ledger")
            ledger.register(hypothesis)
            result = evaluate_registered_hypotheses(
                policy_path="config/autonomous_research_policy.json",
                ledger_dir=root / "ledger",
                output_dir=root / "artifacts",
                run_id="weekly-1",
                code_revision="revision-1",
                data_manifest_ids=["manifest-1"],
                points=_points(),
                execution_cost_model=_cost_model(),
            )

            self.assertEqual(result["registered_count"], 1)
            self.assertEqual(result["evaluated"], [hypothesis.hypothesis_id])
            self.assertFalse(result["promotion_authorized"])
            self.assertEqual(len(result["candidate_results"]), 1)
            self.assertIn("annualized_mean_excess", result["candidate_results"][0])
            evaluation_path = (
                root
                / "ledger"
                / "evaluations"
                / hypothesis.hypothesis_id
                / "weekly-1.json"
            )
            evaluation = json.loads(evaluation_path.read_text(encoding="utf-8"))
            self.assertFalse(evaluation["promotion_authorized"])
            self.assertFalse(evaluation["execution_authorized"])
            self.assertEqual(evaluation["statistical_test"]["bonferroni_family_size"], 1)
            self.assertEqual(
                evaluation["prospective_observation"]["state"], "registered"
            )
            self.assertFalse(
                evaluation["prospective_observation"]["metrics_revealed"]
            )

            repeated = evaluate_registered_hypotheses(
                policy_path="config/autonomous_research_policy.json",
                ledger_dir=root / "ledger",
                output_dir=root / "artifacts",
                run_id="weekly-1",
                code_revision="revision-1",
                data_manifest_ids=["manifest-1"],
                points=_points(),
                execution_cost_model=_cost_model(),
            )
            self.assertEqual(repeated["evaluated"], [])
            self.assertEqual(repeated["reused"], [hypothesis.hypothesis_id])

            collecting = evaluate_registered_hypotheses(
                policy_path="config/autonomous_research_policy.json",
                ledger_dir=root / "ledger",
                output_dir=root / "artifacts",
                run_id="weekly-2",
                code_revision="revision-2",
                data_manifest_ids=["manifest-2"],
                points=_points(),
                execution_cost_model=_cost_model(),
            )
            self.assertEqual(collecting["evaluated"], [hypothesis.hypothesis_id])
            collecting_payload = json.loads(
                (
                    root
                    / "ledger"
                    / "evaluations"
                    / hypothesis.hypothesis_id
                    / "weekly-2.json"
                ).read_text(encoding="utf-8")
            )
            self.assertEqual(collecting_payload["state"], "prospective_collecting")
            self.assertIsNone(collecting_payload["prospective_metrics"])

            completed = evaluate_registered_hypotheses(
                policy_path="config/autonomous_research_policy.json",
                ledger_dir=root / "ledger",
                output_dir=root / "artifacts",
                run_id="weekly-3",
                code_revision="revision-3",
                data_manifest_ids=["manifest-3"],
                points=_points(1500),
                execution_cost_model=_cost_model(),
            )
            self.assertEqual(completed["evaluated"], [hypothesis.hypothesis_id])
            completed_payload = json.loads(
                (
                    root
                    / "ledger"
                    / "evaluations"
                    / hypothesis.hypothesis_id
                    / "weekly-3.json"
                ).read_text(encoding="utf-8")
            )
            self.assertEqual(
                completed_payload["state"],
                "prospective_complete_awaiting_paper_infrastructure",
            )
            self.assertTrue(
                completed_payload["prospective_observation"]["metrics_revealed"]
            )
            self.assertIsInstance(completed_payload["prospective_metrics"], dict)
            self.assertFalse(completed_payload["promotion_authorized"])
            self.assertEqual(completed_payload["paper_stage"]["state"], "not_started")

    def test_unsafe_run_id_cannot_escape_ledger(self) -> None:
        policy = load_research_policy("config/autonomous_research_policy.json")
        hypothesis = hypothesis_from_proposal(
            _proposal(), policy=policy, model="test", registered_at="now"
        )
        with tempfile.TemporaryDirectory() as tmp:
            ledger = HypothesisLedger(tmp)
            ledger.register(hypothesis)
            with self.assertRaisesRegex(ValueError, "unsafe"):
                ledger.record_evaluation(
                    hypothesis_id=hypothesis.hypothesis_id,
                    run_id="../escape",
                    result={"state": "failed"},
                )

    def test_daily_cadence_carries_rejected_candidate_without_retesting(self) -> None:
        policy = load_research_policy("config/autonomous_research_policy.json")
        hypothesis = hypothesis_from_proposal(
            _proposal(), policy=policy, model="test", registered_at="2026-01-01T00:00:00Z"
        )
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            ledger = HypothesisLedger(root / "ledger")
            ledger.register(hypothesis)
            ledger.record_evaluation(
                hypothesis_id=hypothesis.hypothesis_id,
                run_id="weekly-prior",
                result={
                    "state": "historical_not_qualified",
                    "historical_screen_passed": False,
                    "promotion_authorized": False,
                    "execution_authorized": False,
                    "gates": {"multiple_testing_adjusted_benchmark": False},
                },
            )

            result = evaluate_registered_hypotheses(
                policy_path="config/autonomous_research_policy.json",
                ledger_dir=root / "ledger",
                output_dir=root / "artifacts",
                run_id="daily-new",
                code_revision="revision-new",
                data_manifest_ids=["manifest-new"],
                points=[],
                execution_cost_model=_cost_model(),
                evaluation_cadence="daily",
            )

            self.assertEqual(result["evaluated"], [])
            self.assertEqual(result["carried_forward"], [hypothesis.hypothesis_id])
            self.assertFalse(
                (root / "ledger" / "evaluations" / hypothesis.hypothesis_id / "daily-new.json").exists()
            )

    def test_factor_family_uses_same_historical_safety_gates(self) -> None:
        policy = load_research_policy("config/autonomous_research_policy.json")
        hypothesis = hypothesis_from_proposal(
            _factor_proposal(), policy=policy, model="test", registered_at="now"
        )
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            ledger = HypothesisLedger(root / "ledger")
            ledger.register(hypothesis)
            result = evaluate_registered_hypotheses(
                policy_path="config/autonomous_research_policy.json",
                ledger_dir=root / "ledger",
                output_dir=root / "artifacts",
                run_id="factor-1",
                code_revision="revision-factor",
                data_manifest_ids=["manifest-factor"],
                points=_points(),
                execution_cost_model=_cost_model(),
            )

            self.assertEqual(result["evaluated"], [hypothesis.hypothesis_id])
            candidate = result["candidate_results"][0]
            self.assertEqual(candidate["strategy_family"], "risk_adjusted_momentum")
            self.assertFalse(result["promotion_authorized"])
            evaluation = json.loads(
                (
                    root
                    / "ledger"
                    / "evaluations"
                    / hypothesis.hypothesis_id
                    / "factor-1.json"
                ).read_text(encoding="utf-8")
            )
            self.assertIn("multiple_testing_adjusted_benchmark", evaluation["gates"])
            self.assertIn("double_cost_stress_excess_positive", evaluation["gates"])


if __name__ == "__main__":
    unittest.main()

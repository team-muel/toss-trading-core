from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from toss_trading.research.hypotheses import (
    HypothesisLedger,
    VertexHypothesisPlanner,
    hypothesis_from_proposal,
    load_research_policy,
    structural_novelty,
)


class _Response:
    def __init__(self, payload: dict) -> None:
        self.payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return self.payload


class _Session:
    def __init__(self, proposal_payload: dict) -> None:
        self.proposal_payload = proposal_payload
        self.calls = []

    def post(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return _Response(
            {
                "candidates": [
                    {
                        "content": {
                            "parts": [
                                {
                                    "text": json.dumps(
                                        {"hypotheses": [self.proposal_payload]},
                                        ensure_ascii=False,
                                    )
                                }
                            ]
                        }
                    }
                ]
            }
        )


def proposal() -> dict:
    return {
        "thesis": "중기 추세가 이어지되 최근 한 달의 과열을 제외하면 신호가 견고한지 검증한다.",
        "falsification_criteria": [
            "비용 후 walk-forward 성과가 기준선보다 낮으면 기각한다.",
            "전향 표본에서 최소 리밸런싱 횟수를 채우지 못하면 결론을 보류한다.",
        ],
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


def factor_proposal() -> dict:
    return {
        "strategy_family": "short_term_reversal",
        "thesis": "단기 유동성 충격 뒤 과도한 가격 움직임이 일부 되돌려지는지 검증한다.",
        "falsification_criteria": [
            "비용 스트레스 후 SPY 대비 초과수익이 없으면 폐기한다."
        ],
        "config": {
            "candidate_symbols": ["SPY", "QQQ", "TLT", "GLD"],
            "cash_symbol": "SGOV",
            "factor_weights": {
                "momentum": 0.0,
                "risk_adjusted_momentum": 0.0,
                "short_term_reversal": 1.0,
                "low_volatility": 0.0,
                "trend_acceleration": 0.0,
            },
            "long_lookback_trading_days": 126,
            "short_lookback_trading_days": 21,
            "volatility_window_trading_days": 63,
            "skip_recent_trading_days": 0,
            "top_k": 2,
            "weighting": "inverse_volatility",
            "rebalance_frequency": "monthly",
            "regime_filter": "none",
            "minimum_composite_score": 0.0,
            "walk_forward_train_days": 504,
            "walk_forward_test_days": 126,
        },
    }


def macro_proposal() -> dict:
    return {
        "strategy_family": "macro_regime",
        "thesis": "수익률 곡선과 물가·고용·정책금리 방향으로 위험 선호 국면을 구분한다.",
        "falsification_criteria": [
            "ALFRED 빈티지와 비용을 적용한 SPY 대비 성과가 없으면 기각한다."
        ],
        "config": {
            "risk_on_symbols": ["SPY", "QQQ"],
            "defensive_symbols": ["SGOV", "TLT"],
            "cash_symbol": "SGOV",
            "macro_signal_weights": {
                "yield_curve": 0.25,
                "inflation_trend": 0.25,
                "unemployment_trend": 0.25,
                "policy_rate_trend": 0.25,
            },
            "signal_lookback_months": 6,
            "minimum_regime_score": 0.0,
            "rebalance_frequency": "monthly",
            "publication_lag_days": 1,
            "walk_forward_train_days": 504,
            "walk_forward_test_days": 126,
        },
    }


class ResearchHypothesisTests(unittest.TestCase):
    def setUp(self) -> None:
        self.policy = load_research_policy("config/autonomous_research_policy.json")

    def test_policy_bounds_are_valid(self) -> None:
        self.assertEqual(self.policy["minimum_prospective_trading_days"], 252)
        self.assertEqual(self.policy["minimum_prospective_rebalances"], 12)
        self.assertEqual(self.policy["minimum_benchmark_outperformance_ratio"], 0.5)
        self.assertNotIn("minimum_positive_walk_forward_ratio", self.policy)

    def test_proposal_is_content_addressed_and_policy_bounded(self) -> None:
        first = hypothesis_from_proposal(
            proposal(), policy=self.policy, model="gemini-test", registered_at="now"
        )
        second = hypothesis_from_proposal(
            proposal(), policy=self.policy, model="gemini-test", registered_at="later"
        )
        self.assertEqual(first.hypothesis_id, second.hypothesis_id)
        unsafe = proposal()
        unsafe["config"]["cash_symbol"] = "BIL"
        with self.assertRaisesRegex(ValueError, "locked policy"):
            hypothesis_from_proposal(unsafe, policy=self.policy, model="gemini-test")

    def test_ledger_is_immutable_and_keeps_evaluations(self) -> None:
        hypothesis = hypothesis_from_proposal(
            proposal(), policy=self.policy, model="gemini-test", registered_at="now"
        )
        with tempfile.TemporaryDirectory() as tmp:
            ledger = HypothesisLedger(Path(tmp) / "ledger")
            destination, created = ledger.register(
                hypothesis,
                output_dir=Path(tmp) / "run-artifacts",
            )
            _, created_again = ledger.register(hypothesis)
            evaluation = ledger.record_evaluation(
                hypothesis_id=hypothesis.hypothesis_id,
                run_id="weekly-1",
                result={"state": "rejected", "reason": "benchmark_failed"},
            )

            self.assertTrue(created)
            self.assertFalse(created_again)
            self.assertTrue(destination.is_file())
            self.assertTrue(evaluation.is_file())
            payload = json.loads(evaluation.read_text(encoding="utf-8"))
            self.assertEqual(payload["state"], "rejected")

    def test_arbitrary_fields_and_symbols_are_rejected(self) -> None:
        unsafe = proposal()
        unsafe["config"]["python_code"] = "import os"
        with self.assertRaisesRegex(ValueError, "bounded strategy DSL"):
            hypothesis_from_proposal(unsafe, policy=self.policy, model="gemini-test")

        unsafe = proposal()
        unsafe["config"]["candidate_symbols"] = ["SPY", "UNKNOWN"]
        with self.assertRaisesRegex(ValueError, "outside policy"):
            hypothesis_from_proposal(unsafe, policy=self.policy, model="gemini-test")

    def test_vertex_schema_enumerates_the_bounded_config(self) -> None:
        session = _Session(factor_proposal())
        planner = VertexHypothesisPlanner(
            project_id="project",
            model="gemini-test",
            session_factory=lambda: session,
        )

        proposals = planner.propose(
            policy=self.policy,
            registered=[],
            available_symbols=self.policy["allowed_candidate_symbols"],
        )

        self.assertEqual(proposals, [factor_proposal()])
        request = session.calls[0][1]["json"]
        config_schema = request["generationConfig"]["responseSchema"]["properties"][
            "hypotheses"
        ]["items"]["properties"]["config"]
        self.assertEqual(
            config_schema["properties"]["candidate_symbols"]["items"]["enum"],
            sorted(self.policy["allowed_candidate_symbols"]),
        )
        self.assertEqual(
            set(config_schema["required"]), set(factor_proposal()["config"])
        )
        self.assertNotIn(
            "enum",
            config_schema["properties"]["factor_weights"]["properties"][
                "momentum"
            ],
        )
        self.assertNotIn(
            "enum", config_schema["properties"]["long_lookback_trading_days"]
        )

    def test_factor_family_is_bounded_and_content_addressed(self) -> None:
        first = hypothesis_from_proposal(
            factor_proposal(),
            policy=self.policy,
            model="gemini-test",
            registered_at="now",
        )
        self.assertEqual(first.strategy_family, "short_term_reversal")
        self.assertEqual(
            first.config["factor_weights"]["short_term_reversal"], 1.0
        )
        unsafe = factor_proposal()
        unsafe["config"]["factor_weights"]["momentum"] = 1.0
        with self.assertRaisesRegex(ValueError, "do not match"):
            hypothesis_from_proposal(
                unsafe,
                policy=self.policy,
                model="gemini-test",
            )
        outside = factor_proposal()
        outside["config"]["long_lookback_trading_days"] = 125
        with self.assertRaisesRegex(ValueError, "outside policy"):
            hypothesis_from_proposal(
                outside,
                policy=self.policy,
                model="gemini-test",
            )

    def test_macro_family_is_point_in_time_bounded(self) -> None:
        hypothesis = hypothesis_from_proposal(
            macro_proposal(), policy=self.policy, model="gemini-test"
        )
        self.assertEqual(hypothesis.strategy_family, "macro_regime")
        self.assertEqual(hypothesis.config["publication_lag_days"], 1)

        unsafe = macro_proposal()
        unsafe["config"]["publication_lag_days"] = 0
        with self.assertRaisesRegex(ValueError, "locked policy"):
            hypothesis_from_proposal(
                unsafe, policy=self.policy, model="gemini-test"
            )

        misleading = macro_proposal()
        misleading["falsification_criteria"] = [
            "선택하지 않은 VTV 대비 성과가 낮으면 기각한다."
        ]
        with self.assertRaisesRegex(ValueError, "unconfigured assets"):
            hypothesis_from_proposal(
                misleading, policy=self.policy, model="gemini-test"
            )

    def test_vertex_uses_macro_schema_for_macro_rotation(self) -> None:
        session = _Session(macro_proposal())
        planner = VertexHypothesisPlanner(
            project_id="project",
            model="gemini-test",
            session_factory=lambda: session,
        )
        macro_policy = {**self.policy, "target_strategy_families": ["macro_regime"]}

        proposals = planner.propose(
            policy=macro_policy,
            registered=[],
            available_symbols=self.policy["allowed_candidate_symbols"],
        )

        self.assertEqual(proposals, [macro_proposal()])
        request = session.calls[0][1]["json"]
        schema = request["generationConfig"]["responseSchema"]["properties"][
            "hypotheses"
        ]["items"]["properties"]["config"]
        self.assertEqual(set(schema["required"]), set(macro_proposal()["config"]))
        self.assertIn("macro_signal_weights", schema["properties"])

    def test_structural_novelty_rejects_near_duplicates_but_not_new_families(self) -> None:
        first = hypothesis_from_proposal(
            factor_proposal(), policy=self.policy, model="gemini-test"
        )
        duplicate_score, nearest = structural_novelty(
            first, [first.to_dict()]
        )
        self.assertEqual(duplicate_score, 0.0)
        self.assertEqual(nearest, first.hypothesis_id)

        different = factor_proposal()
        different["strategy_family"] = "low_volatility"
        different["config"]["factor_weights"]["short_term_reversal"] = 0.0
        different["config"]["factor_weights"]["low_volatility"] = 1.0
        second = hypothesis_from_proposal(
            different, policy=self.policy, model="gemini-test"
        )
        new_family_score, nearest = structural_novelty(
            second, [first.to_dict()]
        )
        self.assertEqual(new_family_score, 1.0)
        self.assertIsNone(nearest)


if __name__ == "__main__":
    unittest.main()

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
        session = _Session(proposal())
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

        self.assertEqual(proposals, [proposal()])
        request = session.calls[0][1]["json"]
        config_schema = request["generationConfig"]["responseSchema"]["properties"][
            "hypotheses"
        ]["items"]["properties"]["config"]
        self.assertEqual(
            config_schema["properties"]["candidate_symbols"]["items"]["enum"],
            sorted(self.policy["allowed_candidate_symbols"]),
        )
        self.assertEqual(
            set(config_schema["required"]), set(proposal()["config"])
        )


if __name__ == "__main__":
    unittest.main()

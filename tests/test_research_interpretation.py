from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from toss_trading.research.interpretation import (
    VertexResearchInterpreter,
    build_research_evidence,
    deterministic_interpretation,
    interpretation_from_payload,
    load_interpretation,
    save_interpretation,
)


def research_summary(
    run_id: str = "daily-20260730T000000Z-abc123",
    *,
    verified_symbols: int = 14,
    quality_errors: int = 0,
) -> dict:
    failure_symbols = ["SPLG", "QQQM"][: 15 - verified_symbols]
    failures = [
        {
            "symbol": symbol,
            "status_code": 404,
            "code": "stock-not-found",
            "reason": "provider_symbol_unavailable",
        }
        for symbol in failure_symbols
    ]
    return {
        "schema_version": "research-visual-report-v1",
        "run_id": run_id,
        "verified_at": "2026-07-30T00:01:00+00:00",
        "mode": "daily",
        "code_revision": "abc123",
        "ready_for_upload": True,
        "provider_states": {
            "toss": "collected",
            "tiingo": "skipped_license_or_secret_gate",
        },
        "toss": {
            "symbols_requested": 15,
            "raw_pages": verified_symbols,
            "adjusted_pages": verified_symbols,
            "raw_failure_count": 15 - verified_symbols,
            "adjusted_failure_count": 15 - verified_symbols,
            "raw_failures": failures,
            "adjusted_failures": failures,
        },
        "quality": {
            "adjustments": ["raw", "split_adjusted"],
            "duplicate_rows": quality_errors,
            "invalid_rows": 0,
            "coverage_mismatch_rows": 0,
            "error_rows": quality_errors,
            "symbol_count": verified_symbols,
        },
        "artifacts": {
            "source_files": 30,
            "source_bytes": 4096,
            "manifests": 28,
            "parquet_files": 28,
        },
        "strategy": {
            "state": "not_available",
            "reason": "verified_total_return_history_not_available",
            "strategy": None,
            "experiment_id": None,
            "code_revision": None,
            "metrics": {},
        },
    }


def model_payload() -> dict:
    point = {
        "statement": "검증된 사실을 근거로 판단했습니다.",
        "evidence_ids": ["current.quality.error_rows"],
    }
    return {
        "title": "일간 연구 해석",
        "overall_assessment": "품질 검사는 통과했지만 데이터 범위는 제한적입니다.",
        "research_process": [point],
        "key_findings": [point],
        "changes_from_previous": [
            {
                "statement": "이전 실행과 검증 종목 수를 비교했습니다.",
                "evidence_ids": ["delta.symbols_verified", "previous.run_id"],
            }
        ],
        "limitations": [
            {
                "statement": "전략 성과를 결론 내릴 수 없습니다.",
                "evidence_ids": ["current.strategy.state"],
            }
        ],
        "next_checks": [
            {
                "statement": "total-return 이력을 확인해야 합니다.",
                "evidence_ids": ["current.strategy.reason"],
            }
        ],
    }


class FakeResponse:
    def __init__(self, payload: dict) -> None:
        self._payload = payload
        self.raised = False

    def raise_for_status(self) -> None:
        self.raised = True

    def json(self) -> dict:
        return self._payload


class FakeSession:
    def __init__(self, response: FakeResponse) -> None:
        self.response = response
        self.calls: list[tuple[str, dict, int]] = []

    def post(self, url: str, *, json: dict, timeout: int):
        self.calls.append((url, json, timeout))
        return self.response


class ResearchInterpretationTest(unittest.TestCase):
    def test_candidate_and_fixed_baseline_must_be_distinguished(self):
        current = research_summary()
        current["autonomous_research"] = {
            "state": "completed",
            "created_count": 1,
            "registered_count": 1,
            "evaluated_count": 1,
            "historically_qualified_count": 0,
            "promotion_authorized": False,
            "candidate_results": [
                {
                    "hypothesis_id": "candidate-one",
                    "thesis": "후보 가설",
                    "state": "historical_not_qualified",
                    "historical_screen_passed": False,
                    "failed_gates": ["multiple_testing_adjusted_benchmark"],
                    "annualized_mean_excess": -0.1,
                    "adjusted_p_value": 1.0,
                    "cost_stress_annualized_mean_excess": -0.11,
                    "prospective_state": None,
                }
            ],
        }
        evidence = build_research_evidence(current)
        point = {
            "statement": "후보는 SPY 우위 관문을 통과하지 못했습니다.",
            "evidence_ids": [
                "current.autonomous_research.candidate.0.failed_gates"
            ],
        }
        payload = {
            "title": "후보 평가",
            "overall_assessment": "현재 전략은 역사적 검증을 통과하지 못했습니다.",
            "research_process": [point],
            "key_findings": [point],
            "changes_from_previous": [point],
            "limitations": [point],
            "next_checks": [point],
        }

        with self.assertRaisesRegex(ValueError, "conflates"):
            interpretation_from_payload(
                payload, evidence=evidence, source="vertex_ai", model="test"
            )

        payload["overall_assessment"] = (
            "AI 후보는 역사 관문을 통과하지 못했고 고정 기준 전략은 전향 표본을 수집 중입니다."
        )
        result = interpretation_from_payload(
            payload, evidence=evidence, source="vertex_ai", model="test"
        )
        self.assertIn("고정 기준 전략", result.overall_assessment)
    def test_evidence_contains_current_previous_and_deltas(self) -> None:
        current = research_summary()
        previous = research_summary(
            "daily-20260729T000000Z-old",
            verified_symbols=13,
            quality_errors=1,
        )
        evidence = build_research_evidence(current, previous=previous)

        self.assertEqual(evidence.previous_run_id, previous["run_id"])
        self.assertEqual(evidence.facts["delta.symbols_verified"], "1")
        self.assertEqual(evidence.facts["delta.quality_error_rows"], "-1")
        self.assertEqual(
            evidence.facts["current.collection.raw_failure.0.symbol"],
            "SPLG",
        )
        self.assertEqual(len(evidence.digest), 64)
        self.assertNotIn("account_seq", json.dumps(evidence.facts))

    def test_deterministic_fallback_explains_scope_and_limitations(self) -> None:
        current = research_summary()
        evidence = build_research_evidence(current)
        interpretation = deterministic_interpretation(
            current,
            evidence=evidence,
            failure_reason="PermissionDenied",
        )

        self.assertEqual(interpretation.source, "deterministic_fallback")
        self.assertIn("14/15", interpretation.overall_assessment)
        self.assertTrue(
            any(
                "Vertex AI" in point.statement
                for point in interpretation.limitations
            )
        )
        self.assertTrue(
            all(point.evidence_ids for point in interpretation.key_findings)
        )
    def test_available_artifact_is_not_reported_as_validated_when_blocked(self) -> None:
        current = research_summary()
        metrics = {
            "total_return": 0.10,
            "cagr": 0.08,
            "annualized_volatility": 0.12,
            "sharpe_zero_rate": 0.7,
            "max_drawdown": -0.05,
            "calmar": 1.6,
            "turnover": 0.4,
            "trading_days": 252.0,
        }
        current["strategy"] = {
            "state": "available",
            "reason": None,
            "artifact_state": "available",
            "methodology_state": "incomplete",
            "methodology_reason": "true_out_of_sample_protocol_not_implemented",
            "benchmark_state": "failed",
            "benchmark_reason": "strategy_did_not_beat_primary_benchmark_gate",
            "promotion_state": "blocked",
            "promotion_reason": "true_out_of_sample_protocol_not_implemented",
            "strategy": "broad_etf_dual_momentum_v1",
            "experiment_id": "experiment-1",
            "code_revision": "abc123",
            "metrics": metrics,
            "benchmark_metrics": metrics,
        }
        evidence = build_research_evidence(current)
        interpretation = deterministic_interpretation(current, evidence=evidence)

        self.assertIn("승격이 차단", interpretation.overall_assessment)
        self.assertTrue(
            any("승격이 차단" in point.statement for point in interpretation.limitations)
        )


    def test_vertex_request_uses_structured_json_and_known_evidence(self) -> None:
        current = research_summary()
        previous = research_summary("daily-20260729T000000Z-old")
        evidence = build_research_evidence(current, previous=previous)
        response = FakeResponse(
            {
                "modelVersion": "gemini-test-001",
                "candidates": [
                    {
                        "finishReason": "STOP",
                        "content": {
                            "parts": [
                                {
                                    "text": json.dumps(
                                        model_payload(),
                                        ensure_ascii=False,
                                    )
                                }
                            ]
                        },
                    }
                ],
            }
        )
        session = FakeSession(response)
        interpreter = VertexResearchInterpreter(
            project_id="test-project",
            model="gemini-test",
            session_factory=lambda: session,
        )
        interpretation = interpreter.interpret(evidence)

        self.assertEqual(interpretation.source, "vertex_ai")
        self.assertEqual(interpretation.model, "gemini-test-001")
        self.assertEqual(len(session.calls), 1)
        url, request, timeout = session.calls[0]
        self.assertIn("projects/test-project/locations/global", url)
        self.assertEqual(timeout, 60)
        self.assertEqual(
            request["generationConfig"]["responseMimeType"],
            "application/json",
        )
        self.assertIn(
            "current.quality.error_rows",
            request["contents"][0]["parts"][0]["text"],
        )

    def test_unknown_model_evidence_is_rejected(self) -> None:
        current = research_summary()
        evidence = build_research_evidence(current)
        payload = model_payload()
        payload["changes_from_previous"][0]["evidence_ids"] = ["invented.fact"]
        response = FakeResponse(
            {
                "candidates": [
                    {
                        "finishReason": "STOP",
                        "content": {"parts": [{"text": json.dumps(payload)}]},
                    }
                ]
            }
        )
        interpreter = VertexResearchInterpreter(
            project_id="test-project",
            session_factory=lambda: FakeSession(response),
        )
        with self.assertRaisesRegex(ValueError, "unknown evidence"):
            interpreter.interpret(evidence)

    def test_no_impact_claim_is_rejected_even_with_known_evidence(self) -> None:
        current = research_summary()
        evidence = build_research_evidence(current)
        payload = model_payload()
        payload["key_findings"][0]["statement"] = (
            "수집 실패가 전체 데이터셋에 영향을 미치지 않았습니다."
        )
        response = FakeResponse(
            {
                "candidates": [
                    {
                        "finishReason": "STOP",
                        "content": {"parts": [{"text": json.dumps(payload)}]},
                    }
                ]
            }
        )
        interpreter = VertexResearchInterpreter(
            project_id="test-project",
            session_factory=lambda: FakeSession(response),
        )
        with self.assertRaisesRegex(ValueError, "unsupported no-impact"):
            interpreter.interpret(evidence)

    def test_saved_interpretation_is_bound_to_evidence_digest(self) -> None:
        current = research_summary()
        evidence = build_research_evidence(current)
        interpretation = deterministic_interpretation(
            current,
            evidence=evidence,
        )
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "interpretation.json"
            save_interpretation(interpretation, path)
            loaded = load_interpretation(path, evidence=evidence)
            self.assertEqual(loaded.evidence_digest, evidence.digest)

            changed = build_research_evidence(
                research_summary(verified_symbols=13)
            )
            with self.assertRaisesRegex(ValueError, "evidence_digest"):
                load_interpretation(path, evidence=changed)


if __name__ == "__main__":
    unittest.main()

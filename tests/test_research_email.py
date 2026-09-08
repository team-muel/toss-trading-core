import base64
import json
import tempfile
import unittest
from email import policy
from email.parser import BytesParser
from pathlib import Path

from toss_trading.research.email_digest import (
    EmailDeliveryLedger,
    GmailApiClient,
    build_gmail_message,
    deliver_research_digest,
    render_research_digest,
)
from toss_trading.research.interpretation import (
    build_research_evidence,
    interpretation_from_payload,
)


def summary() -> dict:
    return {
        "schema_version": "research-visual-report-v1",
        "run_id": "daily-20260730T000000Z-abc123",
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
            "raw_pages": 14,
            "adjusted_pages": 14,
            "raw_failure_count": 1,
            "adjusted_failure_count": 1,
        },
        "quality": {
            "adjustments": ["raw", "split_adjusted"],
            "duplicate_rows": 0,
            "invalid_rows": 0,
            "coverage_mismatch_rows": 0,
            "error_rows": 0,
            "symbol_count": 14,
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


class FakeResponse:
    def __init__(self, payload: dict) -> None:
        self.payload = json.dumps(payload).encode()
        self.closed = False

    def read(self):
        return self.payload

    def close(self):
        self.closed = True


class ResearchEmailTest(unittest.TestCase):
    def test_vertex_interpretation_drives_subject_and_narrative(self):
        evidence = build_research_evidence(summary())
        point = {
            "statement": "이번 실행에서 실제로 관찰한 의미를 설명합니다.",
            "evidence_ids": ["current.quality.error_rows"],
        }
        interpretation = interpretation_from_payload(
            {
                "title": "수집 범위는 제한됐지만 행 품질은 안정적",
                "overall_assessment": "숫자 나열이 아닌 이번 실행의 종합 해석입니다.",
                "research_process": [point],
                "key_findings": [point],
                "changes_from_previous": [
                    {
                        "statement": "첫 실행이므로 비교 기준선으로 사용합니다.",
                        "evidence_ids": ["comparison.previous_run"],
                    }
                ],
                "limitations": [point],
                "next_checks": [point],
            },
            evidence=evidence,
            source="vertex_ai",
            model="gemini-test",
        )
        digest = render_research_digest(
            summary(),
            interpretation=interpretation,
        )

        self.assertIn("[DAILY][전략없음][AI해석포함]", digest.subject)
        self.assertNotIn("수집 범위는 제한됐지만", digest.subject)
        self.assertIn("검증된 사실", digest.text_body)
        self.assertIn("AI 해석", digest.text_body)
        self.assertIn("종합 해석", digest.text_body)
        self.assertIn("current.quality.error_rows", digest.text_body)

    def test_digest_contains_summary_but_not_account_data(self):
        digest = render_research_digest(
            summary(),
            dashboard_url="https://console.cloud.google.com/monitoring/dashboards",
        )

        self.assertIn("[DAILY][전략없음][자동설명]", digest.subject)
        self.assertIn("핵심 결과와 의미", digest.text_body)
        self.assertIn("요청 15개 / 검증 14개", digest.text_body)
        self.assertIn("연구 실행 검증", digest.text_body)
        self.assertIn("전략 수익성 검증", digest.text_body)
        self.assertIn("Live 주문 상태: 비활성화", digest.text_body)
        self.assertIn("deterministic_fallback", digest.text_body)
        self.assertNotIn("account_seq", digest.text_body)
        self.assertNotIn("2500 USD", digest.text_body)
        self.assertIn("GCP 상세 대시보드 열기", digest.html_body)

    def test_collecting_subject_and_facts_cannot_imply_validation(self):
        current = summary()
        current["strategy"] = {
            "state": "available",
            "artifact_state": "available",
            "methodology_state": "collecting",
            "benchmark_state": "not_evaluated",
            "promotion_state": "blocked",
            "prospective_state": "collecting",
            "prospective_observed_days": 0,
            "prospective_required_days": 126,
            "metrics": {"total_return": None},
        }
        digest = render_research_digest(current)

        self.assertIn("[검증대기 0/126]", digest.subject)
        self.assertIn("새로 검증된 전략은 없습니다", digest.text_body)
        self.assertIn("성과를 공개하지 않음", digest.text_body)

    def test_ai_candidate_fact_is_explicitly_not_a_promotion(self):
        current = summary()
        current["autonomous_research"] = {
            "state": "completed",
            "created_count": 2,
            "registered_count": 5,
            "evaluated_count": 5,
            "historically_qualified_count": 1,
            "promotion_authorized": False,
            "candidate_results": [
                {
                    "hypothesis_id": "candidate-one",
                    "state": "historical_not_qualified",
                    "annualized_mean_excess": -0.10,
                    "adjusted_p_value": 1.0,
                    "failed_gates": [
                        "multiple_testing_adjusted_benchmark",
                        "double_cost_stress_excess_positive",
                    ],
                }
            ],
        }

        digest = render_research_digest(current)

        self.assertIn("과거자료 평가 5개", digest.text_body)
        self.assertIn("전향관찰 후보 1개", digest.text_body)
        self.assertIn("승격 또는 주문 실행을 승인하지 않음", digest.text_body)
        self.assertIn("SPY 대비 연율 평균 초과수익 -10.00%", digest.text_body)
        self.assertIn("다중검정 보정 p=1.000", digest.text_body)

    def test_macro_candidate_email_explains_actual_regime_configuration(self):
        current = summary()
        current["autonomous_research"] = {
            "state": "completed",
            "created_count": 1,
            "registered_count": 1,
            "evaluated_count": 1,
            "historically_qualified_count": 0,
            "promotion_authorized": False,
            "candidate_results": [
                {
                    "hypothesis_id": "macro-one",
                    "strategy_family": "macro_regime",
                    "state": "historical_not_qualified",
                    "annualized_mean_excess": -0.01,
                    "adjusted_p_value": 1.0,
                    "failed_gates": ["multiple_testing_adjusted_benchmark"],
                    "config": {
                        "risk_on_symbols": ["SPY", "QQQ"],
                        "defensive_symbols": ["SGOV", "TLT"],
                        "macro_signal_weights": {
                            "yield_curve": 0.5,
                            "inflation_trend": 0.5,
                        },
                        "signal_lookback_months": 6,
                        "minimum_regime_score": 0.0,
                        "publication_lag_days": 1,
                    },
                }
            ],
        }

        digest = render_research_digest(current)

        self.assertIn("거시 후보 설정", digest.text_body)
        self.assertIn("위험선호=SPY,QQQ", digest.text_body)
        self.assertIn("ALFRED 공개지연=1일", digest.text_body)

    def test_message_is_utf8_multipart_and_rejects_header_injection(self):
        digest = render_research_digest(summary())
        raw = build_gmail_message(
            digest,
            sender="researcher@example.com",
            recipient="researcher@example.com",
        )
        message = BytesParser(policy=policy.default).parsebytes(raw)

        self.assertEqual(message["To"], "researcher@example.com")
        self.assertTrue(message.is_multipart())
        self.assertIn("연구 과정", message.get_body().get_content())
        with self.assertRaisesRegex(ValueError, "plain email"):
            build_gmail_message(
                digest,
                sender="researcher@example.com",
                recipient="victim@example.com\nBcc: other@example.com",
            )

    def test_gmail_api_and_delivery_ledger_prevent_repeat_send(self):
        requests = []
        responses = [
            FakeResponse({"access_token": "short-lived-access-token"}),
            FakeResponse({"id": "gmail-message-1"}),
        ]

        def opener(request, timeout):
            requests.append((request, timeout))
            return responses.pop(0)

        client = GmailApiClient(
            client_id="client-id",
            client_secret="client-secret",
            refresh_token="refresh-token",
            opener=opener,
        )
        with tempfile.TemporaryDirectory() as tmp:
            ledger = EmailDeliveryLedger(Path(tmp) / "deliveries.sqlite")
            first = deliver_research_digest(
                summary(),
                sender="researcher@example.com",
                recipient="researcher@example.com",
                client=client,
                ledger=ledger,
            )
            second = deliver_research_digest(
                summary(),
                sender="researcher@example.com",
                recipient="researcher@example.com",
                client=client,
                ledger=ledger,
            )

        self.assertEqual(first["state"], "sent")
        self.assertEqual(second["state"], "already_sent")
        self.assertEqual(len(requests), 2)
        token_request, gmail_request = requests
        self.assertEqual(token_request[0].full_url, "https://oauth2.googleapis.com/token")
        self.assertNotIn("client-secret", token_request[0].full_url)
        self.assertEqual(
            gmail_request[0].full_url,
            "https://gmail.googleapis.com/gmail/v1/users/me/messages/send",
        )
        body = json.loads(gmail_request[0].data)
        decoded = base64.urlsafe_b64decode(body["raw"])
        self.assertIn(b"X-Toss-Research-Run-ID", decoded)


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import base64
import hashlib
import html
import json
import sqlite3
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone
from email.message import EmailMessage
from email.utils import parseaddr
from pathlib import Path
from typing import Any, Callable

from toss_trading.research.interpretation import (
    ResearchInterpretation,
    build_research_evidence,
    deterministic_interpretation,
)


GMAIL_SEND_SCOPE = "https://www.googleapis.com/auth/gmail.send"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GMAIL_SEND_URL = "https://gmail.googleapis.com/gmail/v1/users/me/messages/send"


def _validated_email(value: str, *, field_name: str) -> str:
    normalized = value.strip()
    display_name, address = parseaddr(normalized)
    if (
        display_name
        or address != normalized
        or "@" not in address
        or any(character in normalized for character in "\r\n")
    ):
        raise ValueError(f"{field_name} must be one plain email address")
    return normalized


@dataclass(frozen=True)
class ResearchEmailDigest:
    run_id: str
    mode: str
    subject: str
    text_body: str
    html_body: str

    @property
    def dedupe_key(self) -> str:
        value = f"{self.run_id}:{self.mode}:{self.subject}"
        return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _plain_provider_state(value: str) -> str:
    labels = {
        "collected": "수집 완료",
        "skipped_schedule_or_contact_gate": "일정 또는 연락처 승인 대기",
        "skipped_license_or_secret_gate": "라이선스 또는 API 키 승인 대기",
    }
    return labels.get(value, value)


def _strategy_verdict(summary: dict[str, Any]) -> tuple[str, str]:
    strategy = summary["strategy"]
    promotion = str(strategy.get("promotion_state", "blocked"))
    prospective = strategy.get("prospective_state")
    observed = strategy.get("prospective_observed_days")
    required = strategy.get("prospective_required_days")
    if promotion == "eligible":
        return (
            "연구관문통과",
            "전략이 방법론·벤치마크 연구 관문을 통과했습니다. "
            "아직 paper, shadow 또는 live 승인은 아닙니다.",
        )
    if prospective == "collecting" and isinstance(observed, int) and isinstance(
        required, int
    ):
        return (
            f"검증대기 {observed}/{required}",
            f"새로 검증된 전략은 없습니다. 전향 검증 표본은 {observed}/{required}거래일입니다.",
        )
    if prospective == "invalid_data_gap":
        return (
            "prospective data gap",
            "Prospective collection continuity failed. Performance remains sealed and promotion is blocked.",
        )
    if strategy.get("artifact_state", strategy.get("state")) == "available":
        return (
            "검증미통과",
            "전략 산출물은 존재하지만 연구 관문을 통과하지 못했습니다.",
        )
    return (
        "전략없음",
        "이번 실행에서 검증 가능한 전략 산출물이 생성되지 않았습니다.",
    )


def _verified_fact_lines(summary: dict[str, Any]) -> list[str]:
    strategy = summary["strategy"]
    quality = summary["quality"]
    toss = summary["toss"]
    providers = ", ".join(
        f"{name}={_plain_provider_state(str(state))}"
        for name, state in sorted(summary["provider_states"].items())
    )
    data_progress = summary.get("data_progress", {})
    facts = [
        "연구 실행 상태: 데이터·산출물 무결성 검증 통과",
        f"데이터 공급자: {providers}",
        (
            "종목 범위: "
            f"요청 {int(toss['symbols_requested'])}개 / 검증 {int(quality['symbol_count'])}개"
        ),
        (
            "품질 오류: "
            f"중복 {int(quality['duplicate_rows'])}행, "
            f"무효 {int(quality['invalid_rows'])}행, "
            f"기간 불일치 {int(quality['coverage_mismatch_rows'])}행"
        ),
        (
            "전략 상태: "
            f"산출물 {strategy.get('artifact_state', strategy.get('state', 'not_available'))}, "
            f"방법론 {strategy.get('methodology_state', 'not_evaluated')}, "
            f"벤치마크 {strategy.get('benchmark_state', 'not_evaluated')}, "
            f"승격 {strategy.get('promotion_state', 'blocked')}"
        ),
    ]
    if data_progress.get("state") == "collected":
        facts.append(
            "검증 데이터: "
            f"{data_progress.get('symbol_count', 0)}개 종목, "
            f"총수익률 {data_progress.get('total_return_rows_collected', 0):,}행, "
            f"요청 범위 {data_progress.get('history_start_date')}~"
            f"{data_progress.get('complete_through_date')}"
        )
    if strategy.get("prospective_state") in {"collecting", "invalid_data_gap"}:
        facts.append(
            "성과 공개: 전향 표본이 완성되지 않아 전략·벤치마크 성과를 공개하지 않음"
        )
    failures = []
    for adjustment in ("raw", "adjusted"):
        for failure in toss.get(f"{adjustment}_failures", []):
            if not isinstance(failure, dict):
                continue
            failures.append(
                f"{failure.get('symbol', 'unknown')} {adjustment} "
                f"HTTP {failure.get('status_code', 'unknown')} "
                f"({failure.get('code', failure.get('reason', 'unknown'))})"
            )
    facts.append(
        "수집 예외: " + ("; ".join(failures) if failures else "없음")
    )
    autonomous = summary.get("autonomous_research")
    if isinstance(autonomous, dict):
        facts.append(
            "AI 가설 연구: "
            f"상태 {autonomous.get('state', 'not_scheduled')}, "
            f"신규 {autonomous.get('created_count', 0)}개, "
            f"누적 {autonomous.get('registered_count', '확인 불가')}개, "
            f"과거자료 평가 {autonomous.get('evaluated_count', 0)}개, "
            f"기존 결론 이월 {autonomous.get('carried_forward_count', 0)}개, "
            f"전향관찰 후보 {autonomous.get('historically_qualified_count', 0)}개"
        )
        facts.append(
            "AI 후보 권한: 과거자료 평가만으로 승격 또는 주문 실행을 승인하지 않음"
        )
        gate_labels = {
            "minimum_walk_forward_folds": "워크포워드 표본",
            "benchmark_outperformance_ratio": "워크포워드 벤치마크 초과",
            "multiple_testing_adjusted_benchmark": "다중검정 보정 SPY 우위",
            "double_cost_stress_excess_positive": "비용 2배 스트레스",
        }
        for candidate in autonomous.get("candidate_results", []):
            if not isinstance(candidate, dict):
                continue
            if candidate.get("activity") == "carried_forward":
                continue
            excess = candidate.get("annualized_mean_excess")
            adjusted_p = candidate.get("adjusted_p_value")
            failed = candidate.get("failed_gates", [])
            failed_text = ", ".join(
                gate_labels.get(str(name), str(name)) for name in failed
            ) or "없음"
            excess_text = (
                f"{float(excess):+.2%}" if isinstance(excess, (int, float)) else "미측정"
            )
            p_text = (
                f"{float(adjusted_p):.3f}"
                if isinstance(adjusted_p, (int, float))
                else "미측정"
            )
            facts.append(
                f"AI 후보 {str(candidate.get('hypothesis_id', 'unknown'))[:8]}: "
                f"{candidate.get('state', 'unknown')}, SPY 대비 연율 평균 초과수익 "
                f"{excess_text}, 다중검정 보정 p={p_text}, 실패 관문={failed_text}"
            )
    return facts


def render_research_digest(
    summary: dict[str, Any],
    *,
    interpretation: ResearchInterpretation | None = None,
    dashboard_url: str | None = None,
) -> ResearchEmailDigest:
    if summary.get("schema_version") != "research-visual-report-v1":
        raise ValueError("unsupported reporting summary schema")
    if summary.get("ready_for_upload") is not True:
        raise ValueError("email digest requires an upload-ready summary")

    run_id = str(summary["run_id"])
    mode = str(summary["mode"]).upper()
    if interpretation is None:
        evidence = build_research_evidence(summary)
        interpretation = deterministic_interpretation(
            summary,
            evidence=evidence,
            failure_reason="interpretation_not_supplied",
        )
    if interpretation.current_run_id != run_id:
        raise ValueError("interpretation belongs to a different research run")

    status_label, verdict = _strategy_verdict(summary)
    source_label = "AI해석포함" if interpretation.source == "vertex_ai" else "자동설명"
    data_progress = summary.get("data_progress", {})
    data_date = data_progress.get("complete_through_date") or "데이터 없음"
    autonomous = summary.get("autonomous_research", {})
    created = int(autonomous.get("created_count", 0) or 0)
    evaluated = int(autonomous.get("evaluated_count", 0) or 0)
    subject = (
        f"[Toss Research][{mode}][{status_label}][{source_label}]"
        f"[데이터 {data_date}][신규 {created}·검증 {evaluated}]"
    )
    verified_facts = _verified_fact_lines(summary)

    sections = (
        ("연구 과정", interpretation.research_process),
        ("핵심 결과와 의미", interpretation.key_findings),
        ("이전 실행 대비 변화", interpretation.changes_from_previous),
        ("해석의 한계", interpretation.limitations),
        ("다음 확인 항목", interpretation.next_checks),
    )
    lines = [
        f"오늘의 결론: {verdict}",
        "",
        "검증된 사실 — 이 부분이 보고서의 권위 있는 기록입니다",
        *(f"- {fact}" for fact in verified_facts),
        "",
        "AI 해석 — 이해를 돕는 참고 설명이며 검증 사실 자체가 아닙니다",
        f"- {interpretation.overall_assessment}",
        "",
    ]
    for heading, points in sections:
        lines.append(heading)
        for point in points:
            references = ", ".join(point.evidence_ids)
            lines.append(f"- {point.statement} [근거: {references}]")
        lines.append("")
    lines.extend(
        [
            "보고서 추적 정보",
            f"- 실행 ID: {run_id}",
            f"- 이전 실행 ID: {interpretation.previous_run_id or '없음'}",
            f"- 검증 시각: {summary['verified_at']}",
            f"- 코드 리비전: {summary['code_revision']}",
            f"- 해석 방식: {interpretation.source}",
            f"- 근거 묶음 SHA-256: {interpretation.evidence_digest}",
            "",
            "Live 주문 상태: 비활성화",
            "'연구 실행 검증'은 '전략 수익성 검증'과 다른 상태입니다.",
            "이 보고서는 연구 기록이며 투자 권고나 주문 승인이 아닙니다.",
            "계좌번호, 보유수량, API 자격증명, 원본 응답은 포함하지 않습니다.",
        ]
    )
    if dashboard_url:
        lines.extend(["", f"상세 대시보드: {dashboard_url.strip()}"])
    text_body = "\n".join(lines)

    html_lines = [
        f"<h2>오늘의 결론: {html.escape(verdict)}</h2>",
        "<h3>검증된 사실</h3>",
        "<p><strong>이 부분이 보고서의 권위 있는 기록입니다.</strong></p>",
        "<ul>",
        *(f"<li>{html.escape(fact)}</li>" for fact in verified_facts),
        "</ul>",
        "<h3>AI 해석</h3>",
        "<p><em>이해를 돕는 참고 설명이며 검증 사실 자체가 아닙니다.</em></p>",
        f"<p>{html.escape(interpretation.overall_assessment)}</p>",
    ]
    for heading, points in sections:
        html_lines.extend([f"<h3>{html.escape(heading)}</h3>", "<ul>"])
        for point in points:
            references = ", ".join(point.evidence_ids)
            html_lines.append(
                f"<li>{html.escape(point.statement)}"
                f"<br><small>근거: {html.escape(references)}</small></li>"
            )
        html_lines.append("</ul>")
    html_lines.extend(
        [
            "<h3>보고서 추적 정보</h3>",
            "<ul>",
            f"<li>실행 ID: <code>{html.escape(run_id)}</code></li>",
            (
                "<li>이전 실행 ID: <code>"
                f"{html.escape(interpretation.previous_run_id or '없음')}</code></li>"
            ),
            f"<li>검증 시각: {html.escape(str(summary['verified_at']))}</li>",
            (
                "<li>코드 리비전: <code>"
                f"{html.escape(str(summary['code_revision']))}</code></li>"
            ),
            f"<li>해석 방식: {html.escape(interpretation.source)}</li>",
            (
                "<li>근거 묶음 SHA-256: <code>"
                f"{html.escape(interpretation.evidence_digest)}</code></li>"
            ),
            "</ul>",
            "<p><strong>Live 주문 상태: 비활성화</strong></p>",
            "<p><strong>'연구 실행 검증'은 '전략 수익성 검증'과 다른 상태입니다.</strong></p>",
            "<p>이 보고서는 연구 기록이며 투자 권고나 주문 승인이 아닙니다. "
            "계좌번호, 보유수량, API 자격증명, 원본 응답은 포함하지 않습니다.</p>",
        ]
    )
    if dashboard_url:
        safe_url = html.escape(dashboard_url.strip(), quote=True)
        html_lines.append(f'<p><a href="{safe_url}">GCP 상세 대시보드 열기</a></p>')
    return ResearchEmailDigest(
        run_id=run_id,
        mode=mode.lower(),
        subject=subject,
        text_body=text_body,
        html_body="\n".join(html_lines),
    )


def email_delivery_dedupe_key(
    *,
    run_id: str,
    mode: str,
    recipient: str,
) -> str:
    recipient_address = _validated_email(recipient, field_name="recipient")
    value = f"{run_id}:{mode.lower()}:{recipient_address.lower()}"
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def build_gmail_message(
    digest: ResearchEmailDigest,
    *,
    sender: str,
    recipient: str,
) -> bytes:
    sender_address = _validated_email(sender, field_name="sender")
    recipient_address = _validated_email(recipient, field_name="recipient")
    message = EmailMessage()
    message["From"] = sender_address
    message["To"] = recipient_address
    message["Subject"] = digest.subject
    message_id = digest.dedupe_key[:32]
    message["Message-ID"] = f"<{message_id}@toss-research.local>"
    message["X-Toss-Research-Run-ID"] = digest.run_id
    message.set_content(digest.text_body)
    message.add_alternative(digest.html_body, subtype="html")
    return message.as_bytes()


@dataclass
class GmailApiClient:
    client_id: str = field(repr=False)
    client_secret: str = field(repr=False)
    refresh_token: str = field(repr=False)
    opener: Callable[..., Any] = urllib.request.urlopen

    def _request_json(self, request: urllib.request.Request) -> dict[str, Any]:
        response = self.opener(request, timeout=30)
        try:
            payload = json.loads(response.read())
        finally:
            response.close()
        if not isinstance(payload, dict):
            raise RuntimeError("Gmail API returned a non-object response")
        return payload

    def access_token(self) -> str:
        if not all(
            value.strip()
            for value in (self.client_id, self.client_secret, self.refresh_token)
        ):
            raise RuntimeError("Gmail OAuth client and refresh token are required")
        body = urllib.parse.urlencode(
            {
                "client_id": self.client_id,
                "client_secret": self.client_secret,
                "refresh_token": self.refresh_token,
                "grant_type": "refresh_token",
            }
        ).encode("ascii")
        payload = self._request_json(
            urllib.request.Request(
                GOOGLE_TOKEN_URL,
                data=body,
                headers={"Content-Type": "application/x-www-form-urlencoded"},
                method="POST",
            )
        )
        token = str(payload.get("access_token") or "").strip()
        if not token:
            raise RuntimeError("Google OAuth response did not contain an access token")
        return token

    def send(self, raw_message: bytes) -> str:
        raw = base64.urlsafe_b64encode(raw_message).decode("ascii")
        payload = self._request_json(
            urllib.request.Request(
                GMAIL_SEND_URL,
                data=json.dumps({"raw": raw}, separators=(",", ":")).encode("utf-8"),
                headers={
                    "Authorization": f"Bearer {self.access_token()}",
                    "Content-Type": "application/json",
                },
                method="POST",
            )
        )
        message_id = str(payload.get("id") or "").strip()
        if not message_id:
            raise RuntimeError("Gmail API response did not contain a message id")
        return message_id


class EmailDeliveryLedger:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path)
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS sent_email (
              dedupe_key TEXT PRIMARY KEY,
              run_id TEXT NOT NULL,
              mode TEXT NOT NULL,
              recipient_hash TEXT NOT NULL,
              subject TEXT NOT NULL,
              gmail_message_id TEXT NOT NULL,
              sent_at TEXT NOT NULL
            )
            """
        )
        return connection

    def existing_message_id(self, dedupe_key: str) -> str | None:
        connection = self._connect()
        try:
            row = connection.execute(
                "SELECT gmail_message_id FROM sent_email WHERE dedupe_key = ?",
                (dedupe_key,),
            ).fetchone()
        finally:
            connection.close()
        return str(row[0]) if row else None

    def record(
        self,
        digest: ResearchEmailDigest,
        *,
        dedupe_key: str,
        recipient: str,
        gmail_message_id: str,
    ) -> None:
        recipient_hash = hashlib.sha256(
            recipient.strip().lower().encode("utf-8")
        ).hexdigest()
        connection = self._connect()
        try:
            connection.execute(
                """
                INSERT OR IGNORE INTO sent_email (
                  dedupe_key, run_id, mode, recipient_hash, subject,
                  gmail_message_id, sent_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    dedupe_key,
                    digest.run_id,
                    digest.mode,
                    recipient_hash,
                    digest.subject,
                    gmail_message_id,
                    datetime.now(timezone.utc).isoformat(),
                ),
            )
            connection.commit()
        finally:
            connection.close()


def deliver_research_digest(
    summary: dict[str, Any],
    *,
    sender: str,
    recipient: str,
    client: GmailApiClient,
    ledger: EmailDeliveryLedger,
    interpretation: ResearchInterpretation | None = None,
    dashboard_url: str | None = None,
) -> dict[str, str]:
    digest = render_research_digest(
        summary,
        interpretation=interpretation,
        dashboard_url=dashboard_url,
    )
    recipient_address = _validated_email(recipient, field_name="recipient")
    dedupe_key = email_delivery_dedupe_key(
        run_id=digest.run_id,
        mode=digest.mode,
        recipient=recipient_address,
    )
    existing = ledger.existing_message_id(dedupe_key)
    if existing:
        return {"state": "already_sent", "gmail_message_id": existing}

    raw_message = build_gmail_message(
        digest,
        sender=sender,
        recipient=recipient_address,
    )
    message_id = client.send(raw_message)
    ledger.record(
        digest,
        dedupe_key=dedupe_key,
        recipient=recipient_address,
        gmail_message_id=message_id,
    )
    return {"state": "sent", "gmail_message_id": message_id}

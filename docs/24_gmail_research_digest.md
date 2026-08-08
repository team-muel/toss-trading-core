# Vertex AI 기반 개인 Gmail 연구 해석 보고서

## 목적

대시보드의 숫자를 정형 템플릿으로 옮겨 적는 메일이 아니라, 실제 연구 자동화가
수행한 수집·검증·전략 평가 과정을 읽고 결과의 의미와 한계를 설명하는 한국어
연구 보고서를 개인 Gmail로 전달합니다.

대시보드는 상세 수치 확인용으로 유지하고 알림 경로를 다음과 같이 분리합니다.

- 장애·heartbeat 누락: Cloud Monitoring 이메일 알림
- 정상 연구 실행의 과정·결과 해석: Vertex AI 연구 보고서
- Vertex AI 실패: 근거가 확인된 사실만 설명하는 `FACTS` 안전 대체 보고서

Gmail 자체 전송이 실패하면 `research_automation_failed`를 남기므로, Gmail
OAuth와 독립적인 Cloud Monitoring 경보가 장애를 알릴 수 있습니다.

## 보고서가 사용하는 근거

현재 실행의 검증된 `reporting-summary.json`과, 존재할 경우 직전 동일 모드
실행의 같은 파일만 사용합니다. 원본 공급자 응답이나 계좌 데이터 전체를 모델에
전송하지 않습니다.

근거에는 다음이 포함됩니다.

- 공급자별 수집·건너뜀 상태
- 요청 종목 수, 검증 종목 수, raw/adjusted 실패 수
- 중복·유효성·coverage 품질 검사 결과
- 생성된 source·manifest·Parquet 산출물 수와 크기
- 전략 산출물·방법론·벤치마크·승격 상태
- prospective OOS 관측일/필요일과, 표본 완료 후에만 공개되는 전략·SPY 지표
- 이전 동일 모드 실행 대비 품질·coverage·수집 실패·코드 revision 변화

각 사실에는 `current.quality.error_rows` 같은 안정적인 근거 ID가 부여됩니다.
Vertex AI가 만든 모든 연구 과정·핵심 결과·변화·한계·다음 확인 항목은 하나 이상의
실제 근거 ID를 인용해야 합니다. 존재하지 않는 ID를 인용하거나 JSON 계약을
지키지 않은 응답은 폐기합니다.

## 메일 구조

메일 제목은 전략 검증 상태와 해석 출처를 명시합니다. AI가 만든 자유 문구는
제목의 검증 상태에 영향을 주지 않습니다.

```text
[Toss Research][DAILY][검증대기 0/126][AI해석포함]
[Toss Research][WEEKLY][검증미통과][자동설명]
```

본문은 먼저 코드가 계산한 권위 있는 사실을 제시하고, 그 다음 AI 해석을 별도
구역에 둡니다. AI 해석은 사실을 대체하지 않습니다.

1. 검증된 사실 — 실행 무결성, 공급자, 종목, 품질 오류, 전략 관문
2. AI 후보 연구 — 신규·누적·평가·전향관찰 후보 수와 승격 금지 문구
   - 후보별 SPY 대비 연율 평균 초과수익, 다중검정 보정 p-value, 실패 관문
3. AI 해석 — 종합 판단, 과정, 의미, 변화, 한계, 다음 확인
4. 실행 ID·이전 실행 ID·근거 SHA-256

단순 수치 나열, 미래 수익률 추정, 자동 매매 추천, live 주문 승격은 허용하지
않습니다.

## Vertex AI 보안 경계

- 인증: VM attached service account의 Application Default Credentials
- IAM: `roles/aiplatform.user`
- API: `aiplatform.googleapis.com`
- 위치: `global`
- 기본 모델: `gemini-3.1-flash-lite`
- temperature: `0.2`
- 응답: JSON schema로 제한된 구조화 출력
- OpenAI API key 또는 별도 Gemini API key: 사용하지 않음
- 계좌번호, 보유 포지션, API/OAuth credential, 원본 응답: 입력·메일에서 제외

`gemini-3.1-flash-lite`는 현재 public preview이므로 모델 ID는 환경변수로
분리합니다. 모델 수명주기나 안정성 요구가 바뀌면 코드 수정 없이
`RESEARCH_INTERPRETATION_MODEL`만 검증된 후 교체합니다. 모델 호출은 주문
경로와 완전히 분리되어 있으며 결과는 주문 입력으로 사용되지 않습니다.

## Vertex AI 실패 정책

다음 중 하나라도 발생하면 AI 문장을 보내지 않습니다.

- Vertex AI 권한·quota·네트워크 오류
- 빈 응답 또는 비정상 종료
- JSON schema 위반
- 알려지지 않은 evidence ID 인용
- 현재 실행·이전 실행·evidence digest 불일치

이 경우 검증된 사실로만 작성한 `deterministic_fallback` 보고서를 보내고 제목에
`자동설명`을 표시합니다. 동시에 `research_interpretation_failed` 이벤트를 남겨
운영자가 AI 해석이 아니었음을 확인할 수 있게 합니다. Gmail 성공과 AI 해석
성공을 같은 상태로 가장하지 않습니다.

## Gmail 보안 경계

- 발신자와 수신자: 운영자가 지정한 동일한 개인 Gmail 주소
- OAuth 권한: `https://www.googleapis.com/auth/gmail.send` 하나만 사용
- Gmail 비밀번호와 앱 비밀번호: 사용하지 않음
- OAuth client와 refresh token: GCP Secret Manager에만 저장
- 중복 방지: `run_id`·mode·수신자 기준 SQLite delivery ledger
- 실제 개인 주소: 추적되는 저장소가 아니라 VM 환경 파일에만 저장

## 최초 1회 Gmail OAuth 설정

1. Google Cloud 프로젝트 `toss-trading-core-lab`에서 Gmail API를 활성화합니다.
2. Google Auth Platform의 앱 이름을 `Toss Research Mailer`로 지정하고 외부
   사용자를 선택합니다.
3. Gmail API의 `gmail.send` 범위만 추가합니다.
4. 장기 자동화에서는 refresh token의 7일 Testing 제한을 피하도록 앱 상태를
   `In production`으로 둡니다.
5. Desktop app OAuth client를 만들고 JSON 파일을 내려받습니다.
6. 저장소 루트에서 다음 명령을 실행하고 사용할 개인 Gmail로 동의합니다.

```powershell
python scripts/authorize_research_gmail.py `
  --client-json "$HOME\Downloads\client_secret_....json" `
  --project-id toss-trading-core-lab `
  --email <personal-gmail-address>
```

도구는 loopback callback과 PKCE를 사용하며 다음 Secret을 생성하거나 새 버전을
추가합니다. Secret 값은 출력하지 않습니다.

```text
toss-research-gmail-oauth-client-id
toss-research-gmail-oauth-client-secret
toss-research-gmail-oauth-refresh-token
```

Secret 저장을 확인한 뒤 내려받은 OAuth client JSON은 안전하게 삭제합니다.

## GCP 적용 순서

provisioner는 Vertex AI와 Gmail API를 활성화하고 VM service account에
`roles/aiplatform.user` 및 존재하는 Gmail Secret의 accessor 권한을 부여합니다.

```bash
export MONITORING_NOTIFICATION_CHANNEL='projects/toss-trading-core-lab/notificationChannels/<id>'
./scripts/provision_research_automation_gcp.sh
```

새 release를 VM에 설치한 뒤 `/etc/toss-trading/research.env`에 실제 주소와
해석 설정을 둡니다.

```dotenv
RESEARCH_EMAIL_ENABLED=1
RESEARCH_EMAIL_SENDER=<personal-gmail-address>
RESEARCH_EMAIL_RECIPIENT=<personal-gmail-address>
RESEARCH_DASHBOARD_URL=<실제 Cloud Monitoring dashboard URL>
RESEARCH_INTERPRETATION_ENABLED=1
RESEARCH_INTERPRETATION_LOCATION=global
RESEARCH_INTERPRETATION_MODEL=gemini-3.1-flash-lite
```

처음에는 daily 서비스를 수동 실행합니다.

```bash
sudo systemctl start toss-research-automation@daily.service
sudo systemctl status toss-research-automation@daily.service --no-pager
tail -n 50 /home/seoje/toss-trading/research-runtime/research_automation.jsonl
```

AI 보고서가 정상이라면 `research_interpretation_ok`,
`research_email_ok`, `research_automation_ok`가 모두 있어야 합니다.
`research_interpretation_failed`와 `research_email_ok` 조합은 메일은 갔지만
`FACTS` 안전 대체 보고서였다는 뜻입니다.

## 구현 위치

- 근거 추출·이전 실행 비교·Vertex AI 계약:
  `src/toss_trading/research/interpretation.py`
- 해석 보고서 메일·Gmail API·중복 방지:
  `src/toss_trading/research/email_digest.py`
- reporting CLI:
  `src/toss_trading/cli/research_reporting.py`
- GCP runner:
  `scripts/run_research_automation_gcp.sh`
- Gmail OAuth/Secret Manager 도구:
  `scripts/authorize_research_gmail.py`

## 공식 참고 자료

- [Vertex AI Gemini API quickstart](https://docs.cloud.google.com/vertex-ai/generative-ai/docs/start/quickstart)
- [Vertex AI 생성형 API 오류](https://cloud.google.com/vertex-ai/generative-ai/docs/model-reference/api-errors)
- [Vertex AI release notes](https://docs.cloud.google.com/vertex-ai/generative-ai/docs/release-notes)
- [Gmail API로 메일 보내기](https://developers.google.com/workspace/gmail/api/guides/sending)
- [Gmail API OAuth 범위](https://developers.google.com/workspace/gmail/api/auth/scopes)

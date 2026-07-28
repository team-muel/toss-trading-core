# Implementation Roadmap

이 문서는 실제 작업 순서의 기준입니다. phase 이름보다 아래 19개 작업의 순서를 우선합니다.

## Current Sprint Goal

Foundation v1과 초기 24시간 집중 관찰은 완료되었습니다. 현재 목표는
Foundation P0 안전 결함 수정과 total-return 연구 데이터 활성화를 두 개의
독립된 작업선으로 병렬 진행하는 것입니다.

질문은 하나입니다.

```text
내 시스템이 Toss 계좌 상태를 정확히 읽고 저장하고 설명할 수 있는가?
```

이 질문은 live 주문의 선행조건입니다. 그러나 주문을 제출하지 않는 raw
데이터 수집과 백테스트는 계좌 안전 작업과 병렬로 진행합니다. 옵션,
실시간 WebSocket, NAV, 뉴스는 첫 baseline이 필요로 할 때까지 미룹니다.

## Current Status — 2026-07-27

Foundation v0 운영 기반은 완료되었습니다.

- GCP 고정 외부 IP가 Toss Open API 허용 IP에 등록됨
- 전용 VM 서비스 계정과 최소 Secret Manager 접근 권한 적용
- Toss Client ID/Secret 재발급 후 Secret Manager 버전 2만 활성화
- 6시간 주기의 읽기 전용 systemd runner 운영
- 실제 VM에서 accounts, holdings, open orders, buying power, commissions 조회가 모두 2xx로 성공
- `foundation_audit --profile v0-empty-safe` 통과 및 private GCS 백업 업로드 성공
- Cloud Logging 지표와 6개 운영 경보 및 이메일 알림 구성
- GitHub `master` 보호와 필수 CI 검사 적용
- GCP 연구 데이터 daily/weekly 자동화, 독립 실행 snapshot, QA 후 GCS
  승격, Cloud Logging/Monitoring 템플릿과 Cloud Build 검증 경로 구현
- VM 활성 research release `a6c1471`, research daily/weekly timer와 Ops
  Agent가 `active`, `enabled`
- 최신 daily run `daily-20260727T121203Z-a6c1471`이 private GCS와
  BigQuery에 기록되고 `research_automation_ok` 수신
- 15개 ETF 중 14개 품질검사 통과, 중복·OHLC·시간·coverage 오류 0행
- `SPLG`는 Toss에서 raw/adjusted 각각 404로 수집 실패하여 dashboard에
  실패 요청 2건으로 표시
- 18개 research log metric, 전체 경보 11개, 통합 dashboard 운영
- total-return 공급자 미승인 상태이므로 strategy 상태는 의도대로
  `not_available`

Codex의 매일 19:00 KST 24시간 집중 관찰 자동화는 2026-07-23 사용자
승인으로 중지했습니다. 이는 운영 감시 종료가 아닙니다.
`toss-foundation.timer`의 6시간 read-only 실행과 Cloud Monitoring 경보
6개는 계속 활성 상태로 유지합니다.

2026-07-21 수동 국내주식 주문과 수동 해외주식 주문이 각각 즉시 체결되어 보유종목 2개와 매도 가능 수량 2건이 확인되었습니다. Toss 웹 화면 식별자는 Open API `orderId`가 아니었지만, 실제 `status=CLOSED` 조회가 성공해 해외 주문의 진짜 Open API ID를 복구했습니다. 동일 주문 상세, 체결 snapshot/delta, 실수수료, 결제일을 확인했고 `foundation_audit=ok`, `profile=v1-funded-read-only`로 Foundation v1을 통과했습니다. 검증 종목은 전략 universe 밖이므로 계좌·주문 파이프라인 증거로만 사용하며 전략 매수 대상에는 추가하지 않습니다.

## Next Work Priorities

| Priority | Work | Completion evidence | Dependency |
| --- | --- | --- | --- |
| 완료 | Foundation v1 funded read-only | CLOSED 조회로 실제 Open API `orderId`를 복구하고 상세·체결·수수료·결제일 감사 통과 | 2026-07-21 완료 |
| 완료 | 백업 복원 훈련 | 격리 경로에서 SQLite `integrity_check=ok`와 v1 감사 재통과 | 2026-07-21 완료 |
| 완료 | Codex 24시간 집중 관찰 | 2026-07-23 사용자 승인으로 집중 관찰 자동화 중지 | 운영 timer·경보는 계속 유지 |
| 상시 운영 | VM 및 Cloud Monitoring 감시 | 6시간 `toss-foundation.timer`와 Cloud Monitoring 경보 6개 유지 | 종료 대상 아님 |
| 완료 | Foundation 1–5 회귀 점검 | universe/master 1:1, source-health/CI, 실제 v0·v1 GCS 백업 raw replay와 감사 통과 | 2026-07-23 완료 |
| P0-A | 계좌·주문 안전장치 | 실패 run cash backfill, 초기잔고, 통화별 buying-power, 미해결 BLOCK, EOD 대사 | live 계속 차단 |
| P0-B | 저장소 진실성 | branch/CI/release SHA, wheel resource smoke test, monitoring IaC | P0-A와 병렬 |
| 상시 운영 | 연구 데이터 자동화·보고 | daily/weekly timer, GCS, BigQuery, dashboard, research 경보 6개 유지 | 코드 반영, 재배포 필요 |
| P1-A | total-return 공급자 활성화 | Tiingo 약관·키 승인 후 실제 raw/total-return 수집과 QA | 전략 검증의 현재 핵심 blocker |
| P1-B | 데이터 정합성 보강 | `SPLG` vendor symbol 결정, point-in-time universe, corporate action·상장/폐지 이력 | P1-A와 병렬 |
| P1-C | 거시·공시 활성화 | FRED series 권리·키, SEC 연락처 승인 후 자동 gate 해제 | 사용자 승인 필요 |
| P2 | baseline 백테스트 | 비용후 dual-momentum, benchmark, OOS/walk-forward | 검증된 total-return 데이터 |
| P3 | signal safety와 persistent paper | 강제 RiskDecision, stale gate, 체결·잔고·비용 simulator | P0/P2 |
| P6 | shadow live 2주 후 초소형 live 검토 | 2주간 오류 없는 일일 보고와 별도 live 승인 | 모든 선행 gate |

추가 주문은 필요하지 않습니다. 자동 주문, paper planner, 전략 신호는 다음 안전 단계가 구현될 때까지 계속 차단합니다.

2026-07-23 Foundation replay 검증에서는 최신 v0 백업과 기존 v1 백업을 각각 임시
SQLite로 replay했습니다. 두 replay 모두 raw response hash 검증과 해당
profile의 `foundation_audit=ok`를 통과했습니다. 이 과정에서 raw 계좌
응답의 식별자 마스킹 때문에 replay 시 내부 `account_seq`를 복원해야 하는
결함을 발견해 수정했습니다.

P3의 첫 하위 작업으로 execution delta 기반 통화별 cash event와 OPEN 매수
예약현금 계산을 구현했습니다. 부분체결 후 완전체결 시 증분
원금·수수료·세금만 반영하고 재실행 시 중복 반영하지 않습니다. 주문금액을
확정할 수 없는 OPEN 매수는 감사 blocker가 됩니다. 독립적으로 확인된 초기
현금잔고, 미결제/결제완료 잔고와 buying power 대사는 아직 완료되지
않았습니다.

## Ordered Backlog

| Step | Work Item | Output | Go/No-Go Check |
| --- | --- | --- | --- |
| 1 | `universe.csv` 작성 | `data/universe.csv` | live 후보가 현물 ETF/주식으로 제한됨 |
| 2 | `instrument_master` 테이블 작성 | `data/instrument_master.csv`와 SQL table | universe와 master가 1:1로 대사됨 |
| 3 | Toss account/holdings/orders/buying_power adapter 안정화 | Toss adapter + account state replay | token 발급, IP allowlist, holdings/orders/buying power 반복 호출 안정 |
| 4 | `raw_api_response` 저장 구조 추가 | broker/vendor 공통 raw response table | 모든 API 응답 replay 가능 |
| 5 | `source_health` 테이블 추가 | source health snapshot | stale/degraded/blocked 상태 표현 가능 |
| 6 | 계좌·주문 안전장치 완성 | cash ledger + reserved cash + reconciliation report | 통화별 가용현금과 broker constraint 대사 |
| 7 | Tiingo EOD 활성화 | raw/total-return 일봉 + normalized snapshot | 약관·secret gate, timestamp·조정정책 검증 |
| 8 | ETF corporate action 저장 | dividend/split/ticker-history event tables | point-in-time total-return 검증 가능 |
| 9 | FRED/ALFRED 활성화 | revision-aware rate series observations | 권리 registry와 rate hurdle refresh 가능 |
| 10 | SEC ticker-CIK map 추가 | ticker-CIK reference | SEC event를 universe에 연결 |
| 11 | SEC submissions poller 추가 | filing event log | event gate 가능 |
| 12 | feature table 추가 | feature snapshot table | feature와 signal 분리 |
| 13 | ALLOW/REDUCE/BLOCK signal layer 추가 | signal decision table | 매수/축소/차단을 명확히 분리 |
| 14 | Toss paper order planner 연결 | paper order plan + execution simulation | 실제 주문 없이 account replay |
| 15 | stale-data gate 추가 | engine-scoped stale gate | stale source 의존 신호 차단 |
| 16 | shadow live 2주 | live data + paper orders + daily report | broker/account/order 상태 안정 |
| 17 | 초소형 live | micro live runbook | 수익보다 운영 정상성 검증 |
| 18 | Massive REST/WebSocket 추가 | options·실시간 source with heartbeat | 전략 가설, REST fallback과 slow-consumer 대응 |
| 19 | issuer NAV/ROC parser 추가 | audit queue + parser output | ROC 불명 ETF 신규 매수 차단 |

## Sequencing Rules

- 1-5번은 foundation입니다. 이 구간이 끝나기 전 전략 구현을 시작하지 않습니다.
- 6번은 live 계좌·주문 safety입니다. live는 계속 차단하지만 읽기 전용
  데이터 수집과 백테스트는 기다리지 않고 병렬 진행합니다.
- 7-11번은 external minimum stack입니다. Tiingo total-return, FRED/ALFRED,
  SEC를 먼저 활성화하고 Massive와 issuer parser는 뒤로 둡니다.
- 12-15번은 signal safety입니다. feature와 signal decision을 분리하고 stale-data gate를 먼저 완성합니다.
- 16-17번은 live gate입니다. shadow live 2주 없이 초소형 live로 넘어가지 않습니다.
- 18-19번은 확장입니다. Massive WebSocket과 issuer NAV/ROC parser는 기본 장부와 source health가 안정된 뒤 추가합니다.

## Completion Standard

각 단계는 다음 조건을 만족해야 완료로 봅니다.

- raw API 응답이 저장되어 replay 가능
- normalized table이 source와 timestamp를 보존
- stale 또는 mismatch가 신규 주문을 차단
- Toss 계좌 상태가 외부 데이터로 덮어써지지 않음
- 문서, schema, config가 같은 용어를 사용

## Foundation Snapshot Command

IP allowlist와 Toss credential이 준비되면 아래 read-only 명령으로 이번 주 목표를 검증합니다.

```powershell
$env:PYTHONPATH='src'
python -m toss_trading.cli.foundation_snapshot
```

스냅샷이 성공한 뒤에는 아래 audit 명령으로 foundation 완료 증거를 점검합니다.

```powershell
$env:PYTHONPATH='src'
python -m toss_trading.cli.foundation_audit
```

Foundation v1 funded read-only 검증은 실제 현금, 보유종목, 수동 주문, 체결, 수수료, 결제일이 있는 계좌 상태에서 아래 명령으로 수행합니다.

```powershell
$env:PYTHONPATH='src'
python -m toss_trading.cli.foundation_snapshot --target-order-id "<OPEN 상태에서 확보한 orderId>"
python -m toss_trading.cli.foundation_audit --profile v1-funded-read-only
```

v1이 통과하기 전에는 paper order planner와 전략 신호를 붙이지 않습니다.

GCP 고정 IP VM에서는 Secret Manager와 JSONL 로그를 사용하는 runner로 같은 검증을 반복합니다.

```bash
export FOUNDATION_LOAD_GCP_SECRETS=1
export FOUNDATION_AUDIT_PROFILE=v0-empty-safe
./scripts/run_foundation_gcp.sh
```

기본 산출물:

- `runtime/foundation_account_state.sqlite`
- `runtime/foundation_account_state_report.txt`

성공 조건:

- `foundation_snapshot=ok`
- `foundation_audit=ok`
- accounts, holdings, orders, buying power raw response가 `raw_api_response`에 저장됨
- holdings, orders, buying power 정규화 snapshot이 생성됨
- report가 계좌 상태를 사람이 읽을 수 있게 설명함

운영 blocker 처리:

- GCP 고정 외부 IP의 Toss Open API 허용 IP 등록은 2026-07-21 완료되었습니다.
- 새 GCP VM 실행에서 `/oauth2/token`이 `403 access_denied`와 `IP address not allowed`를 다시 반환하면 등록한 IP와 VM의 실제 외부 IP가 같은지 재확인합니다.
- 이 경우 CLI는 `raw_api_response`에 원본 실패 응답을 저장하고, `source_health_snapshot`에 `source_status='blocked'`, `action='register_current_ip_in_toss_openapi_allowlist'`를 남깁니다.

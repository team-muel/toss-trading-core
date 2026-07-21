# Implementation Roadmap

이 문서는 실제 작업 순서의 기준입니다. phase 이름보다 아래 18개 작업의 순서를 우선합니다.

## Current Sprint Goal

이번 주 목표는 Foundation입니다.

질문은 하나입니다.

```text
내 시스템이 Toss 계좌 상태를 정확히 읽고 저장하고 설명할 수 있는가?
```

이 질문에 예라고 답하기 전에는 전략, 옵션 데이터, NAV 데이터, 뉴스 데이터를 붙이지 않습니다. 현재 범위는 Toss 계좌 원장, 거래 대상 universe, instrument mapping입니다.

## Ordered Backlog

| Step | Work Item | Output | Go/No-Go Check |
| --- | --- | --- | --- |
| 1 | `universe.csv` 작성 | `data/universe.csv` | live 후보가 현물 ETF/주식으로 제한됨 |
| 2 | `instrument_master` 테이블 작성 | `data/instrument_master.csv`와 SQL table | universe와 master가 1:1로 대사됨 |
| 3 | Toss account/holdings/orders/buying_power adapter 안정화 | Toss adapter + account state replay | token 발급, IP allowlist, holdings/orders/buying power 반복 호출 안정 |
| 4 | `raw_api_response` 저장 구조 추가 | broker/vendor 공통 raw response table | 모든 API 응답 replay 가능 |
| 5 | `source_health` 테이블 추가 | source health snapshot | stale/degraded/blocked 상태 표현 가능 |
| 6 | Massive REST adapter 추가 | Massive REST raw response + normalized snapshot | secret 미노출, timestamp 정규화 |
| 7 | Massive dividends/splits 저장 | dividend/split event tables | distribution/event gate 입력 가능 |
| 8 | FRED adapter 추가 | rate series observations | rate hurdle batch refresh 가능 |
| 9 | SEC ticker-CIK map 추가 | ticker-CIK reference | SEC event를 universe에 연결 |
| 10 | SEC submissions poller 추가 | filing event log | event gate 가능 |
| 11 | feature table 추가 | feature snapshot table | feature와 signal 분리 |
| 12 | ALLOW/REDUCE/BLOCK signal layer 추가 | signal decision table | 매수/축소/차단을 명확히 분리 |
| 13 | Toss paper order planner 연결 | paper order plan + execution simulation | 실제 주문 없이 account replay |
| 14 | stale-data gate 추가 | engine-scoped stale gate | stale source 의존 신호 차단 |
| 15 | shadow live 2주 | live data + paper orders + daily report | broker/account/order 상태 안정 |
| 16 | 초소형 live | micro live runbook | 수익보다 운영 정상성 검증 |
| 17 | Massive WebSocket 추가 | realtime source with heartbeat | REST fallback과 slow-consumer 대응 |
| 18 | issuer NAV/ROC parser 추가 | audit queue + parser output | ROC 불명 ETF 신규 매수 차단 |

## Sequencing Rules

- 1-5번은 foundation입니다. 이 구간이 끝나기 전 전략 구현을 시작하지 않습니다.
- 6-10번은 external minimum stack입니다. WebSocket과 issuer parser보다 REST, FRED, SEC를 먼저 붙입니다.
- 11-14번은 signal safety입니다. feature와 signal decision을 분리하고 stale-data gate를 먼저 완성합니다.
- 15-16번은 live gate입니다. shadow live 2주 없이 초소형 live로 넘어가지 않습니다.
- 17-18번은 확장입니다. Massive WebSocket과 issuer NAV/ROC parser는 기본 장부와 source health가 안정된 뒤 추가합니다.

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

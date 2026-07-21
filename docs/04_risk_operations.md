# Risk And Operations

## Portfolio Limits

아래 숫자는 시장 법칙이 아니라 starter guardrail입니다. 보고서 숫자를 그대로 자동매매에 넣지 않고, 실제 체결·슬리피지·손실분포·대사 실패율이 쌓인 뒤 calibrated policy로 승격합니다.

| Limit | Starter Intent |
| --- | --- |
| Drawdown warning | 보고서 예시보다 더 낮게 시작 |
| Drawdown kill switch | micro-live 중에는 작은 손실에도 중단 |
| Single trade max loss | NAV 훼손보다 운영 검증을 우선 |
| Single live order notional | 초소형 주문으로 시작 |
| Distribution ETF exposure | NAV/ROC 데이터가 검증되기 전 최소화 |
| Cash buffer | `cashBuyingPower` constraint와 내부 cash ledger 차이를 보고 조정 |

## Kill Switch Conditions

다음 조건에서는 신규 주문을 중단하고 기존 포지션 축소 또는 수동 점검만 허용합니다.

- starter 또는 calibrated drawdown 한도 초과
- 주문 거부율이 한도 초과
- 실체결 슬리피지가 모델 대비 2배 이상으로 3거래일 지속
- Toss holdings와 내부 position ledger 불일치
- Toss OPEN 주문 및 확보된 target order 상세와 내부 order ledger 불일치
- 주문 응답 타임아웃 뒤 접수 여부 확인 전 새 주문을 내야 하는 상황
- `cashBuyingPower` constraint 조회 실패 또는 내부 available cash와 정책 허용 범위 초과
- raw API response 저장 실패
- source health 저장 실패
- `CANCEL_REJECTED` 또는 `REPLACE_REJECTED` 후 원주문 effective state 미확정
- `ORDER`, `ORDER_HISTORY`, `ORDER_INFO` token bucket degraded
- 외부 피드 stale 상태에서 해당 피드 의존 신호가 계속 발생
- SEC/issuer event gate가 실패했는데 분배형 ETF 신규 매수가 발생

## Alert Classes

| Class | Examples | Action |
| --- | --- | --- |
| `broker` | token failure, account header error, raw API response failure | live 신규 주문 중단 |
| `execution` | reject code, timeout, partial fill drift, cancel/replace review | 주문 속도 감속 또는 중단 |
| `reconciliation` | cash diff, position diff, missing target order detail | 신규 주문 중단 |
| `data` | stale quote, missing NAV, missing option chain, source heartbeat loss | 의존 엔진 신호 중단 |
| `risk` | drawdown, tail stress, concentration | 포지션 축소 검토 |
| `tax` | ROC unknown, withholding mismatch, tax lot provisional | 해당 ETF 신규진입 차단 |

## Daily Runbook

1. Toss OAuth2, accountSeq, broker endpoint 상태 확인
2. 전일 holdings, orders, executions, fees, tax, settlement replay
3. Toss snapshot과 내부 ledger reconciliation
4. external source health 확인
5. corporate action, ex-date, SEC filing, issuer notice 반영
6. buying power, sellable quantity, open order 예약금 점검
7. 신호 생성 후 risk hub 승인 여부 확인
8. paper 또는 live 주문 계획 생성
9. 장중 order detail/list polling과 rate limit 상태 기록
10. 장 종료 후 mark-to-market, cash ledger, tax lot, risk snapshot 저장

## Secret Handling SOP

API key, secret, access token은 코드, 문서, Git, 로그, 스크린샷에 저장하지 않습니다.

- Toss live key와 외부 vendor key를 분리합니다.
- `.env`는 Git 무시 대상이며 로컬 단일 사용자 개발에서만 임시 사용합니다.
- 운영 환경은 secret manager를 사용합니다.
- raw API response에는 authorization header, API key, account number 전체를 저장하지 않습니다.
- 계좌번호와 secret은 마스킹하고 hash만 남깁니다.

## Weekly Review

- 엔진별 P&L, drawdown, hit rate, slippage
- 신호와 체결 괴리
- 주문 상태 지연, reject, partial fill
- source stale 비율과 fallback 발생률
- NAV/ROC parser 수동 검토 결과
- 세후/비용후 기대값 변화
- calibration 변경 필요 여부

## Go/No-Go Criteria

Go 최소 조건:

- Toss OAuth2, accounts, holdings, orders, buying power 호출 안정
- raw API response 저장과 replay 검증
- 내부 ledger와 Toss holdings/orders 대사 자동화
- `clientOrderId` 멱등성과 내부 영구 재사용 금지 검증
- execution snapshot/delta와 cash ledger replay 검증
- rate-limit token bucket 검증
- 외부 피드 stale gate와 source health 검증
- paper에서 modeled vs actual/paper slippage 괴리 허용 범위

No-Go 조건:

- 중복 주문 가능성이 남아 있음
- Toss 계좌 상태와 내부 상태가 반복적으로 어긋남
- 외부 피드가 stale인데 신호가 살아 있음
- NAV/ROC 불확실한 ETF가 필터 없이 매수 후보로 올라옴
- reconciliation 실패를 수익으로 덮으려는 구조가 있음

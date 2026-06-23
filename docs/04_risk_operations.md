# 리스크와 운영 절차

## Portfolio Limits

아래 숫자는 시장 법칙이 아니라 starter guardrail입니다. 보고서 숫자를 그대로 자동매매에 넣지 않고, 실제 체결·슬리피지·손실분포가 쌓인 뒤 calibrated policy로 승격합니다.

| Limit | Starter Intent |
| --- | --- |
| Drawdown warning | 보고서 예시보다 더 낮게 시작 |
| Drawdown kill switch | micro-live 중에는 작은 손실에도 중단 |
| Single trade max loss | NAV 훼손보다 운영 검증을 우선 |
| Distribution ETF exposure | ROC/NAV 데이터가 검증되기 전 최소화 |
| Single live order notional | 초소형 주문으로 시작 |
| Cash buffer | 실제 `cashBuyingPower`와 내부 장부 차이를 보고 조정 |

## Kill Switch Conditions

다음 조건에서는 신규 주문을 중단하고 기존 포지션 축소 계획만 허용합니다.

- starter 또는 calibrated drawdown 한도 초과
- 주문 거부율이 starter 또는 calibrated 한도 초과
- 실체결 슬리피지가 모델 대비 2배 이상으로 3거래일 지속
- 데이터 지연 또는 누락으로 핵심 엔진 2개 이상이 stale
- 엔진별 P&L 상관이 위기 구간에서 0.8 이상으로 수렴
- Toss holdings와 내부 position ledger 불일치
- Toss OPEN 주문 목록과 내부 open order ledger 불일치
- 주문 응답 타임아웃 뒤 주문 상태 확인 전 새 `clientOrderId`로 재전송해야 하는 상황
- `cashBuyingPower` 조회 실패 또는 내부 available cash와 불일치

## Alert Classes

| Class | Examples | Action |
| --- | --- | --- |
| `data` | stale quote, missing NAV, missing option chain | 신규 신호 중단 |
| `execution` | reject code, partial fill drift, rate limit, timeout | 주문 속도 감속 또는 중단 |
| `reconciliation` | cash diff, position diff, missing fill | 신규 주문 중단 |
| `risk` | margin, drawdown, tail stress | 포지션 축소 검토 |
| `tax` | ROC unknown, withholding mismatch | 해당 ETF 신규진입 차단 |

## Daily Runbook

1. 장 시작 전 데이터 공급자 상태 확인
2. 전일 체결, 잔고, 내부장부 대사
3. corporate action, ex-date, earnings calendar 반영
4. buying power와 open order 예약금 점검
5. 신호 생성 후 risk hub 승인 여부 확인
6. `paper` 주문 계획과 예상 체결 기록
7. 장 종료 후 실제 시장 가격 기준 mark-to-market
8. 리스크 스냅샷과 알림 결과 저장

## API Key And Secret SOP

Toss `client_id`, `client_secret`, access token은 코드, 문서, Git, 로그에 저장하지 않습니다.

- 운영 키와 테스트 키를 분리합니다.
- 가능하면 조회용/거래용 권한 또는 환경을 분리합니다.
- 로컬 개발에서도 실제 키를 `.env` 파일에 저장하지 않고 OS secret store, Vault, KMS, 또는 배포 환경의 secret manager를 사용합니다.
- 로그에는 access token, API key, 서명 원문, 계좌번호 전체를 남기지 않습니다.
- 요청/응답 전문 저장이 필요하면 민감 필드를 마스킹하고 해시만 남깁니다.
- 키 회전, 폐기, 권한 축소 절차를 문서화합니다.

## Weekly Review

- 엔진별 P&L, drawdown, hit rate, slippage 검토
- 신호와 체결 괴리 분석
- 세후/비용후 기대 캐리 업데이트
- 네트워크 중복 노출 점검
- 신규 데이터 결측과 stale 비율 점검

## Go/No-Go Criteria For Live Trading

최소 조건:

- 충분한 페이퍼트레이드 로그 보유
- OAuth2 토큰 발급, accountSeq, holdings, orders, buying power 호출 검증
- 내부 장부와 Toss holdings/OPEN/CLOSED orders 대사 자동화
- `clientOrderId` 멱등성과 kill switch 검증
- 실체결 비용 추정치가 calibrated tolerance 이내
- 세무 보조장부 필드 검증

위 조건 중 하나라도 충족하지 못하면 `semi_auto` 또는 `paper` 모드를 유지합니다.

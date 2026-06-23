# 전략 엔진 명세

## Toss Live Scope

공식 Toss Open API 기준 live 자동화 대상은 국내/미국 주식·ETF 현물 주문입니다. 옵션, T-bill 직접 보유, 마진, 대차 기반 전략은 Toss 단독 live 범위 밖입니다.

## 1. ETF Relative Value Engine

목적은 동일하거나 매우 유사한 익스포저를 가진 ETF 간 일시적 괴리의 평균회귀를 거래하는 것입니다.

Toss live MVP에서는 다음 제약을 둡니다.

- 현물 long-only 또는 현금 보유 전환만 허용합니다.
- short leg, borrow rate가 필요한 pair는 제외합니다.
- 주문은 `cashBuyingPower` broker constraint와 sellable quantity를 통과해야 합니다.
- OPEN 주문이 남아 있으면 같은 symbol의 반대 주문을 제한합니다.

초기 규칙은 확정 숫자가 아니라 calibration 대상입니다.

- 대상: 동일 지수 ETF, 대체성 높은 섹터/스타일 ETF
- 제외: 장기 보유용 레버리지/인버스 ETP, borrow 필요 구조
- 진입: 사전에 정한 lookback에서 평균회귀성이 확인되고, 실제 체결 비용을 이긴다는 증거가 쌓인 경우
- 청산: 평균회귀, 시간 경과, 손실한도, 유동성 악화 중 먼저 오는 조건
- lookback, half-life, z-score, 보유기간은 보고서 숫자를 복사하지 않고 paper/live 관측 후 승인합니다.

## 2. Distribution Filter Engine

이 엔진은 매수 엔진이 아니라 함정 회피 필터입니다.

Toss 공식 API는 ETF NAV, premium/discount, ROC 구성, ex-date를 직접 제공하지 않습니다. 이 데이터는 issuer, SEC, 거래소, 외부 데이터 공급자로 보완합니다.

차단 조건:

- 시장가격이 NAV 또는 indicative value 대비 과도한 premium
- 최근 분배의 ROC 비중이 높음
- ex-date 직전 단순 배당 캡처 목적 매수
- headline distribution rate와 30-day SEC yield의 괴리가 과도함
- 기초자산 총수익률 대비 ETF 총수익률 괴리가 비정상적으로 확대

## 3. Option Carry Engine

목적은 내재변동성이 실현변동성보다 높을 때 정의된 위험의 옵션 프리미엄을 수확하는 것입니다.

공식 Toss Open API 기준으로 옵션체인, Greeks, 옵션 주문은 제공 범위가 아닙니다. 따라서 이 엔진은 Toss 단독 live 자동매매 대상이 아니라 연구용 또는 외부 브로커 연동 대상입니다.

Toss 저장소 안에서는 다음 산출물까지만 둡니다.

- 외부 옵션 데이터 스키마
- 백테스트/리서치 신호
- Toss 현물 포지션과의 공통 리스크 측정

## 4. T-Bill Collateral Engine

목적은 담보 안정성, 유동성, 현금성 수익을 동시에 관리하는 것입니다.

공식 Toss Open API의 보유주식 조회는 국내/미국 주식만 포함하고 해외 옵션·채권은 제외합니다. T-bill 직접 보유, 채권, 담보 인정, margin breakdown은 Toss Open API 자동화 범위 밖으로 둡니다.

Toss 저장소 안에서는 다음만 관리합니다.

- 현금성 대체 ETF 사용 여부의 리스크 검토
- 외부 T-bill ladder 장부
- 옵션/담보 모듈과 Toss 현물 계좌의 회계상 분리

## Unified Scoring

모든 엔진은 다음 개념의 조정 점수로 비교합니다.

```text
AdjustedScore =
  z(expected_carry)
  - risk_penalty
  - liquidity_cost_penalty
  - tax_drag_penalty
  - execution_penalty
  - network_overlap_penalty
```

Toss live 주문 후보는 추가로 다음 게이트를 통과해야 합니다.

- market calendar상 주문 가능 시간
- stock warning 없음 또는 허용 가능한 warning
- price limit/orderbook 기준 가격 유효성
- buying power 충분
- sellable quantity 충분
- OPEN 주문 충돌 없음
- rate limit 예산 충분

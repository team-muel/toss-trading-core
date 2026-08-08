# Strategy Engines

## Scope

Toss live 후보 범위는 전용 계좌의 미국 상장 USD long-only ETF 현물 주문입니다. 국내 종목과 개별주는 research-only입니다. 전략 엔진은 주문을 직접 내지 않고 `signal_log`와 target proposal만 생성합니다. 최종 주문은 account state, data quality, risk hub, rate limit gate를 통과해야 합니다.

## Engine Matrix

| Engine | Live Status | Required Data | Decision |
| --- | --- | --- | --- |
| Broad ETF Dual Momentum + Cash Overlay | live candidate after paper | Toss candles, market calendar, holdings, buying power; optional FRED cash hurdle | 1차 MVP 후보 |
| Homogeneous ETF Relative Value Long-Only | live candidate after paper | Toss/Massive prices, trades, spreads, commissions | short 없이 underperformer만 매수 |
| Distribution Filter | risk filter | issuer/SEC/Massive dividend data, NAV/ROC parser | 매수 엔진이 아니라 손실 회피 필터 |
| Opening Gap Fade | paper only first | orderbook, trades, intraday candles, strict latency logs | 체결 품질 검증 전 live 금지 |
| Option Carry | research only | Massive/Tradier options chain, IV, Greeks, OI | Toss 옵션 주문 미지원 |
| T-Bill Collateral / Rate Hurdle | accounting only | FRED, TreasuryDirect, internal cash ledger | 직접 T-bill 자동화 아님 |

## Broad ETF Dual Momentum + Cash Overlay

목적은 광역 ETF 바스켓에서 상대강도가 좋은 자산만 보유하고, 조건이 나빠지면 현금 또는 현금성 ETF로 낮추는 것입니다.

초기 제약:

- 저회전 월간 또는 주간 리밸런싱부터 시작
- 시장가 남발 금지
- `cashBuyingPower` constraint와 내부 available cash를 모두 통과
- drawdown 관리가 목적이지 고빈도 alpha 수확이 아님
- FRED 금리는 현금 대기 비용과 hurdle 보조로만 사용

### 사전등록 연구 Baseline v1

첫 구현은 `broad_etf_dual_momentum_v1` 하나로 제한합니다.

- 입력은 배당·분할을 포함한 `total_return` 일봉
- 252 거래일 lookback, 최근 21 거래일 skip
- 월말 종가까지의 이용 가능한 데이터로 신호 계산
- 다음 거래일부터 목표 비중 적용
- 절대 모멘텀 0 초과 후보 중 상대 모멘텀 상위 1개
- 통과 후보가 없으면 `SGOV`
- Foundation이 내보낸 현재 Toss 미국 수수료와 사전 선언된 주문금액별 slippage
  tier를 매도·매수 양쪽 체결금액에 적용
- SPY buy-and-hold, 동일가중, 60/40, 현금성 benchmark와 비교

전략 파라미터는 수익을 보장하는 calibrated policy가 아니라 하나의 falsifiable
baseline입니다. 비용 정책은 성과가 아니라 계좌 schedule과 주문금액으로 결정하며
OOS를 확인한 뒤 최적화해 소급 변경하지 않습니다.
실험 config, 입력 manifest와 code revision은 `gold/experiments`에 함께
기록합니다.

## Homogeneous ETF Relative Value Long-Only

이상적인 ETF relative value는 long/short지만 Toss live MVP에서는 short leg를 쓰지 않습니다.

허용 방식:

- 매우 유사한 ETF 대체군만 whitelist
- 괴리가 커진 underperformer만 매수
- outperformer는 현금 보유 또는 기존 보유 축소로 대응
- 예상 spread 회복이 총 비용의 최소 2배 이상일 때만 후보
- 구조 차이, 배당 처리, 운용보수, 유동성 차이를 별도 필터로 확인

제외:

- borrow rate가 필요한 pair
- inverse/leveraged ETP
- NAV/iNAV 신뢰도가 없으면 premium/discount arbitrage

## Distribution Filter

분배형 ETF 필터는 수익 엔진이 아니라 신규 매수 차단 엔진입니다.

차단 조건:

- NAV 또는 indicative value 대비 premium 과열
- ROC(Return of Capital) 비중 불명 또는 과도
- ex-date 직전 단순 배당 capture 목적 매수
- headline distribution rate와 30-day SEC yield의 큰 괴리
- 기초자산 총수익률 대비 ETF 총수익률 열화
- issuer 공지 또는 SEC filing이 stale

Toss API만으로 NAV, premium/discount, ROC, ex-date를 완전히 해결할 수 없으므로 `docs/12_external_data_feeds.md`의 issuer/SEC/Massive 보조 계층이 필요합니다.

## Opening Gap Fade

개장초 갭-되돌림은 후보로 남기되 live 초기 범위에서는 제외합니다.

필수 선행 조건:

- orderbook/trades timestamp 정합성 검증
- order create -> detail 반영 지연 측정
- partial fill, cancel, replace 거절 처리 검증
- 첫 5분 거래 금지 또는 별도 실험 계정
- 하루 손실 한도 도달 시 엔진 즉시 off

## Research-Only Engines

### Option Carry

Toss 공식 API는 옵션 주문, 옵션 chain, IV, Greeks를 제공하지 않습니다. 옵션 데이터는 Massive/Tradier 같은 외부 provider로 연구할 수 있지만, Toss live 주문과 연결하지 않습니다.

### T-Bill Collateral / Rate Hurdle

Toss 공식 API는 해외 채권 또는 직접 T-bill 보유/담보 자동화를 제공하지 않습니다. 이 엔진은 FRED/TreasuryDirect 기반 rate hurdle, 외부 T-bill ladder 보조장부, 현금성 ETF 비교까지만 담당합니다.

## Unified Gate

모든 엔진 후보는 같은 gate를 통과합니다.

- market calendar상 주문 가능 시간
- stock warning 또는 거래 제한 없음
- stale-data gate 통과
- source health 정상
- buying power와 sellable quantity 통과
- open order 충돌 없음
- rate-limit token bucket 정상
- reconciliation 차이 없음
- starter/calibrated guardrail 통과

## Research Alpha Authoring

위 엔진 매트릭스를 fast-expression alpha로 작성·채점하는 research-only 방법과 operator/metric
어휘는 `docs/18_alpha_expression_language.md`를 참고합니다. 그 계층은 신호만 만들고, 산출된
`Signal`은 여전히 이 문서의 Unified Gate와 `RiskHub`를 그대로 통과해야 합니다.

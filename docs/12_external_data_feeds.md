# External Data Feeds

## Purpose

외부 데이터 피드는 Toss를 대체하지 않습니다. Toss는 실행·계좌·보유·주문 상태의 기준원장이고, 외부 피드는 신호 계산, 이벤트 차단, 리스크 필터, NAV/ROC 보조에만 사용합니다.

## Minimum Stack

비용 민감형 1차 스택:

| Layer | Source | Role |
| --- | --- | --- |
| Execution/account truth | Toss Open API | 주문, 보유, buying power, sellable quantity, commissions |
| Historical ETF total return | Tiingo EOD | raw와 배당·분할 반영 adjusted OHLCV |
| Rates and release calendar | FRED/ALFRED | SOFR, Treasury yields, rate hurdle, point-in-time 관측값 |
| Official event gate | SEC EDGAR | 8-K, 10-Q, 10-K, fund filings, CIK mapping |
| ETF NAV/ROC | issuer parser | NAV, premium/discount, Section 19a, ROC 보조 |

성능 민감형 2차 확장:

| Layer | Source | Role |
| --- | --- | --- |
| Market/options/futures/corporate actions | Massive REST | options snapshot, dividends, splits, indices/futures 보조 |
| Realtime market/options/futures | Massive WebSocket | event-driven feature |
| Shortability/options backup | Tradier | ETB list, ORATS-linked options chain, market session |
| Faster event detection | SEC poller + issuer watch | filing/event gate 강화 |

Headline-news API는 1차 필수 구성요소가 아닙니다. 초기에는 공식 공시, issuer notice, corporate action만으로 risk gate를 만드는 편이 안전합니다.

## Provider Roles

### Toss Open API

Toss는 execution/account truth입니다. 외부 가격이나 corporate action이 들어와도 체결 후 포지션, 현금, 수수료, 세금, 결제예정일은 Toss snapshot과 내부 ledger로 다시 확정합니다.

### Tiingo

Tiingo EOD는 첫 전략 기준선에 필요한 미국 ETF 장기
`raw`/`total_return` 일봉의 주 공급자입니다. API 약관 수락과 토큰 등록은
사용자 승인 게이트이며, 승인 전에는 자동화가 provider를 건너뜁니다. Toss
`adjusted=true`는 배당 포함 계약이 확인되지 않았으므로 Tiingo
`total_return`을 대체하지 않습니다.

주요 사용:

- 장기 ETF total-return 일봉
- 배당·분할 반영 adjusted OHLCV
- Toss raw/split-adjusted 시계열 교차 검증
- 재현 가능한 baseline과 benchmark 입력

### Massive

Massive는 REST와 WebSocket을 통해 options, stocks, indices, futures, dividends, splits 같은 market data를 제공합니다. 초기에는 REST snapshot부터 붙이고, WebSocket은 source health와 slow-consumer 대응이 생긴 뒤 붙입니다.

주요 사용:

- options IV/Greeks/OI research
- dividends/splits calendar
- futures/indices risk proxy
- cross-source price sanity check

### FRED

FRED는 daily/batch 성격의 금리와 release calendar에 적합합니다.

주요 사용:

- SOFR
- Treasury yield series
- cash hurdle
- rate regime
- release calendar

백테스트에서는 revision bias를 줄이기 위해 ALFRED 또는 vintage-aware 정책을 검토합니다.

### SEC EDGAR

SEC EDGAR는 공식 이벤트 차단 계층입니다.

주요 사용:

- ticker-CIK mapping
- 8-K, 10-Q, 10-K, registration statement 감시
- fund filing 감시
- issuer parser 보조

SEC 요청은 명확한 User-Agent와 rate control을 둡니다. 실패 시 nightly archive를 fallback으로 사용합니다.

### Issuer Parser

ETF NAV, premium/discount, ROC tax character는 retail-friendly public API가 안정적으로 제공되지 않는 경우가 많습니다. issuer parser는 자동 확정 엔진이 아니라 audit queue입니다.

초기 원칙:

- whitelist ETF만 지원
- HTML/PDF/CSV parser 결과를 `raw_api_response`로 저장
- ex-date 근처 polling 강화
- ROC 불명은 신규 매수 차단
- 사람이 검토한 값과 parser 값을 분리

### Tradier

Tradier는 1차 필수 provider가 아닙니다. 옵션 체인, greeks, ETB list, market session 보조가 필요할 때 2차 provider로 검토합니다.

## Adapter Contract

모든 외부 adapter는 같은 계약을 지킵니다.

- `raw_api_response` 저장
- provider timestamp와 received timestamp 분리
- UTC normalization
- source timezone 기록
- symbol mapping을 instrument master에 의존
- heartbeat 또는 last success 기록
- stale 상태면 의존 feature 비활성화
- secret과 API key를 `raw_api_response`에 저장하지 않음

## Quality Gates

필수 gate:

- stale-data gate
- cross-source sanity check
- source heartbeat
- fallback hierarchy
- circuit breaker
- manual audit flag for NAV/ROC

예시:

- Tiingo EOD stale/누락 -> total-return baseline 실행 차단
- Massive WebSocket 실패 -> Massive REST snapshot -> 해당 feature 비활성화
- SEC EDGAR 지연 -> event gate 보수화 -> 신규 매수 차단
- issuer parser 실패 -> distribution filter 신규 매수 차단
- options quote crossed -> option research signal 비활성화

## Instrument Mapping

`instrument_master`는 외부 피드의 핵심입니다.

필수 매핑:

- Toss symbol
- ticker
- vendor symbol
- OCC option symbol
- CIK
- asset class
- currency
- timezone
- MIC
- effective date range

이 계층이 없으면 corporate action, ETF share class, option chain, SEC filing이 서로 다른 종목을 가리키는 오류가 생깁니다.

## Roadmap

1. `universe.csv`
2. `instrument_master`
3. Toss account/holdings/orders/buying_power adapter
4. `raw_api_response`
5. `source_health`
6. Tiingo EOD adapter와 라이선스 gate
7. ETF corporate action 정규화
8. FRED/ALFRED adapter
9. SEC ticker-CIK map
10. SEC submissions poller
11. feature table
12. ALLOW/REDUCE/BLOCK signal layer
13. Toss paper order planner
14. stale-data gate
15. shadow live 2 weeks
16. micro live
17. Massive REST/WebSocket
18. issuer NAV/ROC parser

## Sources

- Toss Open API: `https://developers.tossinvest.com/llms.txt`
- Tiingo API: `https://www.tiingo.com/documentation/end-of-day`
- Massive REST/WebSocket docs: `https://massive.com/docs`
- FRED API: `https://fred.stlouisfed.org/docs/api/fred/`
- SEC EDGAR APIs: `https://www.sec.gov/search-filings/edgar-application-programming-interfaces`
- Tradier options chains: `https://docs.tradier.com/reference/brokerage-api-markets-get-options-chains`
- TreasuryDirect auctions: `https://www.treasurydirect.gov/auctions/`

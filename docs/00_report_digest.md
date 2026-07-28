# Report Digest

이 문서는 지금까지 작성한 전략, Toss API, USD 현물 퀀트, 외부 데이터 피드 보고서의 결론만 모은 운영 기준입니다. 상세 endpoint 목록은 `docs/09_toss_official_api_coverage.md`, 외부 피드 설계는 `docs/12_external_data_feeds.md`를 기준으로 합니다.

## 결정된 원칙

1. Toss Open API는 실행·계좌·보유·주문 상태의 기준원장입니다.
2. 외부 데이터는 신호, 필터, 리스크 판단을 보강할 뿐 계좌 상태를 덮어쓰지 않습니다.
3. live MVP는 국내/미국 주식 및 ETF 현물 주문으로 제한합니다.
4. 옵션, 숏/대차, 직접 T-bill ladder, 마진 전략은 Toss 단독 live 범위가 아닙니다.
5. 전략 신호보다 계좌 상태 엔진, 장부, 체결 대사, rate limit 처리가 먼저입니다.
6. 보고서의 숫자는 초기 운용 가드레일이며 코드에 시장 법칙처럼 박아 넣지 않습니다.
7. `clientOrderId`는 모든 live 주문에 필수이고 내부적으로 영구 재사용하지 않습니다.
8. `cashBuyingPower`는 현금 잔고가 아니라 브로커가 반환한 주문 제약값입니다.
9. `CANCEL_REJECTED`, `REPLACE_REJECTED`는 terminal 상태가 아니라 원주문 재조회가 필요한 review 상태입니다.
10. `raw_api_response` 없이는 broker/vendor 장애를 재현할 수 없습니다.

## Live MVP 범위

허용 후보:

- 광역 ETF 듀얼모멘텀 + 현금 또는 현금성 ETF 오버레이
- 동질 ETF 대체군 평균회귀 long-only
- 분배형 ETF 신규진입 차단 필터
- 계좌 상태 기반 주문 가능 금액, 포지션, open order 관리

초기 제외:

- 해외 옵션 주문 및 옵션 캐리 live 자동화
- short leg 또는 borrow rate가 필요한 pair
- 직접 T-bill/채권 ladder 자동화
- NAV arbitrage처럼 실시간 NAV/iNAV 품질이 필요한 전략
- headline sentiment 기반 고빈도 이벤트 매매

## 외부 피드 최소 스택

비용과 복잡도를 줄이는 1차 조합:

- Toss Open API: 실행, 계좌, 주문, holdings, buying power
- Tiingo EOD: 미국 ETF 장기 raw/adjusted/total-return 일봉
- FRED/ALFRED: 금리, SOFR, Treasury yield, point-in-time 거시 시계열
- SEC EDGAR: 8-K, 10-Q, 10-K, fund filing, CIK 기반 event gate
- issuer parser: ETF NAV, premium/discount, ROC 공백 보완

성능 우선 2차 확장:

- Massive REST: 옵션/지수/선물/배당/분할 등 보조
- Massive WebSocket
- SEC 실시간 poller
- Tradier 옵션 체인/ETB 보조
- source health dashboard

## 구현 순서

1. Toss 계좌·주문 동기화
2. 내부 ledger와 `raw_api_response`
3. rate limit과 주문 상태 machine
4. 외부 피드 canonical schema
5. stale-data gate와 source health
6. paper replay
7. shadow-live
8. 초소형 live 또는 No-Go

## No-Go 원칙

다음 중 하나라도 발생하면 전략 성과와 무관하게 live 신규 주문을 막습니다.

- Toss holdings, orders, buying power와 내부 장부가 어긋남
- 주문 상태 불명 또는 duplicate create 위험
- 외부 피드 stale 상태에서 이전 신호로 주문을 계속 냄
- NAV/ROC 불확실성이 큰 ETF를 필터 없이 매수함
- rate limit degraded 상태에서 신규 주문이 조회·취소보다 우선됨

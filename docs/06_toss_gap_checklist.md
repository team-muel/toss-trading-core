# Toss API Checklist

공식 Toss Invest Open API 문서 확인 결과, 기본 자동매매 API는 존재합니다. 이 문서는 확인된 항목과 남은 검증 항목을 관리합니다.

## Account and Authentication

- [x] 공식 API 문서 존재
- [x] OAuth2 Client Credentials Grant
- [x] `POST /oauth2/token`
- [x] `Authorization: Bearer {access_token}`
- [x] 계좌 API용 `X-Tossinvest-Account` 헤더
- [x] rate limit 그룹과 응답 헤더
- [ ] sandbox 또는 paper endpoint 존재 여부
- [ ] 토큰 만료 시간 실계좌 확인
- [ ] 계좌별 주문 권한 확인

## Market Data

- [x] 현재가 `GET /api/v1/prices`
- [x] 호가 `GET /api/v1/orderbook`
- [x] 최근 체결 `GET /api/v1/trades`
- [x] 상/하한가 `GET /api/v1/price-limits`
- [x] 1분/일봉 캔들 `GET /api/v1/candles`
- [x] 종목 기본 정보 `GET /api/v1/stocks`
- [x] 매수 유의사항 `GET /api/v1/stocks/{symbol}/warnings`
- [x] 환율 `GET /api/v1/exchange-rate`
- [x] 국내/미국 장 운영 시간
- [ ] 옵션 chain
- [ ] 옵션 IV/Greeks
- [ ] ETF NAV 또는 premium/discount
- [ ] corporate action, ex-date

## Trading

- [x] 주문 생성 `POST /api/v1/orders`
- [x] 주문 취소 `POST /api/v1/orders/{orderId}/cancel`
- [x] 주문 정정 `POST /api/v1/orders/{orderId}/modify`
- [x] 주문 목록 조회 `GET /api/v1/orders`
- [x] 주문 상세 조회 `GET /api/v1/orders/{orderId}`
- [x] 주문 execution 합계 필드
- [x] `clientOrderId` 멱등성 지원
- [x] reject/error code 문서
- [x] 국내/미국 주식 주문
- [ ] 체결 webhook
- [ ] 미국 옵션 주문

## Account State

- [x] 계좌 목록 조회 `GET /api/v1/accounts`
- [x] 보유 주식 조회 `GET /api/v1/holdings`
- [x] 미체결/종료 주문 조회
- [x] buying power 조회 `GET /api/v1/buying-power`
- [x] sellable quantity 조회 `GET /api/v1/sellable-quantity`
- [x] commissions 조회 `GET /api/v1/commissions`
- [ ] 별도 현금 잔고 API
- [ ] settled/unsettled cash 직접 분리 API
- [ ] margin requirement breakdown 조회
- [ ] T-bill 또는 T-bill ETF 담보 인정 범위

## Safety Requirements

실주문 어댑터 구현 전 필수 조건:

- [ ] 내부장부와 브로커 holdings 대사
- [ ] OPEN 주문 목록과 내부 open order ledger 대사
- [ ] CLOSED 주문 페이징 누락 방지
- [ ] `clientOrderId`를 모든 주문에 강제
- [ ] 주문 응답 타임아웃 후 중복 주문 방지
- [ ] `cashBuyingPower` broker constraint와 내부 available cash 비교
- [ ] kill switch가 브로커 주문 경로보다 상위에 위치
- [ ] 429 retry와 선제 감속
- [ ] 부분체결 처리
- [ ] 장중 거래중단/거래제한 처리
- [ ] 장 마감 후 reconciliation 리포트

## Decision

| Result | Allowed Mode |
| --- | --- |
| OAuth2, accounts, holdings, orders, buying power 확인 | 현물 주식/ETF live adapter 설계 가능 |
| 옵션 데이터/옵션 주문 미지원 | 옵션 엔진 research only |
| 담보 인정 범위 미확인 | T-bill과 옵션마진 회계 분리 |
| webhook/stream 미지원 | REST polling 기반 운영 |
| cashflow/tax lot 미지원 | 자체 cash ledger와 세무 보조장부 필수 |

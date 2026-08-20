# Toss And Data Gap Checklist

이 문서는 구현 전 확인해야 할 gap만 관리합니다. 공식 endpoint 상세는 `docs/09_toss_official_api_coverage.md`, 외부 provider 상세는 `docs/12_external_data_feeds.md`를 봅니다.

## Toss Account And Authentication

- [x] 공식 API 문서 존재
- [x] OAuth2 Client Credentials Grant
- [x] `POST /oauth2/token`
- [x] `Authorization: Bearer {access_token}`
- [x] 계좌 API용 `X-Tossinvest-Account` 헤더
- [x] rate limit 그룹과 응답 헤더
- [x] GCP 고정 외부 IP의 Toss Open API 허용 IP 등록 확인 (2026-07-21)
- [ ] token expiry와 refresh timing 실계좌 확인
- [ ] 계좌별 주문 권한 확인
- [ ] sandbox 또는 paper endpoint 존재 여부 확인

## Toss Market And Trading

- [x] 현재가
- [x] 호가
- [x] 최근 체결
- [x] 상/하한가
- [x] 1분/일봉 candles
- [x] 종목 기본 정보
- [x] 매수 유의사항
- [x] 환율
- [x] 국내/미국 장 운영 시간
- [x] 주문 생성/정정/취소/조회
- [x] buying power
- [x] sellable quantity
- [x] commissions
- [x] execution 누적 필드
- [x] `clientOrderId` 멱등성

## Toss Missing Or Out Of Live Scope

- [ ] 체결 webhook
- [ ] broker websocket stream
- [ ] 해외 옵션 주문
- [ ] 옵션 chain, IV, Greeks
- [ ] borrow rate, short availability
- [ ] margin requirement breakdown
- [ ] 별도 현금 잔고 API
- [ ] settled/unsettled cash 직접 분리 API
- [ ] tax lot/cost basis API
- [ ] ETF NAV 또는 premium/discount
- [ ] ROC tax character
- [ ] corporate action cashflow event API
- [ ] 직접 T-bill/채권 보유 또는 담보 API

## External Data Readiness

- [ ] Massive REST key와 basic endpoint 확인
- [ ] Massive dividends/splits/options snapshot schema 확인
- [ ] FRED API key와 rate series loader 확인
- [ ] SEC EDGAR User-Agent, ticker-CIK map, submissions poller 확인
- [ ] issuer NAV/ROC parser 대상 ETF whitelist 확인
- [ ] raw API response 저장 확인
- [ ] source health와 stale gate 확인
- [ ] instrument master effective date 관리 확인
- [ ] timezone normalization 확인

## Safety Requirements Before Live Adapter

- [ ] 내부 장부와 Toss holdings 대사
- [x] `/oauth2/token`이 `access_denied` 또는 IP 제한 없이 성공
- [ ] OPEN 주문 목록과 내부 open order ledger 대사
- [x] CLOSED 목록은 OpenAPI 1.2.14에 따라 정기 runner에서 7일 중첩 window로 대사
- [x] OPEN 또는 검증된 CLOSED 복구 경로의 정확한 `orderId` 상세로 종료 상태 검증
- [x] 부분체결 snapshot/delta 증분 처리와 synthetic 회귀 테스트
- [ ] `clientOrderId`를 모든 주문에 강제
- [ ] 주문 timeout 후 중복 주문 방지
- [ ] `cashBuyingPower` constraint와 내부 available cash 비교
- [ ] kill switch가 주문 경로보다 상위에 위치
- [ ] 429 retry와 선제 감속
- [x] 실제 v0/v1 GCS 백업 raw API response replay와 profile 감사
- [ ] external stale source가 의존 엔진을 차단
- [ ] 장 마감 후 reconciliation report

## Decision Matrix

| Result | Allowed Mode |
| --- | --- |
| Toss auth/account/order/buying power 안정 | 현물 주식/ETF live adapter 설계 가능 |
| Toss 대사 실패 | paper 또는 semi-auto 유지 |
| external source stale gate 미완성 | 해당 외부 피드 의존 엔진 비활성화 |
| 옵션 데이터만 있고 옵션 주문 없음 | option engine research only |
| NAV/ROC parser 미검증 | distribution filter는 신규매수 차단 모드 |
| T-bill 직접 보유 미지원 | rate hurdle/accounting only |

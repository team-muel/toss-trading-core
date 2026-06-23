# 보고서 기반 설계 요약

## 핵심 결론

기존 보고서의 전략 결론은 유지하되, Toss API 전제는 공식 문서 확인 결과에 맞게 수정합니다.

전략 자체는 숨겨진 무위험 수익을 찾는 구조가 아니라, 여러 리스크 프리미엄을 분해하고 공통 꼬리위험을 제한하는 멀티엔진 시스템입니다. 다만 Toss Open API만으로 바로 자동화할 수 있는 범위는 국내/미국 주식·ETF 현물 주문입니다.

## 공식 Toss API 확인 결과

공식 문서 기준으로 제공되는 기능:

- OAuth2 Client Credentials Grant
- 계좌 목록 조회
- 국내/미국 주식 보유 현황 조회
- 국내/미국 주식 시세, 호가, 체결, 캔들, 상하한가
- 종목 기본 정보와 매수 유의사항
- 환율과 국내/미국 장 운영 시간
- 주문 생성, 정정, 취소
- 주문 목록, 주문 상세
- 매수 가능 금액, 매도 가능 수량, 수수료
- `clientOrderId` 기반 주문 멱등성
- 주문 execution의 체결수량, 평균체결가, 체결금액, 수수료, 세금, 최종체결시각, 결제예정일

공식 문서 기준으로 제공 범위 밖인 기능:

- 해외 옵션 주문
- 옵션체인, IV, Greeks
- 채권/T-bill 직접 보유 또는 담보 API
- margin requirement breakdown
- borrow rate, short availability
- webhook/websocket
- tax lot/cost basis API
- 배당/원천징수/corporate action cashflow 이벤트 API
- ETF NAV, premium/discount, ROC 분배 구성

## 자동매매 설계 원칙

1. Toss live MVP는 국내/미국 주식·ETF 현물 주문으로 제한합니다.
2. 모든 주문 생성에는 `clientOrderId`를 강제합니다.
3. 주문 상태는 REST 폴링으로 대사합니다.
4. `cashBuyingPower`, holdings, OPEN/CLOSED orders를 내부 장부와 매번 비교합니다.
5. 옵션 캐리와 T-bill 담보 엔진은 Toss 단독 live 범위 밖의 연구/외부 브로커 모듈로 둡니다.
6. ETF 상대가치와 분배형 ETF 필터는 외부 NAV/분배 데이터와 Toss 현물 주문을 결합해 제한적으로 구현합니다.
7. 실거래 전 단계는 데이터 적합성 -> 시뮬레이션 -> 페이퍼트레이드 -> 초소형 현물 실거래 순서로 고정합니다.

## 주요 위험

- webhook이 없으므로 체결 이벤트는 폴링 지연을 감안해야 합니다.
- cashflow/tax lot API가 없으므로 세후 장부는 자체적으로 유지해야 합니다.
- 옵션/마진/대차 전략은 Toss 공식 API 범위 밖입니다.
- 분배형 ETF의 높은 분배율은 경제적 수익이 아니라 ROC 또는 옵션 프리미엄 반환일 수 있습니다.
- 레버리지/인버스 ETP는 일일 리셋과 경로의존성 때문에 장기 헤지 수단으로 부적합할 수 있습니다.

## 우선 구현 범위

- OAuth2 토큰 발급 모듈
- accountSeq 조회와 계좌 헤더 처리
- holdings, buying power, orders 기반 계좌 상태 엔진
- 주문 계획 로그
- REST 주문 상태 폴링
- 주문 execution 기반 체결/수수료/세금/결제예정일 장부
- broker reconciliation 로그
- rate limit과 error code 처리
- kill switch

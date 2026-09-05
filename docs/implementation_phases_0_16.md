# 구현 단계 0~16 전체 감사

감사 기준일: 2026-09-05  
운영 설정: `READ_ONLY`, `live_trading_enabled: false`

## 판정 방법

각 단계의 코드, 스키마, 정책 문서, 정상·실패 경로 테스트를 대조했다. `완료`는 현재 단계 브랜치에서 계약이 구현되고 fail-closed 동작이 검증됐다는 뜻이다. 열린 PR이 순서대로 기본 브랜치에 병합되기 전에는 릴리스 완료가 아니며, 외부 공급자 데이터의 범위와 최신성은 운영 중 계속 감시해야 한다.

## 의존 순서와 판정

| 단계 | 구현 범위 | 판정 | 핵심 근거 |
|---:|---|---|---|
| 0 | 거버넌스, 정책 registry, 안전 기본값 | 완료 | policy hash/status, 의존 검사, 실거래 false |
| 1 | Decimal·UTC·설정·migration 기반 | 완료 | 공통 도메인 타입, replay clock, 원자적 migration |
| 2 | Toss read-only 원본·계좌 truth | 완료 | GET/OAuth 한정, secret 제거, raw replay |
| 3 | 주문 상태·체결 delta 원장 | 완료 | append-only 전이, 멱등성, unknown 차단 |
| 4 | 현금·포지션·결제·tax lot | 완료 | 증거 기반 posting, reservation, settlement lineage |
| 5 | 계좌 대사와 거래 gate | 완료 | 불일치·미확인 입력 차단, DB gate |
| 6 | point-in-time 시간 truth | 완료 | availability/cutoff/vintage/revision 분리 |
| 7 | instrument·Universe·calendar·action | 완료 | 역사적 reference와 symbol mapping |
| 8 | 불변 bronze/silver/gold/catalog | 완료 | raw-first, SHA-256, manifest, lineage, no-overwrite |
| 9 | 시장·거시·기업 데이터 수집 계약 | 완료 | 발표/수신/사용시각, source revision, 수집 순서 |
| 10 | 데이터 품질과 source health | 완료 | stale/conflict/missing 전파 및 NO_TRADE |
| 11 | Feature Store | 완료 | 결정론 snapshot, 누수 방지, 전체 lineage |
| 12 | Market/Company/Portfolio/System State | 완료 | 상태 분리, component 재계산, 위험 축소 |
| 13 | 자산가격결정 Engine | 완료 | CAPM·다요인·BL·Reverse DCF, 주문 비생성 |
| 14 | 기대수익률과 Alpha | 완료 | 자산군별 component, gross/net, 구간과 ABSTAIN |
| 15 | 위험모형 | 완료 | 공분산·tail·stress·exposure·risk contribution |
| 16 | 포트폴리오 구성 | 완료 | 6계층 target, 제약, 비용, no-trade, fail-safe |

런타임 순서는 `investment policy -> account truth -> time truth -> data truth -> financial calculation -> target portfolio -> risk control -> order`로 보존한다.

## 단계별 완료조건 점검

단계 0~8의 상세 근거와 예외는 [implementation_phases_0_8.md](implementation_phases_0_8.md)에 기록돼 있다. 단계 9는 raw-first adapter가 가격, 캘린더, 기업행동, FX, 금리, 거시, 컨센서스, 공시, 재무제표와 analyst estimate의 필수 필드를 검사한다. API 오류는 성공본을 대체하지 않고 계좌 DB를 수정하지 않는다. 상세 계약은 [data_collection_phase9.md](data_collection_phase9.md)다.

단계 10은 stale source와 심각한 품질 오류를 차단하고, conflict를 평균하지 않으며, missing을 0으로 바꾸지 않는다. 품질은 feature, state, decision confidence와 risk action까지 전파된다. 단계 11은 동일 입력·파라미터·시점·코드에서 동일 snapshot을 만들고 미래 정보, 현재 Universe의 소급 적용, 부족한 history를 차단한다. Feature는 BUY/SELL을 반환하지 않는다. 단계 12는 네 상태를 분리하고 모든 component와 불확실성을 보존하며, 낮은 신뢰는 위험을 줄이고 blocking 상태는 신규 거래를 막는다.

단계 13은 21·63·126·252 거래일 변환, point-in-time 무위험금리, 수축 베타 CAPM, 일곱 factor premium, Black-Litterman 전제조건과 Reverse DCF 역산을 구현한다. factor 중복 반영과 장·단기 신호 직접 합산을 차단한다. 결과에는 상하한과 추정오차가 있고 주문 필드는 없다. 자세한 내용은 [asset_pricing_phase13.md](asset_pricing_phase13.md)다.

단계 14는 개별주, 주식 ETF, 채권 ETF, 현금성 자산과 원자재 ETF에 서로 다른 기대수익 component 계약을 적용한다. component 합, gross/net 비용 분해, confidence shrinkage, expected-minus-required Alpha와 불확실성 구간을 보존한다. 불확실한 판단은 명시적 `ABSTAIN`으로 끝난다. 자세한 내용은 [expected_returns_phase14.md](expected_returns_phase14.md)다.

단계 15는 total-return panel에서 표본·EWMA·shrinkage·factor·stress covariance를 만들고 PSD를 검사한다. 포트폴리오 변동성, Euler 위험기여도, historical VaR/CVaR, exposure, stress, event risk와 drawdown 진단을 분리해 보존한다. 역행렬 fallback과 위험모형 실패는 optimizer를 차단한다. 자세한 내용은 [risk_models_phase15.md](risk_models_phase15.md)다.

단계 16은 strategic부터 trade optimization까지 여섯 target 변환을 분리한다. 목적함수의 위험·비용·turnover 중복을 검사하고 모든 정책 제약, no-trade band, 경제성 gate와 solver fail-safe를 적용한다. 결과는 주문이 아니라 raw·risk-constrained·executable 목표 비중과 목표수량이다. 자세한 내용은 [portfolio_construction_phase16.md](portfolio_construction_phase16.md)다.

## 운영 상태

- Toss OAuth는 등록된 GCP 고정 IP의 VM에서 검증됐고, 자격증명은 Secret Manager에서 주입한다. 비밀값은 저장소와 수집 원본에 기록하지 않는다.
- Tiingo와 FRED secret도 VM 서비스 계정에서 접근 가능하나, 공급자 이용범위와 실데이터 최신성은 manifest와 source health로 매 실행 기록해야 한다.
- 단계 7 fixture calendar는 운영 calendar가 아니다. 단계 9 이후 모듈은 계약과 처리 경로를 제공하며 24시간 scheduler 자체는 아직 이 단계들의 완료조건이 아니다.
- 계좌 cash source가 직접 대사 불가능하면 `UNVERIFIABLE`, 데이터가 stale/missing/conflict이면 `NO_TRADE`다.
- 실주문 승인은 없으며 write path와 `live_trading_enabled`는 계속 차단 상태다.

## 검증 기록

- `python -m pytest -q`: **313 passed**
- `python scripts/check_governance.py`: 통과
- `python scripts/check_toss_openapi.py`: 통과
- 모든 JSON schema parsing과 Python compile: 통과
- wheel build와 포함 파일 검사: 통과
- `git diff --check`: 통과

## 변경 전달 상태

단계별 변경은 의존 순서를 보존하는 stacked PR로 열려 있다.

| 단계 | PR | Base | 상태 |
|---:|---:|---|---|
| 7 | #9 | 단계 4 settlement evidence | CLOSED, 미병합 |
| 8 | #10 | 단계 7 | CLOSED, 미병합 |
| 9 | #11 | 단계 8 | CLOSED, 미병합 |
| 10 | #15 | 단계 9 | OPEN, mergeable, CI 통과 |
| 11 | #16 | 단계 10 | OPEN, mergeable, CI 통과 |
| 12 | #17 | 단계 11 | OPEN, mergeable, CI 통과 |
| 13 | #18 | 단계 12 | OPEN, mergeable, CI 통과 |
| 14 | #19 | 단계 13 | OPEN, mergeable, CI 통과 |
| 15 | #20 | 단계 14 | OPEN, mergeable, CI 통과 |
| 16 | #21 | 단계 15 | OPEN, mergeable, CI 통과 |

문서 최신화는 단계 16 위의 별도 문서 PR로 관리한다. 단계 7~9 PR은 미병합 상태로 닫혔으므로 병합 전에 다시 열거나 대체 PR을 만들어야 한다. 모든 변경은 낮은 단계부터 순서대로 반영해야 한다. 현재 구현 브랜치에서 완료조건은 충족하지만 기본 브랜치 릴리스 완료 상태는 아니다.

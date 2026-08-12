# 2,000~3,000종목 광범위 주식 추천 연구

## 목적과 분리 원칙

기존 15개 ETF baseline은 전향 검증 규칙과 시작일을 변경하지 않는다. 광범위 주식
추천은 별도 파이프라인으로 운영하며, 미국 상장 보통주 중 유동성 상위 약 2,500개를
매일 동일한 규칙으로 평가한다.

여기서 “추천”은 조사 우선순위를 뜻한다. 개인 맞춤 투자자문이나 주문 승인이 아니며,
추천 결과에서 Toss 주문 경로로 이어지는 연결은 만들지 않는다.

## 공급자 선택

현재 Tiingo Starter는 월간 고유 symbol 한도가 500개여서 2,000~3,000개 상시
스크리닝에 맞지 않는다. 별도 유료 상향을 하지 않는 초기 경로는 Massive의 미국 주식
reference와 일자별 grouped daily bar를 사용한다. API key, 개인 이용약관 승인,
Secret Manager version이 모두 준비되기 전 `config/stock_recommendation_policy.json`의
`enabled`는 `false`다.

## 고정 스크리닝 규칙

- 미국 상장 active common stock
- 최신 가격 5 USD 이상
- 최근 21거래일 median dollar volume 5백만 USD 이상
- 최소 253거래일 이력
- 유동성 순으로 최대 2,500개를 고정 평가 우주로 선택
- 12-1개월 momentum 35%
- 6-1개월 momentum 25%
- 126일 저변동성 20%
- 200일 이동평균 대비 trend strength 20%
- 12-1 momentum이 양수이고 200일 이동평균 위에 있는 종목만 최종 후보
- 상위 10개까지만 연구 추천

점수는 그날 단면의 percentile rank로 계산해 극단값의 영향을 제한한다. LLM은 종목,
점수 또는 순위를 변경하지 않고 설명만 할 수 있다.

## 검증과 전달

각 실행은 전체 평가 수, 유동성·이력 통과 수, 추세 통과 수, 최종 추천과 factor별
근거를 content-addressed JSON으로 남긴다. 각 추천은 SPY 대비 5·21·63거래일 성과를
전향 추적하며, 추천 당시에는 미래 성과를 표시하지 않는다.

매일 데이터가 실제로 전진한 경우에만 새 추천을 만들고 Gmail 연구 digest에는 다음을
포함한다.

- 기준 거래일과 실제 평가 종목 수
- 추천 종목과 factor별 점수
- 유동성·가격·추세 필터 통과 사실
- 이전 추천의 만기가 도래한 전향 성과
- `execution_authorized=false`

## 활성화 전 남은 작업

1. Massive 개인 이용약관 승인과 API key Secret Manager 등록
2. 최근 253거래일 grouped daily bootstrap
3. active common-stock reference와 ticker 변경·상장폐지 이력 보존
4. raw 응답과 normalized bar manifest 연결
5. 2,000개 이상 universe QA 통과
6. 첫 추천 run을 만들되 live·paper 주문에는 연결하지 않음

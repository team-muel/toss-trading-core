# 2,000~3,000종목 광범위 주식 추천 연구

## 목적과 분리 원칙

기존 15개 ETF baseline은 전향 검증 규칙과 시작일을 변경하지 않는다. 광범위 주식
추천 연구는 별도 파이프라인으로 운영하며, 미국 상장 보통주 중 유동성 상위 약
2,500개를 매일 동일한 규칙으로 평가한다.

정량 상위 종목은 `screening_candidates`이며 아직 매수 추천이 아니다. 실제
`recommendations`에는 `focused-research-dossier-v5` 검증을 통과하고 결론이 `buy`인
종목만 들어간다. 둘 다 개인 맞춤 투자자문이나 주문 승인이 아니며, 결과에서 Toss
주문 경로로 이어지는 연결은 만들지 않는다.

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
- 상위 25개까지만 집중연구 큐에 등록
- Variant Perception 집중연구를 통과한 종목 중 상위 10개까지만 연구상 매수 추천

정량 점수는 집중연구 대상을 고르는 screening 우선순위에만 사용한다. 실제 추천은
Investment Thesis → Variant View → Earnings Model → Earnings Quality → Valuation → Catalyst Path →
Risk/Disconfirming Evidence → Position Construction을 통과해야 한다. conviction 점수는
이 판단이나 포지션 크기에 쓰지 않고 마지막 요약에만 둔다. LLM은 종목, 정량 점수 또는
순위를 변경하지 않고 설명만 할 수 있다.

## 검증과 전달

각 실행은 전체 평가 수, 유동성·이력 통과 수, 추세 통과 수, 정량 screening 후보,
집중연구 대기 큐, 최종 매수 추천과 factor별 근거를 content-addressed JSON으로
남긴다. 집중연구를 통과하지 않은 정량 후보는 매수 추천으로 승격하지 않는다. 실제
매수 추천만 SPY 대비 5·21·63거래일 성과를 전향 추적하며, 추천 당시에는 미래 성과를
표시하지 않는다.

`stock-recommendation-run-v6`의 실제 추천은 dossier의 전체 분석 섹션과
driver-based earnings model을 그대로 전달한다. segment driver, 손익·현금흐름 bridge,
유지·성장 CAPEX, 투하자본 bridge, 증분 매출·영업이익·NOPAT, hurdle 대비 증분 ROIC,
CAPEX 생산성과 shock sensitivity뿐 아니라 Earnings Quality의 balance-sheet
growth, accruals, cash conversion, 조정항목, GAAP/non-GAAP bridge, EPS 성장 attribution을
축약하지 않으며 `score_summary`를 마지막에 둔다. 반면 아직 집중연구가 없는 큐 항목은
정량 `screening_score`를 연구 우선순위로만 보존한다.

기존 `focused-research-dossier-v2`는 새 기준에서 매수 추천 근거로 재사용하지 않는다.
발견 시 실행 전체를 실패시키지 않고 `focused_research_driver_model_required`로 되돌려
v5 dossier 재작성 큐에 넣는다. 손익·현금흐름 bridge가 없는 과거 dossier를 자동
변환하거나 결과 숫자를 역으로 가정해 채우지 않는다.

기존 `focused-research-dossier-v3`도 Earnings Quality가 없으므로 매수 추천 근거로
자동 승격하지 않는다. 실행 전체를 실패시키지 않고
`focused_research_earnings_quality_required` 상태로 돌려보내며, 매출채권·재고·조정항목
등의 숫자를 기존 EPS에서 역산해 채우지 않는다.

기존 `focused-research-dossier-v4`는 Earnings Quality가 있더라도 투하자본 증감을
CAPEX·D&A·운전자본·인수에 연결하는 증분 경제성 bridge가 없다. 따라서
`focused_research_incremental_economics_required`로 되돌리고, 기말 투하자본 차이를
원인별 근거 없이 자동 분해하거나 hurdle rate를 임의 가정하지 않는다.

매일 데이터가 실제로 전진한 경우에만 새 추천을 만들고 Gmail 연구 digest에는 다음을
포함한다.

- 기준 거래일과 실제 평가 종목 수
- screening 후보와 factor별 점수
- 집중연구 dossier ID, 시장 내재 기대와 자체 추정의 격차, 연결된 촉매
- 집중연구 미완료로 매수 추천이 보류된 종목 수
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

집중연구의 필수 데이터 계약과 검증 명령은
[`docs/34_variant_perception_focused_research.md`](34_variant_perception_focused_research.md)를
따른다.

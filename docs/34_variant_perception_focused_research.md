# Variant Perception 중심 기관급 집중연구

## 기준

집중연구의 첫 질문은 “좋은 회사인가?”가 아니다.

> 시장이 현재 무엇을 기대하며, 우리는 어느 지점에서 그 기대가 틀렸다고 보는가?

좋은 산업, AI 수혜, 높은 성장 같은 공개 서술은 연구 출발점일 뿐 초과수익 가설이
아니다. 모든 종목 집중연구는 아래 연결을 수치와 출처로 완성해야 한다.

```text
시점 고정 시장 컨센서스
  → 현재 가격에 내재된 기대
  → 자체 추정
  → 방향성과 크기가 계산된 격차
  → 격차를 해소하거나 반증할 관측 가능한 촉매
```

하나라도 빠지면 `screening_candidate` 상태에 머물며 `buy` 연구 추천이 될 수 없다.

## 의사결정 메모의 고정 순서

모든 `focused-research-dossier-v9`는 다음 순서를 그대로 사용한다.

```text
Investment Thesis
  → Variant View
  → Earnings Model
  → Earnings Quality
  → Supply-chain Read-through
  → Earnings Call Diff / Management Calibration
  → Positioning Analysis
  → Valuation
  → Catalyst Path
  → Risk / Disconfirming Evidence
  → Position Construction
  → Score Summary
```

점수는 분석 입력이 아니다. `Investment Conviction`은 마지막 압축 요약일 뿐 추천
게이트와 포지션 크기 계산에서 읽지 않는다. 검증된 JSON과 함께 같은 순서의 Markdown
메모를 생성하므로 JSON viewer의 키 정렬 방식에도 의존하지 않는다.

핵심 숫자는 점수보다 먼저 다음처럼 직접 드러나야 한다.

- 목표 연도의 시장가격 내재 EPS와 point-in-time 컨센서스 EPS
- FY1·FY2 EPS, revenue, EBITDA/FCF revision과 가격의 같은 기간 변화
- 목표가 분포, 실적 발표 전후 revision, 상향·하향·유지 analyst breadth
- bear/base/bull의 driver별 매출, gross margin, operating expense, tax, EPS,
  OCF, 유지·성장 CAPEX, FCF, 증분 ROIC와 확률
- 비교기간과 투자-성과 시차, 투하자본 bridge, 증분 매출·영업이익·NOPAT,
  hurdle 대비 증분 ROIC·경제적 이익과 CAPEX 생산성
- 특정 경제 driver 충격이 매출·EPS·FCF·증분 ROIC에 미치는 재계산 민감도
- 매출 대비 매출채권·재고·계약부채·이연매출 증가율과 현금전환·accrual 분석
- GAAP/non-GAAP reconciliation과 EPS 성장의 영업·세율·조정·자사주 기여도
- 고객·공급업체·경쟁사의 관계와 시차, 독립 1차 출처별 지지·반대 신호
- 연속 두 earnings call의 새 표현·사라진 표현, horizon·confidence와 질문·회피 변화
- 수치 guidance 범위 변화와 같은 지표의 직전 8개 분기 guidance 대 실제 결과
- 과거 경영진 약속의 `met`, `missed`, `changed`, `pending` 상태와 이행률
- 13F·13D/G·Form 4, 주요 주주 집중도, passive ownership과 ETF exposure
- short interest·days-to-cover·short-sale volume·borrow availability와 비용
- IV term structure·percentile·realized 대비 implied·25Δ skew·OI·expected move·IV crush
- Base EPS의 컨센서스·시장 내재 값 대비 차이
- bear/base/bull 목표가격과 확률가중 기대수익
- 날짜가 있는 촉매, 관측 변수, thesis 해소 방식
- 사전 반증 조건과 현재 존재하는 contrary evidence
- 초기·목표·최대 비중, 실적 이벤트 상한, risk budget, 증액·축소·청산 조건

## 두 단계 연구 구조

| 단계 | 목적 | 가능한 결론 |
|---|---|---|
| 2,000~3,000종목 정량 스크리닝 | 유동성·이력·momentum·변동성·추세로 연구 우선순위 생성 | `screening_candidate` |
| 집중연구 dossier | 컨센서스와 가격 내재 기대를 역산하고 자체 추정과 촉매를 연결 | `buy`, `watch`, `avoid`, `no_view` |

정량 순위가 아무리 높아도 집중연구를 대신하지 않는다. 반대로 집중연구 dossier가
있어도 해당 날짜의 정량 평가 우주와 필터를 통과하지 않으면 그 실행의 매수 추천에
포함되지 않는다.

## 필수 데이터 계약

`config/focused_research_policy.json`과
`src/toss_trading/research/variant_perception.py`가 권위 있는 계약이다.

각 dossier에는 최소 세 개의 expectation chain이 필요하며 다음 범주를 모두 덮어야
한다.

1. 산업 또는 매출 driver
2. margin 또는 cash flow
3. 자본효율 또는 valuation

각 chain의 필수 필드는 다음과 같다.

- 동일 metric과 horizon의 point-in-time 시장 컨센서스
- 현재 주가를 1% 이내 오차로 재현하는 reverse DCF, reverse multiple,
  reverse unit economics 또는 segment SOTP의 가격 내재 값
- 최소 두 개의 명시적 역산 가정과 각 가정의 출처
- 공시·산업자료를 사용한 자체 추정과 방법론
- `house - implied`, `house - consensus`, 유리한 방향을 반영한 gap
- 시장이 틀릴 수 있는 구체적 이유
- 사전에 정한 반증 조건
- 날짜 범위와 관찰 변수가 있는 catalyst 연결

AMAT를 연구한다면 종목 고유 수치가 확보된 뒤 예를 들어 WFE 성장, gross margin,
advanced packaging 기여, CAPEX가 만드는 증분 ROIC 또는 FCF를 chain으로 선택할 수
있다. 단, 이 문장은 metric 선택 예시일 뿐 수치나 결론을 미리 가정하지 않는다.

## Estimate Revision & Price Divergence

현재 컨센서스 값만으로는 시장 기대가 어느 방향으로 움직이는지 알 수 없다. 따라서
`variant_view.estimate_revision`은 공통 snapshot 날짜로 다음 항목을 비교한다.

- FY1 EPS와 FY2 EPS
- revenue
- EBITDA 또는 free cash flow 중 최소 하나; 데이터가 있으면 둘 다
- 목표가의 minimum, 25분위, median, mean, 75분위, maximum과 analyst 수
- 최근 실적 발표 직전과 직후의 FY1 EPS·revenue
- 같은 기간 FY1 EPS를 상향·하향·유지한 analyst 수와 순 breadth
- 같은 시작일·종료일의 실제 주가

기본 비교기간은 정책상 20~45일이다. 모든 estimate metric은 같은 과거·현재 날짜를
사용하고 각 consensus source의 실제 관측일이 snapshot 날짜와 일치해야 한다. 목표가
분포와 가격도 같은 기간이어야 하며 현재 가격은 dossier 가격과 다시 일치해야 한다.
EPS가 0이거나 부호를 넘는 경우 percentage revision을 억지로 만들지 않고 절대 변화와
방향을 보존한다.

FY1 EPS revision과 가격 변화가 각각 1% 이상일 때 다음처럼 분류한다.

| Estimate | Price | 상태 | 해석 |
|---|---|---|---|
| 상승 | 하락 | `positive_revision_price_decline` | 펀더멘털 상향을 가격이 따라가지 않은 연구 후보 |
| 하락 | 상승 | `negative_revision_price_rally` | 시장이 미래 반전이나 다른 driver를 선반영했는지 검증할 후보 |
| 상승 | 상승 | `positive_revision_price_confirmation` | revision과 가격이 같은 방향으로 확인 |
| 하락 | 하락 | `negative_revision_price_confirmation` | revision과 가격이 같은 방향으로 악화 |

두 divergence 상태는 research-priority signal일 뿐 자동 매수·매도 신호가 아니다. 이후
가격 내재 기대, 자체 driver model, catalyst와 반증 조건을 통해 해석해야 하며 추천
게이트나 Position Construction의 비중 입력으로 직접 사용하지 않는다.

## Driver-based Earnings Model

`earnings_model`은 매출과 EPS 결과를 사람이 입력하는 요약표가 아니다. bear, base,
bull 모두 동일한 `driver_id` 집합을 사용하고 검증기가 다음 식으로 결과를 계산한다.

```text
각 segment revenue
  = economic driver value
  × company share
  × revenue conversion factor
  × timing conversion

total revenue
  = segment revenue의 합

gross profit
  = segment revenue × segment gross margin의 합

operating income
  = gross profit
  - fixed operating expense
  - revenue × variable operating expense rate

EPS
  = (operating income - net interest expense
     + other non-operating income - tax)
  / diluted shares
```

`revenue_conversion_factor`는 driver 단위를 회사 매출로 변환하는 명시적 계수다. AMAT의
시장 CAPEX 모델에서는 장비 intensity 등을 이 계수에 담고 방법론과 출처를 연결한다.
따라서 AMAT 예시는 다음과 같은 형태가 되며, Foundry/Logic, DRAM, NAND,
Packaging/Other를 합쳐 Semiconductor Systems 매출을 만든다.

```text
시장 CAPEX × AMAT 점유율 × 장비 intensity × 출하/인식 timing
  → segment revenue
  → segment gross profit
  → operating expense
  → operating income
  → tax / diluted shares
  → EPS
```

현금흐름은 별도 bridge로 계산한다.

```text
net income
  + D&A
  + stock-based compensation
  - change in net working capital
  + other operating cash adjustments
  = OCF

OCF - maintenance CAPEX - growth CAPEX = FCF

incremental ROIC
  = (operating income - prior-period operating income) × (1 - tax rate)
  / (ending invested capital - prior-period invested capital)
```

### 증분 경제성

기말 투하자본 차이를 직접 입력하고 끝내지 않는다. 다음 bridge가 정확히 일치해야 한다.

```text
기초 투하자본
  + 유지 CAPEX
  + 성장 CAPEX
  - D&A
  + 순운전자본 투자
  + 인수 투자
  + 기타 투하자본 변화
  = 기말 투하자본

증분 매출        = 당기 매출 - 전기 매출
증분 영업이익    = 당기 영업이익 - 전기 영업이익
증분 NOPAT       = 증분 영업이익 × (1 - 정상화 세율)
증분 ROIC        = 증분 NOPAT / 증분 투하자본
가치창출 spread  = 증분 ROIC - hurdle rate
증분 경제적 이익 = 증분 NOPAT - 증분 투하자본 × hurdle rate
```

유지 CAPEX와 성장 CAPEX는 합쳐서 입력할 수 없고 각각 근거와 함께 저장한다. 증분
투하자본이 양수가 아니거나 bridge가 맞지 않으면 증분 경제성 모델로 인정하지 않는다.
각 scenario에는 전기 비교기간, 측정 개월 수, 투자 후 성과가 나타나는 시차를 명시한다.
성장 CAPEX 대비 증분 매출·영업이익·NOPAT 배수, 회수기간, 증분 영업이익률,
증분 capital turnover와 재투자율도 계산한다.

이 계산은 먼저 “같은 기간 자본 증가와 이익 증가가 어떠했는가”를 보여준다. 특정
데이터센터나 공장이 얼마의 매출·이익을 만들었다는 인과 주장은 회사 공시나 별도의
vintage 방법론이 뒷받침할 때만 허용한다. 단순 동행을 인과로 표현하지 않는다.

### Driver shock sensitivity

최소 하나의 sensitivity case가 필수다. 각 case는 base scenario의 특정 driver를
명시적으로 충격한 뒤 다른 share, conversion, timing, 비용, 세율, 주식 수 가정은
고정하여 전체 모델을 다시 계산한다. 결과에는 다음이 포함된다.

- 매출 변화율
- gross margin과 operating margin 변화(bps)
- operating income과 EPS 변화율
- OCF와 FCF 변화율
- 증분 ROIC 변화(bps)
- 충격 전후 EPS와 FCF

예를 들어 `AI-related CAPEX +10%`는 AI 노출이 있는 Foundry/Logic, DRAM,
Packaging driver만 10% 올려 “AI가 성장한다”가 아니라 “현재 share·mix·timing과 비용
구조에서 EPS가 몇 % 변하는가”를 답한다. 이는 다른 조건을 고정한 부분균형 민감도이며
그 자체가 발생 확률이나 최종 목표가격은 아니다.

입력자가 `revenue_usd_millions`, `eps_usd`, `free_cash_flow_usd_millions` 같은 결과를
직접 덮어쓸 수 없다. 생성 시 계산하고, 저장된 dossier를 추천 파이프라인이 다시 읽을
때도 driver 식으로 독립 재계산한다. segment revenue, 손익계산서, 현금흐름, 민감도 중
하나라도 재현되지 않으면 hash 상태와 무관하게 검증이 실패한다.

## Earnings Quality

`earnings_quality`는 `earnings_model`과 별도의 필수 섹션이다. 전망 EPS가 높다는 사실과
그 EPS가 현금으로 전환되고 반복 가능한 원천에서 발생한다는 사실을 구분한다. 전기와
당기의 공시 원자료를 사용하며 다음 항목을 하나도 생략할 수 없다.

- 매출채권 증가율과 매출 증가율의 차이
- 재고 증가율과 매출 증가율의 차이
- contract liabilities와 deferred revenue의 증가율·절대 변화
- `GAAP net income - OCF` accruals와 평균자산 대비 accrual ratio
- `OCF / GAAP net income` 현금전환율
- working-capital의 OCF 기여액과 비중
- SBC 금액, 증가율, 매출 대비 비중
- restructuring, acquisition adjustment, tax benefit, one-off gain
- GAAP operating income·pretax income·net income·EPS에서 non-GAAP로의 reconciliation
- depreciation과 CAPEX의 성장률 및 D&A/CAPEX 비율
- 희석주식 수 변화 중 공시로 자사주매입에 귀속할 수 있는 EPS 효과

조정항목은 모두 부호가 있는 GAAP 영향으로 저장한다. 비용은 음수, tax benefit과
one-off gain은 양수다. 각 항목은 operating, non-operating, tax 중 손익계산서 위치를
명시하며 검증기는 다음을 다시 계산한다.

```text
core pretax income
  = core operating income + recurring non-operating income

core net income
  = core pretax income × (1 - normalized tax rate)

GAAP net income
  = core net income + signed after-tax adjustments

non-GAAP net income
  = GAAP net income - signed after-tax adjustments
```

보고된 GAAP operating income, pretax income, net income, EPS와 non-GAAP net income,
EPS가 이 bridge에 맞지 않으면 dossier 생성이 실패한다. SBC, restructuring,
acquisition adjustment, tax benefit, one-off gain 중 하나라도 빠져도 실패한다.

### EPS growth attribution

EPS 성장률은 다음 기여도의 합으로 정확히 재현한다.

```text
core operating income growth
  + recurring non-operating change
  + normalized tax-rate change
  + SBC change
  + restructuring change
  + acquisition adjustment change
  + tax benefit change
  + one-off gain change
  + share-repurchase effect
  + other net share-count change
  = reported GAAP EPS growth
```

자사주 효과는 단순히 전기 대비 주식 수 감소 전체로 간주하지 않는다. 공시와 계산
방법으로 자사주에 귀속 가능한 가중평균 주식 수 감소를 입력하고, 나머지는 SBC 발행,
인수대가 주식, 옵션 희석 등 `other_share_count_change`로 남긴다. 전기 EPS가 0이면
증가율 기여도는 `not_meaningful_prior_eps_zero`로 표시하지만 원자료와 절대 금액 분석은
계속 보존한다. Earnings Quality 결과는 점수로 압축하거나 추천 게이트의 우회 변수로
사용하지 않는다.

## Supply-chain Read-through

한 회사의 설명을 다른 문장으로 반복하는 것은 교차확인이 아니다. 모든 dossier는
대상 회사를 중심으로 고객, 공급업체, 경쟁사 또는 산업 peer를 관계 graph로 연결하고
각 관계에 upstream metric, 대상 회사 metric, 전달 메커니즘과 예상 시차를 명시한다.
모든 외부 entity는 graph상 대상 회사까지 연결되어야 하며 최소 하나의 수치 신호를
가져야 한다.

각 신호에는 다음을 저장한다.

- entity와 고객·공급업체·경쟁사·peer 역할
- 비교기간, 전기 값, 당기 값, 단위와 계산된 증감률
- 대상 회사 metric에 예상되는 증가·감소·중립 방향
- 신호가 가설을 지지하는지, 반박하는지 또는 불명확한지의 계산 결과
- 연결된 supply-chain relationship과 전달 시차
- 해당 entity 자신의 filing, guidance, earnings transcript 또는 regulatory filing
- 숫자를 대상 회사에 연결하는 방법론

뉴스 요약이나 대상 회사가 인용한 고객 발언은 해당 고객의 독립 확인으로 세지 않는다.
각 외부 신호에는 entity 이름과 source organization이 일치하는 1차 출처가 최소 하나
있어야 한다. 같은 회사의 여러 문서는 하나의 독립 조직으로만 계산한다.

기본 `buy` 기준은 최소 세 개 외부 기업, 최소 두 종류 역할, 세 개의 서로 다른 1차
출처 조직이 가설을 같은 방향으로 지지하는 것이다. 동시에 최소 하나의 외부 반대
신호를 반드시 보존한다. 반대 신호가 존재한다고 자동으로 매수를 금지하지는 않지만,
지지 기업이 세 곳 미만이면 `buy` dossier는 생성되지 않는다. 확인 결과는 점수로
축약하지 않으며 포지션 크기나 주문 권한을 직접 제어하지 않는다.

AMAT 사례의 TSMC·Samsung·SK hynix·Micron·Intel CAPEX나 LRCX·KLAC 전망은 가능한
entity와 metric의 예시일 뿐이다. 실제 dossier에서는 해당 시점의 각 기업 1차 자료를
확보하고, 총 CAPEX 중 장비 주소가능 비중·점유율·인식 시차를 별도 방법론으로 연결해야
한다. 여러 회사가 같은 산업 데이터 한 건을 재인용한 것은 독립 확인 세 건이 아니다.

## Earnings Call Diff / Management Calibration

실적 콜은 현재 분기 요약문 하나로 저장하지 않는다. 대상 회사가 직접 공개한 연속 두
분기의 earnings transcript를 사용하고, 안정적인 `topic_id`, `theme_id`, `guidance_id`로
두 시점을 맞춘다. 각 statement에는 원문, speaker, 유형, horizon과 0~4 confidence
annotation을 남긴다. confidence는 원문에 연결된 비교용 annotation이며 추천 점수나
포지션 크기로 사용하지 않는다.

검증기는 다음을 자동 계산한다.

- 새로 등장하거나 사라진 topic과 표현
- 같은 topic에서 추가·삭제된 단어, horizon과 confidence 변화
- 새로 등장하거나 사라진 analyst 질문 주제
- 답변 상태 `answered`, `partial`, `evaded`와 새로 회피된 질문
- 동일 지표 numeric guidance의 low, high, midpoint, width 변화와 범위 확대·축소
- 과거 약속의 `met`, `missed`, `changed`, `pending` 및 해결된 약속의 이행률

경영진 보수성·공격성은 정확히 직전 8개 분기의 동일 지표·동일 단위로만 측정한다.
각 분기 guidance low/high와 실제 결과를 비교해 range hit와 midpoint 오차를 계산하고,
지표에서 높은 값과 낮은 값 중 어느 쪽이 유리한지도 명시한다. 평균 유리방향 오차가
정책 threshold보다 크면 `historically_conservative`, 작으면
`historically_aggressive`, 그 사이면 `historically_balanced`로 표시한다. 이는 경영진
발언을 신뢰하라는 점수가 아니라 현재 guidance 해석에 붙는 과거 calibration이다.

콜 날짜와 guidance 실제기간은 연결된 1차 출처의 관측일보다 늦을 수 없다. transcript가
아닌 뉴스 요약으로 콜을 대체하거나, 8개 분기 중 빠진 값을 추정하거나, 서로 다른
지표를 이어 붙이면 dossier 생성이 실패한다.

## Positioning Analysis

소셜 sentiment와 단순 put/call ratio는 필수 입력으로 사용하지 않는다. 포지셔닝은
기관 보유구조, 공매도, 옵션을 서로 다른 데이터 계층으로 나누고 결과를 추천 점수로
압축하지 않는다.

### 기관 보유와 SEC filing

- SEC 13F를 manager별로 중복 제거한 연속 두 aggregate의 report date, snapshot
  availability date, 기관 보유주식 수와 manager 수
- 발행주식 수 대비 기관 보유율 변화와 현재 13F reporting lag
- 주요 주주의 top 1·5·10 집중도와 disclosed-holder coverage
- 데이터셋이 active/passive로 분류한 보유량과 분류 의존성 표시
- 13D, 13D/A, 13G, 13G/A의 filer, event/filed date, 지분율 변화와 intent
- Form 4의 transaction code, 취득·처분, direct·indirect, 수량과 가격
- ETF별 보유주식, issuer 지분율, ETF 내 weight와 시장가치

13F는 분기말 현재 보유를 나중에 보고하며 issuer-level 합계는 여러 manager filing을
정규화한 결과이므로 단일 filing처럼 표현하지 않는다. aggregate는 원 SEC accession
lineage와 snapshot availability date를 남기고 현재 포지션과 동일시하지 않는다.
13D/G와 Form 4는 filing date가 연결된 SEC 원문 관측일과 일치해야 한다. Form 4의
grant·option exercise·tax withholding은 공개시장 매수·매도와 다르므로 전체 순거래와
transaction code `P`·`S` 수량을 별도로 표시한다. ETF 보유량은 passive ownership에
포함될 수 있으므로 두 값을 더하지 않는다.

### Short

연속 settlement snapshot에서 short interest/float와 days-to-cover를 계산한다. 일별
short-sale volume은 거래 흐름이며 미결제 short position이 아니므로 별도 기간 합계와
비율로만 저장하고 `not_short_interest=true`를 강제한다. securities-lending 데이터가
있으면 borrow availability, fee와 utilization을 보존하고, 없으면 `unavailable`과 이유를
명시한다. short-interest 데이터에서 borrow cost를 역산하지 않는다.

### Options

최소 세 expiry의 ATM IV로 term structure를 만들고, 252개 이상 과거 ATM IV 관측치에서
현재 percentile을 재계산한다. 최소 22개 종가의 log return으로 연환산 realized
volatility를 계산해 nearest implied volatility와 비교한다. 25Δ put·ATM·call IV로
put-minus-call skew와 risk reversal을 모두 표시한다.

expiry·strike·call/put별 OI 원자료에서 총 OI, 주요 expiry와 상위 strike concentration을
계산한다. OI는 opening/closing과 buyer/seller 방향을 알 수 없으므로 방향성 포지션으로
단정하지 않는다. 최근 실적 expiry의 ATM straddle로 expected move를 계산하고, 최소
4개 과거 실적의 직전·직후 ATM IV에서 평균·중앙 IV crush를 산출한다.

CFTC futures positioning은 단일 종목 보유, short interest 또는 single-name options
자료가 아니다. commodity·rate·index futures가 기업 thesis에 직접 전달되는 경우에만
`context_only` macro overlay로 허용하며 `issuer_positioning_source=false`를 유지한다.
Positioning Analysis는 연구 해석 입력이며 recommendation gate나 position sizing을
직접 제어하지 않는다.

## 출처와 시점 규칙

- 모든 출처는 `as_of_date` 이전에 관측 가능해야 한다.
- consensus snapshot은 기본 45일, 다른 자료는 기본 120일을 넘으면 stale로 실패한다.
- 최소 세 개의 서로 다른 기관/조직을 사용한다.
- consensus 값에는 `consensus_dataset`, 현재 가격과 역산에는 `market_price` 출처가
  반드시 연결된다.
- URL이나 문서명만 적는 것으로 끝내지 않고 accession, snapshot ID, 표/행 위치 등
  재현 가능한 locator를 남긴다.
- 나중에 갱신된 컨센서스나 수정된 전망을 과거 dossier에 덮어쓰지 않는다.

## 가격 내재 기대 역산 규칙

모델은 설명용 문구가 아니라 현재 가격을 재현해야 한다. 각 chain은 solved metric을
제외한 가정을 고정하고 해당 metric을 역산한다. 저장되는 값은 다음을 포함한다.

- 사용한 모델과 식
- 고정 가정과 출처
- 역산된 metric 값
- 모델이 산출한 주가
- 실제 기준 주가와 상대 오차

상대 오차가 1%를 초과하면 “가격 내재”라고 부를 수 없고 검증이 실패한다. metric마다
다른 모델을 사용했다면 각각 독립적으로 가격을 재현해야 한다.

## 촉매와 반증

촉매는 “실적 발표” 같은 일정 이름만으로 부족하다. 발표 시 확인할 매출 mix, margin,
수주, CAPEX, FCF, 산업 수요처럼 논쟁을 해결할 관측 변수를 적는다. 각 variant chain은
최소 하나의 catalyst에 연결되어야 하며, 동시에 가설이 틀렸다고 판단할 사전 조건을
가져야 한다.

bear/base/bull driver 모델과 가격은 모두 필수다. 세 이익 시나리오는 같은 driver
집합을 사용해야 한다. 이익 모델과 valuation의 시나리오별 확률은 같고 합은 1이어야
하며 계산된 EPS와 가격은 각각 `bear < base < bull`이어야 한다. `buy`는 확률가중
기대수익이 양수이고, 적어도 하나의 가격 내재 기대 대비 bullish gap이 있으며, 정책상
최소 reward/risk를 충족할 때만 허용된다. Position Construction은 연구상 위험 예산이며
Supply-chain Read-through에서 세 개 외부 기업의 독립 1차 확인도 충족해야 한다.
Position Construction은 주문 권한이나 전략 승격 권한을 갖지 않는다.

## 생성과 검증

원천 JSON을 작성한 뒤 다음 명령으로 검증된 불변 dossier를 만든다.

```bash
python -m toss_trading.cli.research_validate_focus_dossier \
  --input /secure-input/AMAT-2026-08-20.json \
  --policy config/focused_research_policy.json \
  --code-revision "$(git rev-parse HEAD)" \
  --output-dir /home/seoje/toss-trading/stock-recommendation-runtime/focused-research/dossiers
```

검증기는 source 시점, source type, metric 범주, 가격 재현, gap 계산, driver-based
earnings/cash-flow/ROIC model과 sensitivity 재계산, Earnings Quality와 EPS 성장
attribution, GAAP/non-GAAP reconciliation, supply-chain graph 연결성, issuer-primary
source 독립성, 지지·반대 신호 재계산, earnings-call 언어·질문·guidance diff,
8개 분기 management calibration과 과거 약속 이행, estimate revision·목표가 분포·
analyst breadth·가격 divergence, SEC ownership filing·short·borrow·options positioning
재계산, valuation, 촉매 연결, 반증 증거, 포지션 상한과 추천 조건을 확인한다. 통과 결과에는
결정적 `dossier_id`와 `content_sha256`이 붙고 동일 ID의 `.md` 메모도 생성된다. 이후
내용이 바뀌면 추천 게이트의 hash 검사가 실패한다.

광범위 스크리닝 runner는 이 디렉터리의 dossier를 읽는다. 기준일로부터 기본 14일이
지난 dossier는 자동으로 stale 처리되어 새 매수 추천을 만들 수 없다.

## 아직 자동화하지 않는 것

컨센서스 숫자와 개별 기업 fundamental 전망은 무료 가격 데이터에서 추정하거나 LLM이
채우지 않는다. 적법하게 사용할 수 있는 point-in-time consensus와 공시/산업 데이터가
없는 종목은 `focused_research_required` 또는 `no_view`로 남긴다. “빈 추천”은 실패가
아니라, 시장과 다른 견해를 입증하지 못했다는 정직한 결과다.

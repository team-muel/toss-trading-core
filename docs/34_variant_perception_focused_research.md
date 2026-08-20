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

bear/base/bull 가격과 확률은 모두 필수다. 확률 합은 1이어야 하고 가격은
`bear < base < bull`이어야 한다. `buy`는 확률가중 기대수익이 양수이고, 적어도 하나의
가격 내재 기대 대비 bullish gap이 있을 때만 허용된다. 이 결론도 주문 권한이나 전략
승격 권한을 갖지 않는다.

## 생성과 검증

원천 JSON을 작성한 뒤 다음 명령으로 검증된 불변 dossier를 만든다.

```bash
python -m toss_trading.cli.research_validate_focus_dossier \
  --input /secure-input/AMAT-2026-08-20.json \
  --policy config/focused_research_policy.json \
  --code-revision "$(git rev-parse HEAD)" \
  --output-dir /home/seoje/toss-trading/stock-recommendation-runtime/focused-research/dossiers
```

검증기는 source 시점, source type, metric 범주, 가격 재현, gap 계산, 촉매 연결,
시나리오, 추천 조건을 확인한다. 통과 결과에는 결정적 `dossier_id`와
`content_sha256`이 붙는다. 이후 내용이 바뀌면 추천 게이트의 hash 검사가 실패한다.

광범위 스크리닝 runner는 이 디렉터리의 dossier를 읽는다. 기준일로부터 기본 14일이
지난 dossier는 자동으로 stale 처리되어 새 매수 추천을 만들 수 없다.

## 아직 자동화하지 않는 것

컨센서스 숫자와 개별 기업 fundamental 전망은 무료 가격 데이터에서 추정하거나 LLM이
채우지 않는다. 적법하게 사용할 수 있는 point-in-time consensus와 공시/산업 데이터가
없는 종목은 `focused_research_required` 또는 `no_view`로 남긴다. “빈 추천”은 실패가
아니라, 시장과 다른 견해를 입증하지 못했다는 정직한 결과다.

# 구현 단계 9 — Point-in-time 데이터 수집

## 목적과 경계

단계 9는 실시간 신호를 만들기 전에 재현 가능한 일봉·거시·기업 데이터를 수집한다.
모든 수집은 단계 8의 raw-first 저장소를 통과한다. Provider JSON은 secret 제거 후
bronze에 먼저 보존되고, 원본 schema와 데이터별 계약을 통과한 행만 silver가 된다.
계좌·주문·현금 SQLite DB를 읽거나 수정하지 않는다.

이 단계는 공급자 transport를 자동 실행하거나 외부 데이터 이용권을 부여하지 않는다.
`ProviderBatch`에는 transport가 실제로 관측한 source, endpoint, method, request,
HTTP status, provider/receipt/availability time, source revision, schema, license와 Git
revision을 전달해야 한다. 누락되거나 timezone-naive인 값은 실패한다.

## 수집 순서

`CollectionPlan`은 다음 순서를 고정하며 하나라도 `NO_TRADE`이면 이후 수집을 실행하지 않는다.

```text
거래 세션
-> 기업행동
-> ETF 일봉
-> USD/KRW FX
-> 무위험금리 곡선
-> 거시경제
-> 발표 직전 컨센서스
-> 기업 공시
-> 재무제표
-> 과거 Analyst estimate
```

모든 단계가 없는 plan은 `COLLECTION_PLAN_INCOMPLETE`로 거부된다.

## 공통 시간·출처 계약

- 각 데이터는 경제/시장 event 또는 reference period와 `available_at`을 분리한다.
- 발표 데이터는 scheduled, official, provider, received, available 시각을 구분한다.
- 공시는 period end, filed, accepted, received, available 시각을 구분한다.
- 모든 instant는 timezone-aware UTC이고 manifest 수신 시각보다 이른 availability는 없다.
- 모든 행의 `source`와 `source_revision`은 batch와 정확히 같아야 한다.
- manifest availability는 모든 행의 availability 이상이어야 하고, batch receipt는 행의
  receipt보다 빠를 수 없다. 따라서 manifest가 행을 조기 노출할 수 없다.
- 숫자는 JSON number/float가 아닌 유한한 decimal string이다.
- 필수 필드 누락은 명시적인 reason code가 되며 0이나 빈 문자열로 보정하지 않는다.
- HTTP/schema/normalization/mapping 실패는 bronze와 BLOCKED health만 남기며 silver를 만들지 않는다.
- `LatestSuccessfulDataset`은 cutoff 이전의 `silver/VALID` manifest만 선택한다. 이후 API 오류는
  마지막 성공 데이터를 대체하지 않는다. 같은 availability의 서로 다른 성공본은 CONFLICT다.

## 9.1 ETF 일봉

초기 허용 목록은 SPY, QQQ, BIL이다. 확대는 reference Universe 변경과 별도 검증이 필요하다.
필수 필드는 `instrument_id`, `event_time_utc`, `available_at`, `exchange_local_date`, OHLC,
volume, currency, session, adjustment, source, source_revision이다. OHLC 관계, 양수 가격,
음수가 아닌 volume, REGULAR session과 `raw|split_adjusted|total_return` basis를 검증한다.

가격 silver는 직접 downstream 입력이 아니다. `attach_price_context`가 가격, 거래 세션,
기업행동 silver manifest를 모두 검증하고 gold `daily-prices-with-context`를 생성한다.
각 가격의 instrument-exchange 매핑과 해당 local date의 open session이 없으면 차단한다.
서로 다른 공급자를 결합할 때는 combined source와 이용범위를 명시해야 하며, 부모보다
느슨한 재배포 권한으로 바꿀 수 없다.

## 9.2 거래 캘린더와 기업행동

세션은 exchange ID, local date, open 여부, regular open/close와 event/received/available
시각을 기록한다. 열린 세션의 close는 open 이후여야 한다. 기업행동은 instrument ID,
type, effective date, 비어 있지 않은 terms와 시간·출처를 기록한다. 지원 type은 dividend,
split/reverse split, merger, spinoff, delisting과 ticker change다.

기업행동이나 공시가 없다는 공급자 응답은 raw schema를 검증한 경우에만 `row_count=0`,
`quality_status=VALID`인 명시적 known-empty silver로 보존한다. 일반 정규화의 빈 결과는 성공이 아니다.

## 9.3 FX

최소 통화쌍은 USD/KRW다. event, availability, bid, ask, mid, source와 revision을 기록하고
`bid <= mid <= ask`를 강제한다. `REPORTING` 환율과 `EXECUTED_CONVERSION` 체결 환율을
별도 quote type과 별도 manifest로 보존한다.

## 9.4 무위험금리

한 곡선은 1, 3, 6, 12개월 horizon을 정확히 한 번씩 포함해야 한다. 각 점은 event,
availability, decimal rate, source와 revision을 기록한다. 누락 또는 중복 horizon은 전체
곡선을 `RISK_FREE_CURVE_INCOMPLETE` 또는 `RISK_FREE_HORIZON_INVALID`로 차단한다.

## 9.5 거시경제

초기 필수 series는 CPI, Core CPI, PCE, Core PCE, 고용, 실업률, 임금, GDP, PMI/ISM,
정책금리, 국채금리, 신용 spread proxy다. 한 batch에 전체 최소 집합이 있어야 한다.

각 행은 reference period, actual, prior value, prior vintage, revised prior, scheduled release,
official release, received, available, source와 revision을 기록한다. `available_at`은 공식 발표와
수신보다 빠를 수 없다. 이전 값과 revised prior는 별도 값으로 보존한다.

## 9.6 컨센서스와 Surprise

컨센서스는 실제 발표 전에 저장된 `KNOWN` snapshot만 허용한다. `snapshot_at`과
`available_at`이 발표 시각보다 이르지 않으면 Surprise 계산을 차단한다.

```text
Surprise = Actual - Consensus(t-)
```

현재 웹페이지의 과거 숫자나 발표 후 갱신된 숫자를 소급 사용하지 않는다. 역사적 snapshot이
없거나 UNKNOWN이면 silver와 Surprise를 만들지 않고 `CONSENSUS_HISTORY_UNKNOWN`으로 남긴다.

## 9.7 기업 공시

동일 회사·기간의 수집 우선순위는 10-K, 10-Q, 8-K, earnings release다. 각 공시는
period end, filed, accepted, received, available 시각과 source revision을 보존한다.
시간 순서가 깨지거나 우선순위가 역행하면 실패한다.

## 9.8 재무제표

최소 항목은 revenue, gross profit, operating income, net income, diluted EPS/shares,
operating cash flow, capex, free cash flow, cash, debt, receivables, inventory,
contract liabilities, deferred revenue와 stock-based compensation이다. 모든 값은 필수
decimal string이며 누락을 0으로 바꾸지 않는다. `free_cash_flow = operating_cash_flow - capex`
관계와 filing/acceptance/receipt/availability 순서를 검증한다.

## 9.9 Analyst estimate

metric, forecast period, consensus, 양수 analyst count, snapshot, availability, source와
revision을 기록한다. 과거 `KNOWN` snapshot만 silver가 되며 historical snapshot이 없다는
UNKNOWN 입력은 `ESTIMATE_HISTORY_UNKNOWN`으로 차단한다.

## 완료 조건과 검증

- 발표·수신·사용 가능 시각 분리: 각 계약 validator와 `ProviderBatch`가 강제한다.
- source와 revision 보존: batch와 행의 불일치는 `SOURCE_REVISION_CONFLICT`다.
- 누락을 0으로 채우지 않음: 필수 필드/decimal 검사 실패 시 bronze만 보존한다.
- API 오류가 최신 성공처럼 보이지 않음: latest resolver가 silver/VALID만 조회한다.
- adapter가 계좌 DB를 수정하지 않음: 단계 9 모듈은 sqlite/account/ledger import가 없고
  구조 테스트가 이를 검사한다.
- 캘린더·기업행동이 가격에 연결됨: gold 가격 context가 세 silver parent를 보존한다.

테스트는 `tests/test_phase9_data_collection.py`에 있으며 각 데이터군의 happy path와
누락, 시간 역행, unknown history, schema/source conflict, API 오류, 잘못된 수집 순서,
계좌 DB 의존 금지와 가격 context 계보를 포함한다.

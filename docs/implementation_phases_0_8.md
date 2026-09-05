# 구현 단계 0~8 현황 및 완료 조건 감사

감사 기준일: 2026-09-05  
감사 대상: `codex/phase8-immutable-datasets` (`8583f0b`)  
운영 모드: `READ_ONLY`, `live_trading_enabled: false`

## 판정 기준

이 문서는 각 단계의 계약 문서, 스키마 migration, 구현 코드, happy/failure-path
테스트와 CI 결과를 함께 대조한 결과다. 원래 단계 요청서가 저장소 안에 별도
파일로 모두 보존되어 있지는 않으므로, 완료 조건은 현재 승인된 정책과 단계별
문서 및 테스트가 강제하는 계약을 기준으로 재구성했다.

`완료`는 현재 브랜치에서 기능과 fail-closed 계약이 검증됐다는 의미다.
`운영 제한`은 구현 실패가 아니라 의도적으로 활성화하지 않은 외부 연동 또는
거래 권한이다. 열린 PR이 기본 브랜치에 병합되기 전에는 릴리스 완료로 보지 않는다.

## 전체 의존 순서

```text
0 거버넌스
  -> 1 공통 기반
    -> 2 Toss read-only 원본/account truth
      -> 3 주문 상태·체결
        -> 4 현금·포지션·결제
          -> 5 계좌 대사
            -> 6 point-in-time 시간 진실
              -> 7 종목·Universe 기준정보
                -> 8 불변 데이터 저장소
```

투자 실행 시의 별도 런타임 순서는 다음과 같이 DB evidence로 강제된다.

```text
investment policy -> account truth -> time truth -> data truth
-> financial calculation -> target portfolio -> risk control -> order
```

## 요약 판정

| 단계 | 범위 | 구현 판정 | 운영 판정 |
|---|---|---|---|
| 0 | 거버넌스·정책·안전 기본값 | 완료 | 거래 정책은 DRAFT, 실주문 금지 |
| 1 | 공통 타입·시간·설정·migration | 완료 | READ_ONLY만 기동 가능 |
| 2 | Toss read-only·원본·계좌 진실 | 완료 | 유효 자격증명·고정 IP 필요 |
| 3 | 주문 상태·누적 체결·delta | 완료 | 주문 전송은 비활성 |
| 4 | 현금·포지션·결제·tax lot | 완료 | 증거 없는 opening/결제는 차단 |
| 5 | 외부 계좌와 내부 원장 대사 | 완료 | 직접 현금 진실이 없으면 신규 거래 차단 |
| 6 | point-in-time 관측과 revision | 완료 | 실제 공급자 자료 품질은 별도 운영 책임 |
| 7 | instrument·alias·Universe·calendar·action | 완료 | fixture calendar는 실데이터가 아님 |
| 8 | bronze/silver/gold/catalog 불변 저장 | 완료 | 수집 runtime 자동 활성화 없음 |

## 단계 0 — 거버넌스 기반

완료 조건과 근거:

- 정책 registry가 문서 버전, 상태, 유효기간, 승인자와 SHA-256을 기록한다.
- 문서 변조, 승인되지 않은 정책, 잘못된 실행 모드는 기동을 차단한다.
- 모듈 의존 방향과 런타임 evidence 순서를 정의하고 테스트한다.
- 누락·오래됨·충돌·미대사는 `NO_TRADE`/차단으로 끝난다.
- `live_trading_enabled`는 registry와 runtime 설정에서 모두 `false`다.
- write adapter는 주문 제출을 수행하지 않고 `NoTrade`를 발생시킨다.

근거: `docs/architecture.md`, `docs/adr/`, `config/policy_registry.yaml`,
`scripts/check_governance.py`, `tests/test_phase0_governance.py`,
`tests/test_modular_monolith_structure.py`, `tests/test_investment_pipeline_order.py`.

판정: **완료**. Investment, risk, execution 정책은 의도적으로 DRAFT이며 Paper 이상의
승인이 아니다. 빈 scaffold 모듈은 향후 단계의 성공 결과로 간주하지 않는다.

## 단계 1 — 공통 소프트웨어 기반

완료 조건과 근거:

- Money, Quantity, Weight, Return 등 금융 값은 `Decimal`을 사용하고 float를 거부한다.
- 통화, 범위, 반올림 규칙과 명시적 도메인 오류를 제공한다.
- UTC-aware clock, frozen/replay clock과 `AsOfContext`를 제공한다.
- 설정은 data source, 승인 parameter set, risk limit, live 금지 여부를 검증한다.
- migration은 1부터 연속적이고, 적용된 이름/hash를 변경할 수 없으며 원자적으로 전진한다.
- 같은 migration 재실행은 no-op이고 더 새로운 DB나 gap은 기동을 차단한다.

근거: `src/asset_management/domain/`, `src/asset_management/time/`,
`src/asset_management/config/`, `docs/db_migrations.md`,
`tests/test_phase1_common_foundation.py`, `tests/test_integration_startup.py`.

판정: **완료**.

## 단계 2 — Toss read-only 및 계좌 진실

완료 조건과 근거:

- OAuth와 승인된 GET endpoint만 노출하고 주문 생성·변경·취소를 노출하지 않는다.
- 2xx, 비-2xx, network failure의 원본 증거를 정규화 전에 append-only로 저장한다.
- endpoint, method, request/response hash, UTC 요청·수신 시각, schema와 안전한 header를 보존한다.
- token, secret, cookie, 계좌 식별자와 개인정보를 저장 전에 제거한다.
- schema, enum, decimal, timestamp, pagination 오류와 unknown 상태를 차단한다.
- token bucket과 우선순위가 주문 상태·대사 조회를 신규 market data보다 우선한다.
- 완전한 account truth는 계좌, 보유, OPEN/CLOSED 주문, 상세·체결, buying power,
  sellable quantity, commission, KR/US calendar와 instrument reference를 포함한다.
- replay는 저장 hash를 검증하고 Toss에 접속하지 않는다.

근거: `docs/toss_read_only.md`, `src/toss_trading/broker/toss.py`,
`src/asset_management/broker/toss_read.py`, `src/asset_management/data/raw_store.py`,
`tests/test_phase2_toss_read_only.py`, `tests/test_foundation_account_state.py`.

판정: **완료**. 실운영 호출에는 Toss 자격증명과 허용 IP가 필요하지만 쓰기 권한은 없다.

## 단계 3 — 주문 상태와 체결 원장

완료 조건과 근거:

- 주문 상태는 append-only event와 연속 sequence로 기록한다.
- 허용되지 않은 전이, terminal 이후 전이, 시간 역행과 다른 내용의 idempotency 재사용을 거부한다.
- timeout은 실패나 재주문 승인으로 간주하지 않고 `REVIEW_REQUIRED`로 보존한다.
- unknown broker 상태와 취소/정정 거절은 명시적으로 차단한다.
- Toss 누적 체결에서 증가분만 delta로 만들고 감소나 상충 evidence를 거부한다.
- fill-bearing 상태, snapshot, delta, posting context와 원장 효과는 원자적으로 연결된다.

근거: `docs/order_state_execution.md`, migration 4와 6,
`src/asset_management/account/orders.py`, `account/executions.py`,
`execution/fills.py`, `ledger/posting.py`, `tests/test_phase3_order_state_execution.py`.

판정: **완료**. 실주문 전송은 범위 밖이며 계속 비활성이다.

## 단계 4 — 현금·포지션·결제 원장

완료 조건과 근거:

- 현금과 포지션 opening은 as-of, evidence, approver 없이는 생성되지 않는다.
- 거래 원금, commission, tax를 통화별 별도 append-only event로 기록한다.
- settled/unsettled, buy/sell reservation과 broker constraint를 분리한다.
- terminal 주문은 reservation을 정확히 0으로 해제한다.
- 수동 조정은 사전 승인 evidence와 동일 transaction을 요구한다.
- tax lot은 취득·결제시각, 통화, FX, commission, 정책 버전과 disposal을 보존한다.
- settlement date는 exact Toss raw fill evidence에서 추출·hash 검증하며 추측하지 않는다.
- replay는 원본, 체결, posting, 현금, 포지션, reservation과 settlement 계보를 재검증한다.

근거: `docs/cash_position_settlement.md`, migration 5·6·12,
`src/asset_management/ledger/`, `tests/test_phase4_cash_position_settlement.py`.

판정: **완료**.

## 단계 5 — 계좌 대사와 거래 gate

완료 조건과 근거:

- immutable broker snapshot과 내부 event ledger를 보유·평균단가·현금·주문·체결·비용·세금·결제·제약별로 비교한다.
- 수치 비교는 승인되고 유효한 tolerance를 요구하며 범주 값은 exact match한다.
- 결과는 MATCH, TOLERANCE_MATCH, MISMATCH, UNVERIFIABLE 또는 BLOCKED다.
- mismatch/unverifiable issue는 append-only이며 resolution에 note, approver와 최신 match evidence가 필요하다.
- 거래 gate는 최신·동일 계좌·동일 runtime·유효기간 내 reconciliation과 미해결 issue 부재를 요구한다.
- application 우회 insert도 DB trigger가 order intent를 차단한다.
- backup/restore와 replay가 같은 run, item, issue, resolution과 gate 결과를 보존한다.

근거: `docs/account_reconciliation.md`, migration 7·9,
`src/asset_management/ledger/reconciliation.py`,
`tests/test_phase5_account_reconciliation.py`.

판정: **완료**. 현재 승인된 Toss 응답이 직접 대사 가능한 통화별 현금 잔액을 제공하지
않으면 CASH는 UNVERIFIABLE이며 신규 거래가 차단된다. 이는 완료 실패가 아니라 필요한
진실을 만들지 않는 fail-closed 결과다.

## 단계 6 — Point-in-time 관측

완료 조건과 근거:

- 경제 기준기간·event·예정/공식 발표·provider·receipt·ingestion·availability·revision 시각을 분리한다.
- 모든 instant는 UTC-aware이고 source timezone을 별도로 보존한다.
- `available_at`은 publication, receipt, ingestion, revision보다 빠를 수 없다.
- 모든 조회는 명시적 `AsOfContext.information_cutoff_utc` 이하의 vintage만 반환한다.
- 미래 sentinel, 당일 장 마감 전 값, 발표 전 revision과 지연 수신을 사용할 수 없다.
- correction은 동일 series의 직선형 supersession이며 overwrite, branch와 cross-series 연결을 금지한다.
- 누락 또는 같은 시점의 모호한 최신 결과는 MISSING/CONFLICT로 차단한다.

근거: `docs/point_in_time.md`, `docs/temporal_policy.md`, migration 8·11,
`src/asset_management/data/repositories.py`, `data/asof_query.py`,
`tests/test_phase6_point_in_time.py`.

판정: **완료**.

## 단계 7 — 기준정보와 역사적 Universe

완료 조건과 근거:

- opaque instrument UUID를 사용하며 ticker/vendor symbol을 primary key로 사용하지 않는다.
- reference version과 alias는 source, effective interval, knowledge-time을 보존한다.
- 누락·모호한 alias, listing 이전/상장폐지 이후와 알 수 없는 Universe history를 차단한다.
- 미래 발효 version, correction과 과거 AsOf 조회가 함께 정확히 동작한다.
- exchange session은 IANA timezone, local date, 정규/장전/장후/조기종료 시각을 검증한다.
- corporate action은 split 등 단순 action을 append-only로 대사하고 복합 action은 수동 대사를 요구한다.
- price는 raw/split-adjusted/total-return basis를 구분하고 잘못된 회계 결합을 거부한다.
- 주문 전 provider symbol을 canonical instrument ID로 해석한다.

근거: `docs/reference_universe.md`, migration 13,
`src/asset_management/reference/`, `src/asset_management/data/prices.py`,
`tests/test_phase7_reference.py`.

판정: **완료**. 테스트 fixture calendar는 live 시장 calendar로 승격되지 않는다. 운영에서는
식별된 공급자의 session data가 필요하다.

## 단계 8 — 불변 데이터 저장소

완료 조건과 근거:

- manifest는 ID, source, dataset, schema version, retrieval/availability, content SHA-256,
  row count, license, code revision, parents와 quality를 기록한다.
- raw-first 순서로 secret 제거·bronze 게시·hash/manifest·raw schema 검증·정규화를 수행한다.
- bronze/silver/gold/catalog 계층과 schema, mapping, source-health catalog를 제공한다.
- 게시 API는 atomic no-replace이며 같은 canonical 원본은 같은 hash/blob로 deduplicate한다.
- silver/gold는 기존 하위 계층 parent와 availability/source/license 일치를 요구한다.
- endpoint, HTTP method와 request/query가 request hash에 반드시 포함된다.
- provider timestamp와 수신/availability 시각은 UTC 및 시간 순서를 검증한다.
- 자격증명 분류와 등록을 강제하고 잔존 Bearer/JWT/API-key 패턴을 저장 전에 차단한다.
- raw/normalized schema, instrument mapping, source health와 exact row count를 기록한다.
- normalizer source hash, Git revision과 mapping hash 변경은 새 manifest ID를 만든다.
- 실제 Toss dataset adapter는 read-only `get_*` 호출과 upstream raw evidence를 요구한다.
- 정규화·schema·mapping·HTTP 실패에도 bronze를 보존하고 `NO_TRADE` reason을 기록한다.

근거: `docs/immutable_datasets.md`, `schemas/dataset_manifest.schema.json`,
`src/asset_management/data/immutable.py`, `data/adapters/toss.py`,
`tests/test_phase8_immutable_datasets.py`.

판정: **완료**. 이 단계는 저장·adapter 계약을 제공하지만 scheduler나 외부 공급자 수집을
자동 활성화하지 않는다. 파일 관리자 권한에 의한 변조를 OS 수준에서 막는 저장장치는
아니며, reader가 hash 불일치를 감지하고 차단한다.

## 검증 결과

2026-09-05 재점검 결과:

- `python -m pytest -q`: **215 passed**
- `python scripts/check_governance.py`: 통과
- `python scripts/check_toss_openapi.py`: OpenAPI 1.2.14/hash 통과
- `python -m build --wheel`: 통과, manifest schema와 단계 문서 포함
- `git diff --check`: 통과
- PR #9와 PR #10의 Python 3.11/3.12 CI: 통과
- PR #9와 PR #10: GitHub 기준 mergeable, 아직 OPEN

## 최종 판정과 릴리스 조건

현재 기능 브랜치에서는 단계 0~8의 구현 및 fail-closed 완료 조건이 충족된다.
다만 다음 상태를 구현 완료와 혼동해서는 안 된다.

- 기본 브랜치 릴리스 완료: **아님**. 단계 7 PR #9와 그 위의 단계 8 PR #10이 열려 있다.
- Paper/Shadow/Live 거래 준비 완료: **아님**. Investment/risk/execution 정책이 DRAFT다.
- 실주문 승인: **없음**. write adapter와 설정에서 계속 차단된다.
- 실데이터 운영 준비: 자격증명, 공급자 이용권, live calendar, 현금 source-of-truth,
  영속 저장 위치·backup·retention 운영 구성이 별도로 필요하다.

PR 병합은 선행 단계 순서와 CI 성공을 보존해야 한다. 단계 7을 먼저 병합한 뒤 단계 8을
그 기준으로 병합하거나, 단계 8 PR의 base를 최신 기본 브랜치로 갱신해 같은 변경 순서를
유지해야 한다.

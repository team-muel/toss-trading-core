# AMA-38 — Model Registry, Lifecycle, and Approved Scope

Model registry는 model ID/version, 목적, 입출력, 승인 scope, 알려진 실패 모드,
validation/review 날짜, owner와 lifecycle 상태를 보존한다. 등록은 DEVELOPMENT에서만
시작하며 `DEVELOPMENT → VALIDATED → APPROVED → ACTIVE` 승격과 명시된
degrade/suspend/retire 경로만 허용한다. 모든 전이는 UTC 시각, 사유, evidence ID를
기록하고 registry snapshot은 canonical hash로 식별한다.

모델 실행은 요청 시각에 effective한 ACTIVE 상태, review 유효기간, 승인 scope를 모두 통과한 authorization을
요구한다. registry가 바뀌면 이전 authorization은 무효다. CAPM과 multifactor
legacy required-return 경로는 `REQUIRED_RETURN` authorization을 필수로 받으므로
`POSITION_SIZING`이나 `ORDER_CREATION`으로 직접 호출할 수 없다. DEGRADED,
SUSPENDED, RETIRED 또는 review overdue 모델은 실패로 닫힌다.

미래 시각으로 예약된 lifecycle 전이는 해당 시각 전에는 실행 권한을 만들지 않는다.
동일 시각 전이는 append-only 기록 순서로 해석되며, 이전 시각으로 되돌아가는 전이는 거부한다.

registry publication은 content-addressed catalog에 저장한다. 이 기능은 model 실행
권한만 통제하며 실주문 권한을 활성화하지 않는다. `live_trading_enabled=false`를
유지한다.

## Runtime-selected registry evidence (AMA-38 follow-up)

계산 테스트가 만든 임의의 in-memory registry는 production authority가 아니다. 실제
runtime은 먼저 append-only `am_model_governance_review_evidence`에 owner와 각
lifecycle 전이에 대응하는 review artifact를 기록해야 한다. 그 뒤 repository clock이
기록한 ingestion time과 review content hash를 포함해 `am_model_registry_snapshot`에
저장되고, content hash와 registry hash가 검증된 snapshot만 선택할 수 있다. 호출자는
published/bound timestamp를 제공할 수 없다. snapshot은 해당 `runtime_run_id`의
`information_cutoff` 이전에 발행돼야 하며, 선택은 runtime의 append-only time facts에
hash로 결속되고 어떤 pipeline stage evidence보다 먼저 완료돼야 한다.

`RuntimeModelRegistryEvidenceRepository.authorize`는 이 persisted binding에서만
authorization을 발급한다. CAPM/multifactor의 legacy required-return API와 canonical v2
pricing-baseline API, 그리고 calculation lineage binding은 모두 이 runtime token과
repository revalidation을 요구한다. snapshot이 없거나, cutoff 이후에 발행됐거나,
payload/review/runtime hash가 바뀌었거나, scope/lifecycle/review가 맞지 않으면
fail-closed다. 이 경계는 실제 model validation evidence를 만들어내지 않으며, 배포 전에
실제 review artifact와 snapshot을 반드시 수집해야 한다.

## Canonical return authority (AMA-38 / AMA-101)

`pricing_baseline_return`, `model_relative_alpha`,
`expected_benchmark_active_return`, `realized_active_return`, `regression_alpha`는
각각 같은 이름의 대문자 scope 하나와 해당 출력 하나만 허용한다.
검사는 양방향이다. canonical scope에서 다른 출력을 내는 경우뿐 아니라,
다른 scope가 canonical 출력을 선언하는 경우에도
`PRICING_OUTPUT_AUTHORITY_CONFLICT`로 차단한다. 사후 수익률과 regression alpha
scope를 추가해 사전 예측 및 주문 권한과 분리한다. JSON Schema도 같은 규칙을 적용한다.
새 CAPM/MULTIFACTOR v2는 `PRICING_BASELINE_RETURN`을 사용한다.
기존 REQUIRED_RETURN 모델의 payload와 registry hash는 변경하지 않는다.
이 변경은 출력 권한 계약이며 사후 수익률 계산기 자체를 추가하지 않는다.

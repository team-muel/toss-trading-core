# AMA-38 — Model Registry, Lifecycle, and Approved Scope

Model registry는 model ID/version, 목적, 입출력, 승인 scope, 알려진 실패 모드,
validation/review 날짜, owner와 lifecycle 상태를 보존한다. 등록은 DEVELOPMENT에서만
시작하며 `DEVELOPMENT → VALIDATED → APPROVED → ACTIVE` 승격과 명시된
degrade/suspend/retire 경로만 허용한다. 모든 전이는 UTC 시각, 사유, evidence ID를
기록하고 registry snapshot은 canonical hash로 식별한다.

모델 실행은 요청 시각에 effective한 ACTIVE 상태, review 유효기간, 승인 scope를 모두 통과한 authorization을
요구한다. registry가 바뀌면 이전 authorization은 무효다. CAPM과 multifactor
required-return 경로는 `REQUIRED_RETURN` authorization을 필수로 받으므로
`POSITION_SIZING`이나 `ORDER_CREATION`으로 직접 호출할 수 없다. DEGRADED,
SUSPENDED, RETIRED 또는 review overdue 모델은 실패로 닫힌다.

미래 시각으로 예약된 lifecycle 전이는 해당 시각 전에는 실행 권한을 만들지 않는다.
동일 시각 전이는 append-only 기록 순서로 해석되며, 이전 시각으로 되돌아가는 전이는 거부한다.

registry publication은 content-addressed catalog에 저장한다. 이 기능은 model 실행
권한만 통제하며 실주문 권한을 활성화하지 않는다. `live_trading_enabled=false`를
유지한다.

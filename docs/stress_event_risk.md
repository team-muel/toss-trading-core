# AMA-49 — Stress Scenario and Event Risk

Stress scenario는 risk input을 보수적으로 바꾸는 계약이다. 기대수익, model-relative
alpha, benchmark-active return, target weight를 만들거나 바꾸지 않는다. 각 scenario는
정해진 ID, asset/FX shock, 기준 통화, 평가 convention, 보유 기간, formula version,
liquidity/correlation multiplier를 보존한다. 실행 시 포트폴리오의 통화·평가·기간이 scenario와
다르면 `STRESS_INPUT_CONTEXT_INVALID`로 차단한다. 필요한 scenario 집합이 빠져도
`STRESS_SCENARIO_SET_INCOMPLETE`로 차단한다.

Event risk는 Earnings, FOMC, macro release의 예정 시각과 decision horizon을 비교한다.
결과는 `REDUCE`, `DEFER`, `BLOCK` 중 하나와 uncertainty/liquidity buffer만 포함한다.
시간이 horizon 밖이면 control을 만들지 않는다. naive time, 과거 event, 잘못된 horizon은
fail closed다.

Control은 기존 Risk Governor 입력에만 매핑한다. `REDUCE`는 `event_risk_high`,
`DEFER`는 `event_risk_high`와 `defer_execution`, `BLOCK`은 hard
`event_risk_blocked`를 설정한다. 따라서 이 모듈은 이미 만든 target을 위험 기준으로
감축·보류·차단할 수 있지만, target이나 return estimate를 바꾸지 못한다.

예상수익 overlay와 event penalty가 동일 information lineage를 사용하면 같은 event를 두 번
반영할 수 있다. 두 lineage ID 집합이 겹치면 `EVENT_RISK_LINEAGE_OVERLAP`으로 중단한다.
이 모듈은 기존 target/transition/execution의 위험 제약 입력만 제공하며 주문 권한이나
실거래를 활성화하지 않는다. `live_trading_enabled=false`를 유지한다.

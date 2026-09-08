# Gate E — Portfolio Decision Integrity Acceptance

Gate E는 M5 portfolio construction과 decision control이 M6 실행 구현으로 넘어갈 수 있는지를
판정하는 acceptance boundary다. 투자 가능 자본은 NAV 밖의 liability reserve만 차감하며,
absolute/strategic은 `forecast_total_return'w`, benchmark-relative는 사전 등록 benchmark의
`forecast_total_return'(w-b)`를 사용한다. `model_relative_alpha`는 pricing/factor baseline과
factor-neutrality 검증을 갖춘 전용 mode에서만 직접 사용할 수 있다.

제약 infeasibility, 비용·세금·유동성·no-trade 경제 단위, gross/net drag, 주문과 청산 capacity,
Risk Governor hard gate, transition prerequisite·forecast validity·cost curve, override replay,
decision return semantic/version authority, perturbation stability 및 결정 재현성을 각각 immutable
evidence로 확인한다. 하나라도 failed, missing 또는 unknown이면 결과는 `FAIL`이며
`permits_m6_execution`은 false다. 이 gate는 주문을 생성하거나 `live_trading_enabled: false`를
변경하지 않는다.

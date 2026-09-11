# 구현 단계 13 — 자산가격결정 엔진

아래 `required return`/`REQUIRED_RETURN` 설명은 초기 v1 계약 기록이다. 신규 정규 출력은
`pricing_baseline_return`이며 개인 hurdle이 아니다. v2 scope·출력과 기존 기록의 변환은
[AMA-101 보정 문서](economic_semantics_remediation.md)를 따른다.

정규 경로에서 이 모듈은 주문을 만들지 않고, 적용 가능한 EQUITY/EQUITY_ETF에 대해
`pricing_baseline_return`을 계산한다. 이는 개인 hurdle이나 주문 권한이 아니다. 지원
horizon은 21·63·126·252 거래일이며 연율 값은 `(1+R)^(h/252)-1`로 변환한다.

- 무위험금리 곡선은 `as_of`, horizon, 연율금리, source, quality를 보존하며 정보 cutoff 뒤 데이터와 비정상 품질을 거부한다.
- CAPM은 베타, 원시 베타, 표준오차, 관측수, lookback, R², 시점과 품질을 보존한다. 표준오차가 커질수록 베타를 시장 평균 1로 수축한다.
- 다요인 모델은 Market, Value, Momentum, Quality, Size, Profitability, Investment를 모두 요구한다. 당시 이용 가능했던 premium만 사용하고 추정오차를 출력한다.
- Black-Litterman은 CAPM·공급량·시가총액 안정성이 확인된 경우에만 정식 posterior 식 `Π + τΣP'(PτΣP'+Ω)^-1(Q-PΠ)`로 confidence가 있는 전망을 결합한다.
- Reverse DCF는 FCFF margin을 현금흐름에 한 번만 적용한다. `FCFF margin = operating margin × (1-tax rate) × (1-reinvestment rate)` 관계를 강제하고 매출성장률, 영업마진, FCF 마진, 할인율, terminal growth의 시장 내재값을 각각 역산한다.
- 오차 공분산이 없으면 독립성을 가정하지 않고 보수적인 triangle bound를 쓰며 출력 uncertainty는 결과와 같은 horizon 단위다.

정규 v2 결과 계약에는 종목, horizon, `pricing_baseline_return`, 상하한, 모델명과
version-bound `model_key`, factor loading, 추정불확실성, 품질, `as_of`, canonical
`output_hash`가 있으며 주문 방향이나 주문 생성 필드는 없다. 동일 입력과 `model_key`는
동일 output hash를 만들어 재현 검증에 사용한다. 결측·미래·충돌 입력은 값을 만들지 않고
명시적 오류로 종료한다.

`required_return` 필드와 `capm_required_return`/`multifactor_required_return` 함수는
`pricing-result@1`의 replay·명시적 migration 전용 호환 경로다. 이들은 새 계산, 새 저장,
새 모델 승인 또는 공통 결정 경로의 권한이 아니며, v2 baseline과 혼용할 수 없다.

각 결과는 forecast/holding horizon, `valid_until`, decay profile을 포함하는 signal
validity 계약도 보존한다. 계산 horizon과 forecast horizon이 다르면 결과 생성을
거부한다.

정규 `capm_pricing_baseline_return`과 `multifactor_pricing_baseline_return`은 ACTIVE
model registry에서 발급한 `PRICING_BASELINE_RETURN` authorization만 요구한다. legacy
v1 replay 함수는 원래 `REQUIRED_RETURN` authorization과 원래 hash를 보존하지만 v2를
authorize하지 않는다. 두 경로 모두 승인 scope, review 기한, registry snapshot 중 하나라도
맞지 않으면 계산 전에 실패한다.

# 구현 단계 14 — 기대수익률과 Alpha

아래는 초기 v1 구현 기록이다. 현재 주식 분해는 `expected-return@2`이며 per-share 성장에는
buyback을 별도 합산하지 않는다. aggregate 모드는 명시적으로 선택한다. 최신 의미와
호환성·잔여 작업은 [AMA-101 보정 문서](economic_semantics_remediation.md)를 따른다.

정규 기대수익률은 pricing baseline과 별도이며, 개별주, 주식 ETF, 채권 ETF, 현금성
자산, 원자재 ETF는 각각 고정된 component 계약을 사용한다. 다른 자산군의 component를
섞으면 계산을 거부한다. 채권·현금·원자재 경로는 equity pricing model의 부재를 이유로
실패하지 않으며, pricing이 적용되지 않는 자산에 `model_relative_alpha`를 만들지 않는다.

각 component는 이름, point estimate, uncertainty, confidence, 입력 feature ID와 horizon을 보존한다. 최종 gross 값은 component 합과 반드시 일치한다. 거래비용, 세금 drag, FX 비용을 각각 차감해 net 값을 만들며 confidence interval을 함께 저장한다. `shrink_component`는 신뢰도가 낮은 원시 전망을 `confidence*estimate + (1-confidence)*prior`로 prior 쪽에 수축한 뒤 그 값을 저장한다.

모든 component와 최종 전망은 동일한 forecast/holding horizon 계약을 가져야 한다.
`valid_until` 뒤 weight는 0이며 alpha는 `SIGNAL_EXPIRED`로 ABSTAIN한다. STEP,
LINEAR, EXPONENTIAL decay는 시간이 지날수록 weight를 유지 또는 감소시키며 미래
시각 평가와 서로 다른 horizon의 직접 결합은 실패로 닫힌다.

Component 오차의 상관행렬이 없을 때는 독립성을 임의 가정하지 않고 uncertainty 합을
보수적 상한으로 쓴다. 정규 `model_relative_alpha`는 적용 가능한
`net_forecast - pricing_baseline_return`이며, 같은 통화·basis·horizon과 모델 승인·lineage를
요구한다. 기대수익 하한에서 baseline 상한을 뺀 값과 기대수익 상한에서 baseline 하한을
뺀 값이 잔차 구간이다. 이 구간이 0을 포함하거나 데이터 품질 저하, 모델 불일치, 이벤트
직전, feature 충돌, 비용과 불확실성 buffer를 넘지 못하는 경우 `ABSTAIN`과 복수 reason
code를 반환한다. `calculate_alpha`와 `AlphaEstimate`는 `alpha-estimate@1` replay/migration
호환 경로이며 정규 asset-scope entry point나 benchmark-active return 권한이 아니다. 결과는 주문
방향이나 주문을 만들지 않는다.

## 완료조건 점검

- required return과 expected return 별도 저장: Alpha 계약이 두 값을 독립 필드로 보존한다.
- component 합과 최종값 일치: 생성 시 exact Decimal 합계를 검사하고 불일치는 거부한다.
- gross/net 구분: 거래비용, 세금 drag, FX 비용을 별도 필드로 보존한다.
- confidence interval 보존: component uncertainty를 합성해 기대수익과 Alpha 상하한을 기록한다.
- 불확실하면 ABSTAIN: 구간의 0 포함, 품질, 모델, 이벤트, feature, 비용 조건을 reason code와 함께 차단한다.

단계 테스트 16개와 전체 회귀 테스트 294개가 정상·실패 경로를 검증한다. Governance, Toss OpenAPI, schema parsing, compile, wheel build와 CI 결과도 PR에서 확인한다.

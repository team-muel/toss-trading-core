# 구현 단계 15 — 위험모형

Return panel은 total-return 기준, LOCAL/BASE/HEDGED 통화 기준, 결측정책, 상장 전 제외, winsorization과 연율화 계수를 명시한다. 결측값은 보간하거나 0으로 만들지 않는다. 외화 자산의 기준통화 수익률은 `(1+r_local)(1+r_fx)-1`이며 세 결과를 따로 보존한다.

표본 공분산은 불편추정량 `Σ(r_t-mean)(r_t-mean)'/(T-1)`을 연율화한다. EWMA는 정규화한 `λ` 가중치와 같은 가중평균을 중심으로 계산한다. Shrinkage는 `αF+(1-α)Σ`, factor covariance는 `BΣ_fB'+D`다. 모든 결과는 대칭 PSD 검사를 통과해야 한다. Stress covariance는 정상 covariance와 별도다. 역행렬 pivot이 불안정하면 diagonal fallback을 표시하고 optimizer를 차단한다.

포트폴리오 변동성은 `sqrt(w'Σw)`다. 한계 위험기여도는 `(Σw)_i/σ_p`, component 기여도는 `w_i*MRC_i`이며 합이 전체 변동성과 일치해야 한다. Historical VaR와 CVaR/Expected Shortfall은 손실을 양수로 표현하며 ES는 최악 tail 수익률 평균의 음수다. Gap stress와 명시적 asset/FX/liquidity scenario를 별도로 계산한다.

Exposure는 market beta, sector, industry, value, momentum, quality, growth, duration, credit, currency, liquidity를 보존한다. Event risk는 REDUCE·DEFER·BLOCK만 반환한다. Drawdown은 peak 대비 하락과 위험추정 오차, calibration, regime mismatch, data quality, execution quality를 함께 기록한다.

## 완료조건

- 모든 covariance에 PSD 검사: 충족
- 불안정 역행렬의 safe fallback 및 optimizer 차단: 충족
- 정상 covariance와 stress covariance 분리: 충족
- FX 포함 결과 분리: 충족
- 위험모형 실패 시 optimizer 금지: 충족
- Euler risk contribution 대사: 충족

단계 15 테스트는 정상 경로와 결측, 상장 전 자료, 이상치, 비PSD, 역행렬 fallback, tail, stress, event와 drawdown 실패 경로를 검증한다.

# 구현 단계 16 — 포트폴리오 구성

포트폴리오는 strategic allocation, tactical overlay, security selection, risk scaling, constraint projection, trade optimization의 여섯 층으로 계산한다. Strategic 자산군은 주식·채권·현금·금/원자재를 분리한다. Tactical 변화는 합계 0이고 정책 bounds 안에서만 허용한다. Risk scaling은 목표 변동성, drawdown multiplier와 confidence multiplier 중 가장 보수적인 값을 위험자산에 적용하고 현금을 residual로 맞춘다.

## 목적함수 점검

`maximize α'w - (γ/2)w'Σw - TC(w-w_prev) - η||w-w_prev||₁ - ξ||w||²`

분산항의 `1/2`는 gradient를 `γΣw`로 만드는 표준 표기다. 이를 생략해도 γ 정의만 달라지지만 정책 해석이 불명확해진다. `TC`는 `linear_cost*|Δw| + impact_cost*Δw²`다. TC가 turnover를 이미 포함한다고 선언한 경우 별도 `η`를 양수로 설정하면 중복으로 거부한다. Concentration은 HHI `Σw_i²`를 벌점으로 사용한다. 모든 항은 같은 투자 horizon의 return 단위여야 한다.

가중치 합 1, long-only, 단일종목, 자산군/sector, factor, 통화, 최소현금, 최대변동성, CVaR, stress loss, gross turnover `Σ|Δw|`, 최대 거래금액과 유동성 cap을 versioned policy에서 받는다. 숨은 기본 제한은 없다. Candidate가 infeasible이면 알려진 feasible 현재 포트폴리오와 candidate 사이에서 가장 먼 feasible 지점을 찾는다.

No-trade band는 변동성·spread·세금·비유동성으로 계산한다. 작은 비현금 변화는 유지하고 현금으로 합계 1을 재대사한다. `ExpectedBenefit > ExpectedCost + UncertaintyBuffer`가 거짓이면 현재 비중을 유지한다.

Solver 실패 fallback은 현재 포트폴리오, 위험최소 허용안, 승인 fallback, 현금성 자산, NO_TRADE 순서다. 임의 equal-weight를 만들지 않는다. Raw, risk-constrained, executable target을 별도 저장하며 optimizer 출력은 목표 비중과 lot 단위 목표수량뿐이다.

## 완료조건

- 모든 제약 만족 및 infeasible 명시: 충족
- 거래비용과 gross turnover 반영: 충족
- 주변 파라미터에서 비중 폭주 방지: simplex·cap projection과 concentration penalty로 충족
- optimizer가 주문 대신 목표 비중·수량만 반환: 충족
- solver failure에 임의 equal-weight 금지: 충족
- 세 target 별도 보존과 경제성 gate: 충족

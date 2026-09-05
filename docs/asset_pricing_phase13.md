# 구현 단계 13 — 자산가격결정 엔진

이 모듈은 주문을 만들지 않고 위험을 감수하기 위한 요구수익률과 시장 내재 가정을 계산한다. 지원 horizon은 21·63·126·252 거래일이며 연율 값은 `(1+R)^(h/252)-1`로 변환한다.

- 무위험금리 곡선은 `as_of`, horizon, 연율금리, source, quality를 보존하며 정보 cutoff 뒤 데이터와 비정상 품질을 거부한다.
- CAPM은 베타, 원시 베타, 표준오차, 관측수, lookback, R², 시점과 품질을 보존한다. 표준오차가 커질수록 베타를 시장 평균 1로 수축한다.
- 다요인 모델은 Market, Value, Momentum, Quality, Size, Profitability, Investment를 모두 요구한다. 당시 이용 가능했던 premium만 사용하고 추정오차를 출력한다.
- Black-Litterman은 CAPM·공급량·시가총액 안정성이 확인된 경우에만 정식 posterior 식 `Π + τΣP'(PτΣP'+Ω)^-1(Q-PΠ)`로 confidence가 있는 전망을 결합한다.
- Reverse DCF는 FCFF margin을 현금흐름에 한 번만 적용한다. `FCFF margin = operating margin × (1-tax rate) × (1-reinvestment rate)` 관계를 강제하고 매출성장률, 영업마진, FCF 마진, 할인율, terminal growth의 시장 내재값을 각각 역산한다.
- 오차 공분산이 없으면 독립성을 가정하지 않고 보수적인 triangle bound를 쓰며 출력 uncertainty는 결과와 같은 horizon 단위다.

결과 계약에는 종목, horizon, 요구수익률, 상하한, 모델명, factor loading, 추정불확실성, 품질, `as_of`만 있으며 주문 방향이나 주문 생성 필드는 없다. 결측·미래·충돌 입력은 값을 만들지 않고 명시적 오류로 종료한다.

각 결과는 forecast/holding horizon, `valid_until`, decay profile을 포함하는 signal
validity 계약도 보존한다. 계산 horizon과 forecast horizon이 다르면 결과 생성을
거부한다.

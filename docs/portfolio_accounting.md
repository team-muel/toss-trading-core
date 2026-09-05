# AMA-10 — Portfolio Accounting과 성과 기여도

Portfolio Accounting은 계좌 원장의 검증된 기간 시작 NAV, 종료 현금, 포지션 mark,
처분 lot, 외부 입출금, 배당·이자·수수료·세금을 하나의 보고 통화로 대사한다.
원통화 금액과 명시적 보고통화 FX를 항상 함께 받으며 통화를 암묵적으로 합산하지 않는다.

종료 NAV는 `보고통화 현금 + Σ(수량 × 시장가격 × 현재 FX)`다. 기간 총손익은
`종료 NAV - 시작 NAV - 순외부현금흐름`이다. 기여도는 local realized P&L,
local unrealized P&L, 배당, 이자, 수수료, 세금, FX로 분리하며 합이 총손익과
정확히 같지 않으면 `PORTFOLIO_ACCOUNTING_CONTRIBUTION_MISMATCH`로 차단한다.
수수료와 세금은 음수 비용만 허용한다.

TWR은 외부 현금흐름 직전 valuation으로 잘린 subperiod를 기하 연결한다. 다음
subperiod 시작 NAV는 직전 종료 NAV와 직후 입출금의 합과 정확히 일치해야 한다.
첫 시작 NAV, 마지막 종료 NAV, subperiod 현금흐름 합계도 각각 회계 기간의 시작 NAV,
종료 NAV, 외부 현금흐름 원장과 정확히 일치해야 한다.
따라서 입금 자체는 수익이 되지 않는다. 중간 valuation이 없는 현금흐름은 추정하지
않고 차단한다. MWR은 보조 XIRR 함수로 제공하며 투자자 관점에서 납입은 음수,
인출과 최종 NAV는 양수다. 해가 지정 bracket에 없으면 값을 만들지 않는다.

벤치마크 비교의 기초 인터페이스는 기하 상대수익
`(1 + portfolio_return) / (1 + benchmark_return) - 1`을 반환한다.
최종 결과의 canonical 필드를 SHA-256으로 묶고 JSON Schema를 wheel에 포함한다.
이 기능은 회계 계산만 수행하며 주문 또는 실거래 기능을 생성하지 않는다.

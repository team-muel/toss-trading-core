# AMA-107 — Strategy Registry, Lifecycle, and Capital Budget

Strategy Registry는 Model Registry와 독립된 투자 규칙 계약이다. 각 immutable strategy
version은 경제 가설, 투자 가능 유니버스, Signal 및 forecast-combination 버전, pricing/risk
model 버전, portfolio/execution policy, benchmark, 자본·위험 예산, horizon/currency,
허용 runtime mode와 유효 기간을 함께 기록한다. 그러므로 Signal, optimizer 또는 execution
policy가 달라지면 새 strategy version을 등록해야 하며, 같은 key의 정의 교체는 거부된다.

전략은 `RESEARCH → CANDIDATE → PAPER → SHADOW → LIVE` 승격 경로와 명시적인
`DEGRADED`, `SUSPENDED`, `RETIRED` 경로만 따른다. 모든 전이는 UTC 시각, 사유와
evidence ID를 append-only로 보존한다. 미래 전이는 그 시각 전의 authorization을 만들지
않으며, suspension과 recovery는 Model lifecycle과 별개로 평가한다.

authorization은 상태·허용 mode·유효 기간·registry hash를 결속한다. registry가 변하면
기존 authorization은 무효다. `LIVE` 상태와 mode는 계약상 표현할 수 있지만 현재
`live_trading_enabled=false`에서는 항상 `STRATEGY_LIVE_RUNTIME_DISABLED`로 실패한다.
이 registry는 실주문 권한을 부여하지 않는다.

전략 성과 귀속은 gross return, implementation cost, net return, benchmark return과
forecast component lineage를 content hash로 고정해 `strategy-attribution` catalog에
별도 기록한다. registry snapshot 역시 content-addressed catalog으로 발행한다.

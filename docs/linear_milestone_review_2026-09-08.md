# Linear milestone plan review — 2026-09-08

검토 기준: `d23ba7d44c360dbb1121c12aa558b6e12c9e5c8b`, AMA-101 PR #60.
프로젝트 설명, 11개 마일스톤 설명, 세부 이슈 121건의 본문과 관계를 읽었다.
그중 In Review 라벨 28건을 별도로 대조했다. 본문 수용 조건이 검토 대상이며 댓글 유무는 완료 판단 근거가 아니다.
이 문서는 계획 전수 열람과 발견사항 기록이다. 121개 기능 모두의 운영 수용 검증 완료를 뜻하지 않는다.

## 확인된 수정과 불일치

- AMA-38 canonical 출력이 POSITION_SIZING 등 잘못된 scope로 등록 가능한 역방향 누락을 수정했다. REALIZED_ACTIVE_RETURN/REGRESSION_ALPHA도 분리했다. schema와 정상/실패 및 legacy registry hash 테스트를 함께 변경했다.
- 상위 프로젝트 및 M4/M5/M8 설명의 `ex_ante_active_return`은 최신 AMA-100/101/52/53의 분리된 의미와 불일치한다. asset별 `model_relative_alpha = net_forecast_total_return - pricing_baseline_return`, portfolio별 `expected_benchmark_active_return = (w-b)'μ`를 구분한다. 의미를 모르는 과거 필드는 자동 치환하지 않는다.
- M3.5 상위 설명의 Mapping→Orthogonalization 순서는 AMA-104→105 의존 순서와 불일치한다. 실제 작업은 neutralization 후 calibration 순서로 유지한다.
- AMA-57 cost flag와 AMA-123 callable 경계는 통합 경제 계산 증거를 대신하지 않는다.
- AMA-122 provider readiness, AMA-120 sealed holdout, AMA-125 ETF 경제성 검증은 별도 필수 단계다. 기존 adapter/fixture PASS로 대체하지 않는다.

## In Review 28건 대조

| 이슈 | 판단 / 남은 조건 |
|---|---|
| AMA-10 | NAV 포함관계/결제 회계 구현 재사용. 실제 provider NAV 증거와 운영 snapshot 연결은 미완료. |
| AMA-102 | 기존 Feature와 Signal 분리 계약 재사용. Done 대신 review 상태 유지. |
| AMA-39 | 기존 D1 fixture PASS를 전체 모델 운영 승인으로 간주하지 않음. |
| AMA-38 | 이번 수정: 5개 canonical 출력↔scope 양방향 검증, 사후 수익률/regression scope, schema 및 legacy hash 회귀 검증. |
| AMA-37 | 기존 계산 lineage 재사용; 신규 v2 semantic 필드와 실제 입력 lineage 연결 필요. |
| AMA-32 | 기존 horizon/validity/decay 계약 재사용. 전체 forecast→transition 연결 증거 별도 필요. |
| AMA-30 | 기존 Gate C fixture 재사용; AMA-122 실제 provider sample pull/라이선스 readiness 별도 필요. |
| AMA-22 | 기존 PIT Gate B 증거 재사용; 운영 데이터의 PIT 보장으로 확대 해석하지 않음. |
| AMA-15 | Gate A fixture와 실제 cash-source 검증을 구분. TOSS_CASH_SOURCE_UNVERIFIABLE 해소 증거 없으면 승격 불가. |
| AMA-12 | 기존 계층/CI 기준선 재사용. 이번 변경도 전체 테스트와 governance 검증 대상. |
| AMA-57 | governor.py의 cost_exceeds_benefit bool 확인. 공통 단위의 계산 증거 연결이 남아 있어 새 Gate E 완료 선언 금지. |
| AMA-109 | 기존 quote/session/halt 경계 재사용; 실제 historical fidelity 증거와 연결 필요. |
| AMA-62 | 기존 intent/netting/rounding 재사용; Gate E 및 same-run 계좌/권한 증거 선행. |
| AMA-114 | 기존 crash invariants 계약 재사용; AMA-116/117/118 실제 persistence/boot/chaos 검증을 대신하지 않음. |
| AMA-61 | 이전 Gate E fixture는 최신 AMA-100/101 통합 수용 조건 전체 충족 증거가 아님. |
| AMA-60 | 기존 v2 journal 및 v1 replay 보존 재사용. 실제 upstream applicability/경제 단위 증거 연결 필요. |
| AMA-59 | 기존 immutable override 계약 재사용; 본문 재독으로 신규 구현 필요를 단정하지 않음. |
| AMA-58 | 기존 transition 계약 재사용; forecast 유효기간/decay와 실제 비용 단위 경로 통합 필요. |
| AMA-124 | 기존 mandate 계약 재사용; benchmark/lambda 승인 버전을 실제 optimizer/run spec에 고정 필요. |
| AMA-71 | 기존 attribution 계약 재사용; AMA-120 sealed holdout 및 실제 실행 fidelity 데이터 필요. |
| AMA-123 | decision_kernel.py는 주입된 calculate callable 계약. 전체 replay/paper/shadow/live 실제 계산 경로 통합이 증명된 것으로 간주하지 않음. |
| AMA-119 | 기존 immutable run specification 재사용; 실제 dataset/cost/strategy 버전 동결 필요. |
| AMA-108 | 기존 D1.5 fixture PASS 재사용; 실제 연결 증거 없이 M4 승인으로 확대하지 않음. |
| AMA-107 | 기존 Strategy Registry 재사용; 실제 구성요소 버전 승인과 운영 승격은 별도. |
| AMA-106 | 기존 combination 재사용; 104 lineage와 비용/상관 증거를 실제 common path에 연결 필요. |
| AMA-105 | 기존 calibration 재사용; 실제 OOS calibration/coverage 검증 필요. |
| AMA-104 | 기존 neutralization 재사용; 105 mapping 이전이라는 issue dependency 우선. |
| AMA-103 | 기존 diagnostics 재사용; 실제 OOS/purge/embargo 데이터 증거는 별도. |

## 이어갈 순서

선행 PR/Gate 재확인 → AMA-100/101 보정 및 v2 실제 입력·common path 연결 → M4 D2/M5 E 최신 계약 통합 검증 → M6 재개.
M6에서는 114→62→109→110, event ordering 65와 durable submission/persistence 115/63/116, fidelity 121을 선행 조건에 맞게 연결한다. 실제 validation은 119→66/67/68/69/70→120→71/72→Gate F 순서를 지킨다. AMA-125 경제성 증거 없이 GCP 운영 승격을 선언하지 않는다.
완료 상태 변경, merge, 배포, 실주문 활성화는 이 보고서로 발생하지 않는다. `live_trading_enabled=false` 유지.

## 전수 열람 목록

아래 상태는 열람 시점 Linear 상태이며 구현 완료에 대한 새로운 판정이 아니다.

| 마일스톤 | 이슈 | 제목 | 상태 | In Review |
|---|---|---|---|---|
| M4 — Pricing, Expectations & Risk | AMA-101 | Economic Semantics Remediation — Completed Accounting·Pricing·Risk Modules | In Progress |  |
| M0 — Governance & Account Truth | AMA-9 | 통화별 현금·Settlement·Reserved Cash·Executable Buy Limit 원장 구축 | Done |  |
| M0 — Governance & Account Truth | AMA-10 | Portfolio Accounting·성과 기여도 구축 | In Progress | 예 |
| M4 — Pricing, Expectations & Risk | AMA-100 | Economic Semantics·Return/Risk Taxonomy 계약 구축 | In Progress |  |
| M3.5 — Signal & Forecast Engineering | AMA-102 | Signal Definition Registry·Transform Contract 구축 | In Progress | 예 |
| M6 — Paper, Replay & Validation | AMA-63 | Persistent Paper Broker Core 구축 | In Progress |  |
| M6 — Paper, Replay & Validation | AMA-115 | Durable Order Submission·Ambiguous Broker State Recovery 구축 | In Progress |  |
| M6 — Paper, Replay & Validation | AMA-65 | Event Store·Deterministic Ordering 구축 | In Progress |  |
| M6 — Paper, Replay & Validation | AMA-110 | Execution Policy·Trade Scheduling·Child Order Planner 구축 | In Progress |  |
| M3 — Features, States & Model Governance | AMA-39 | Gate D1 — Feature/State/Model Integrity Acceptance | In Progress | 예 |
| M3 — Features, States & Model Governance | AMA-38 | Model Registry·Lifecycle·Approved Scope 구축 | In Progress | 예 |
| M3 — Features, States & Model Governance | AMA-37 | Calculation Lineage Graph 구축 | In Progress | 예 |
| M3 — Features, States & Model Governance | AMA-32 | Decision Horizon·Signal Validity·Decay 계약 구축 | In Progress | 예 |
| M2 — Data & Quality Plane | AMA-30 | Gate C — Data Truth Acceptance | In Progress | 예 |
| M1 — Temporal & Reference Truth | AMA-22 | Gate B — Temporal Truth Acceptance | In Progress | 예 |
| M0 — Governance & Account Truth | AMA-15 | Gate A — Account Truth Acceptance | In Progress | 예 |
| M0 — Governance & Account Truth | AMA-12 | 모듈 구조·Config·Migration·CI 기준선 구축 | In Progress | 예 |
| M5 — Portfolio Construction & Decision Control | AMA-57 | Risk Governor v2 구축 | In Progress | 예 |
| M6 — Paper, Replay & Validation | AMA-109 | Execution Microstructure Lite — Quote·Spread·Session·Halt 구축 | In Progress | 예 |
| M6 — Paper, Replay & Validation | AMA-62 | Order Intent·Open-order Netting·Rounding·Session Planner 구축 | In Progress | 예 |
| M6 — Paper, Replay & Validation | AMA-114 | Crash Recovery Invariants·RPO/RTO Contract 구축 | In Progress | 예 |
| M5 — Portfolio Construction & Decision Control | AMA-61 | Gate E — Portfolio Decision Integrity Acceptance | In Progress | 예 |
| M5 — Portfolio Construction & Decision Control | AMA-60 | Decision Journal·Economic Semantics·Decision Quality 구축 | In Progress | 예 |
| M5 — Portfolio Construction & Decision Control | AMA-59 | Human Override·Manual Intervention Governance 구축 | In Progress | 예 |
| M5 — Portfolio Construction & Decision Control | AMA-58 | Portfolio Transition·Multi-period Rebalancing Planner 구축 | In Progress | 예 |
| M4 — Pricing, Expectations & Risk | AMA-124 | Investor Mandate·Benchmark·Risk Preference Authority Contract 구축 | In Progress | 예 |
| M6 — Paper, Replay & Validation | AMA-71 | Quant Performance·Benchmark-relative Attribution 구축 | In Progress | 예 |
| M6 — Paper, Replay & Validation | AMA-123 | Research–Production Parity·Single Decision Kernel Contract 구축 | In Progress | 예 |
| M6 — Paper, Replay & Validation | AMA-119 | Backtest Run Specification·Preregistration Contract 구축 | In Progress | 예 |
| M3.5 — Signal & Forecast Engineering | AMA-108 | Gate D1.5 — Signal & Forecast Integrity Acceptance | In Progress | 예 |
| M3.5 — Signal & Forecast Engineering | AMA-107 | Strategy Registry·Lifecycle·Capital Budget Contract 구축 | In Progress | 예 |
| M3.5 — Signal & Forecast Engineering | AMA-106 | Forecast Combination·Diversification·Cost-aware Weighting 구축 | In Progress | 예 |
| M3.5 — Signal & Forecast Engineering | AMA-105 | Signal-to-Forecast Mapping·Calibration Engine 구축 | In Progress | 예 |
| M3.5 — Signal & Forecast Engineering | AMA-104 | Signal Neutralization·Orthogonalization·Incremental Predictive Power 구축 | In Progress | 예 |
| M3.5 — Signal & Forecast Engineering | AMA-103 | Signal Predictive Diagnostics — IC·RankIC·Spread·Decay·Turnover 구축 | In Progress | 예 |
| M6 — Paper, Replay & Validation | AMA-120 | Final Holdout·Sealed OOS Governance 구축 | Backlog |  |
| M6 — Paper, Replay & Validation | AMA-72 | Promotion Decision·Evidence Gate 구축 | Backlog |  |
| M6 — Paper, Replay & Validation | AMA-66 | End-to-End Event Replay Engine 구축 | Backlog |  |
| M6 — Paper, Replay & Validation | AMA-73 | Gate F — Execution & Validation Acceptance | Backlog |  |
| M4 — Pricing, Expectations & Risk | AMA-49 | Stress Scenario·Event Risk Engine 구축 | Backlog |  |
| M8 — Individual Stock Expansion | AMA-92 | Gate H — Individual Stock Expansion Acceptance | Backlog |  |
| M8 — Individual Stock Expansion | AMA-91 | Individual Stock Paper E2E Vertical Slice 구축 | Backlog |  |
| M7 — Operations & ETF Vertical Slice | AMA-80 | Daily·Weekly·Incident Reporting 구축 | Backlog |  |
| M8 — Individual Stock Expansion | AMA-88 | Stock Multifactor Pricing Baseline·Forecast Total Return Integration 구축 | Backlog |  |
| M6 — Paper, Replay & Validation | AMA-125 | ETF Economic Candidate Validation — Pre-GCP Paper Evidence | Backlog |  |
| M7 — Operations & ETF Vertical Slice | AMA-82 | ETF Operational Point-in-Time Vertical Slice E2E 구축 | Backlog |  |
| M7 — Operations & ETF Vertical Slice | AMA-118 | Chaos Recovery Matrix·Fault Injection·GCP Reboot Drill 구축 | Backlog |  |
| M4 — Pricing, Expectations & Risk | AMA-50 | Gate D2 — Pricing·Expectation·Risk Integrity Acceptance | Backlog |  |
| M5 — Portfolio Construction & Decision Control | AMA-53 | Constrained Portfolio Optimizer v1 — Absolute/Active Mode 구축 | Backlog |  |
| M5 — Portfolio Construction & Decision Control | AMA-52 | Strategic/Absolute Allocation·Active Overlay·Risk Scaling 계약 구축 | Backlog |  |
| M0 — Governance & Account Truth | AMA-5 | 투자·위험·실행 정책 계약 정의 | Done |  |
| M4 — Pricing, Expectations & Risk | AMA-43 | Multifactor Pricing Baseline Return Engine 구축 | Done |  |
| M4 — Pricing, Expectations & Risk | AMA-44 | Asset-class Expected Return Framework 구축 | Done |  |
| M4 — Pricing, Expectations & Risk | AMA-45 | Model-Relative Alpha·Shrinkage·Confidence·ABSTAIN Engine 구축 | Backlog |  |
| M5 — Portfolio Construction & Decision Control | AMA-54 | Transaction Cost·Turnover·No-trade Band·Benefit Unit Contract 구축 | Backlog |  |
| M9 — Controlled Production Promotion | AMA-93 | Shadow Runtime — Real Reads, Zero Broker Writes 구축 | Backlog |  |
| M6 — Paper, Replay & Validation | AMA-121 | Execution Fidelity·Historical Data Adequacy Contract 구축 | Backlog |  |
| M2 — Data & Quality Plane | AMA-28 | Historical Analyst Estimate Snapshot 계약 구축 | Done |  |
| M2 — Data & Quality Plane | AMA-23 | DataAdapter 계약·Immutable Bronze/Silver/Gold·Dataset Manifest 구축 | Done |  |
| M2 — Data & Quality Plane | AMA-27 | SEC Filing·Fundamentals Point-in-Time 정규화 구축 | Done |  |
| M2 — Data & Quality Plane | AMA-29 | Data Quality·Source Health·Fallback·Quarantine Engine 구축 | Done |  |
| M2 — Data & Quality Plane | AMA-26 | Macro Vintage·Release Calendar·Consensus Snapshot 구축 | Done |  |
| M6 — Paper, Replay & Validation | AMA-122 | Data Provider Registry·Acquisition Readiness 구축 | Backlog |  |
| M2 — Data & Quality Plane | AMA-25 | FX·Risk-free Curve Adapter 구축 | Done |  |
| M2 — Data & Quality Plane | AMA-24 | Point-in-Time Daily Market Bar Adapter 구축 | Done |  |
| M6 — Paper, Replay & Validation | AMA-111 | Transaction Cost Analysis — Implementation Shortfall·Execution Attribution 구축 | Backlog |  |
| M6 — Paper, Replay & Validation | AMA-70 | Forecast·Risk·Execution Calibration 구축 | Backlog |  |
| M6 — Paper, Replay & Validation | AMA-68 | Nested Walk-forward·Purged/Embargo OOS Validation Framework 구축 | Backlog |  |
| M6 — Paper, Replay & Validation | AMA-64 | Paper Execution Realism — Partial Fill·Fee·Spread·Slippage·Settlement·FX 구축 | Backlog |  |
| M7 — Operations & ETF Vertical Slice | AMA-117 | Boot Recovery Protocol·Privilege Reduction·Startup Reconciliation 구축 | Backlog |  |
| M6 — Paper, Replay & Validation | AMA-116 | Crash-consistent Persistence·Atomic Fill/Ledger Recovery 구축 | Backlog |  |
| M9 — Controlled Production Promotion | AMA-98 | Micro-live Execution Drill 구축 | Backlog |  |
| M7 — Operations & ETF Vertical Slice | AMA-83 | Gate G — Operations & ETF Vertical Slice Acceptance | Backlog |  |
| M7 — Operations & ETF Vertical Slice | AMA-81 | Backup·Restore·Disaster Recovery Drill 구축 | Backlog |  |
| M7 — Operations & ETF Vertical Slice | AMA-78 | Kill Switch·Recovery Approval State Machine 구축 | Backlog |  |
| M7 — Operations & ETF Vertical Slice | AMA-76 | Monitoring·Operational Metrics·Alerting 구축 | Backlog |  |
| M7 — Operations & ETF Vertical Slice | AMA-75 | Daily/Event Orchestrator·Account Lock·Checkpoint 구축 | Backlog |  |
| M7 — Operations & ETF Vertical Slice | AMA-74 | Runtime Mode·State Transition Machine 구축 | Backlog |  |
| M7 — Operations & ETF Vertical Slice | AMA-77 | Capability-based Degraded Mode 구축 | Backlog |  |
| M0 — Governance & Account Truth | AMA-14 | Foundation 원장 Replay·멱등성 복구 구축 | Done |  |
| M0 — Governance & Account Truth | AMA-11 | 계좌 대사 Gate 구축 | Done |  |
| M0 — Governance & Account Truth | AMA-8 | 주문 상태머신·체결 delta 원장 구축 | Done |  |
| M6 — Paper, Replay & Validation | AMA-69 | Bootstrap·Parameter Perturbation·Plateau·Multiple-testing 검증 구축 | Backlog |  |
| M9 — Controlled Production Promotion | AMA-99 | Gate K — Micro-live Acceptance & Capital Authority Scaling | Backlog |  |
| M9 — Controlled Production Promotion | AMA-113 | Strategy Capacity·Marginal Net Alpha·Capital Scaling Model 구축 | Backlog |  |
| M7 — Operations & ETF Vertical Slice | AMA-112 | Strategy Health·Signal Decay·Distribution Shift Monitoring 구축 | Backlog |  |
| M4 — Pricing, Expectations & Risk | AMA-46 | Covariance·Factor + Specific Risk Engine 구축 | Backlog |  |
| M8 — Individual Stock Expansion | AMA-90 | Earnings·Filing Event Gate 구축 | Backlog |  |
| M8 — Individual Stock Expansion | AMA-87 | Reverse DCF·Implied Assumption Surface 구축 | Backlog |  |
| M3 — Features, States & Model Governance | AMA-36 | Portfolio·System State Engine 구축 | Done |  |
| M5 — Portfolio Construction & Decision Control | AMA-56 | Liquidity Constraint·Order/Position Capacity·Liquidation Cost 구축 | Backlog |  |
| M5 — Portfolio Construction & Decision Control | AMA-55 | Tax-aware Portfolio Adjustment 구축 | Backlog |  |
| M5 — Portfolio Construction & Decision Control | AMA-51 | Capital Flow·Liability·Liquidity Reserve 구축 | Backlog |  |
| M4 — Pricing, Expectations & Risk | AMA-48 | VaR·CVaR·FX·Liquidity Risk Engine 구축 | Backlog |  |
| M4 — Pricing, Expectations & Risk | AMA-42 | Factor Return·Loading·Pricing Premium Engine 구축 | Backlog |  |
| M4 — Pricing, Expectations & Risk | AMA-40 | Horizon·Currency-aligned Risk-free Curve Engine 구축 | Backlog |  |
| M4 — Pricing, Expectations & Risk | AMA-47 | Portfolio Volatility·Variance/Volatility Contribution·Factor Exposure 구축 | Done |  |
| M4 — Pricing, Expectations & Risk | AMA-41 | CAPM Pricing Baseline·Beta Estimation·Shrinkage 구축 | Done |  |
| M3 — Features, States & Model Governance | AMA-35 | Market·Company State Engine 구축 | Done |  |
| M3 — Features, States & Model Governance | AMA-34 | Company Feature Set v1 구축 | Done |  |
| M3 — Features, States & Model Governance | AMA-33 | Market·Macro Feature Set v1 구축 | Done |  |
| M3 — Features, States & Model Governance | AMA-31 | Feature Registry·Snapshot·Leakage Guard 구축 | Done |  |
| M1 — Temporal & Reference Truth | AMA-17 | AS-OF Query Engine·Late-arriving Data 정책 구축 | Done |  |
| M0 — Governance & Account Truth | AMA-6 | 공통 도메인 타입·Clock·AsOfContext 구축 | Done |  |
| M1 — Temporal & Reference Truth | AMA-21 | Corporate Action·가격 의미 계약 구축 | Done |  |
| M1 — Temporal & Reference Truth | AMA-20 | Exchange Calendar·Session·DST·Holiday 모델 구축 | Done |  |
| M1 — Temporal & Reference Truth | AMA-19 | Canonical Instrument ID·Alias History·Point-in-Time Universe 구축 | Done |  |
| M1 — Temporal & Reference Truth | AMA-18 | Temporal 공격 테스트 — Future Sentinel·Revision·DST·Same-day Close | Done |  |
| M1 — Temporal & Reference Truth | AMA-16 | Observation 시간 계약·Vintage/Revision 모델 구축 | Done |  |
| M0 — Governance & Account Truth | AMA-13 | 포지션 원장·매도가능수량·Tax Lot 기초 구축 | Done |  |
| M0 — Governance & Account Truth | AMA-7 | Toss read-only 계좌 스냅샷·원본 응답 저장 | Done |  |
| M9 — Controlled Production Promotion | AMA-97 | Live Write Adapter Safety Review·Micro-live Policy 구축 | Backlog |  |
| M9 — Controlled Production Promotion | AMA-96 | Gate J — Semi-auto Acceptance | Backlog |  |
| M9 — Controlled Production Promotion | AMA-95 | Semi-auto Human Approval Workflow 구축 | Backlog |  |
| M9 — Controlled Production Promotion | AMA-94 | Gate I — Shadow Acceptance | Backlog |  |
| M8 — Individual Stock Expansion | AMA-89 | Sector·Factor Concentration·Single-name Risk Controls 구축 | Backlog |  |
| M8 — Individual Stock Expansion | AMA-86 | Analyst Revision·Estimate Dispersion Integration 구축 | Backlog |  |
| M8 — Individual Stock Expansion | AMA-85 | Earnings Quality·GAAP/Non-GAAP·Cash Conversion 구축 | Backlog |  |
| M8 — Individual Stock Expansion | AMA-84 | Point-in-Time Stock Universe·Eligibility·Liquidity Screen 구축 | Backlog |  |
| M7 — Operations & ETF Vertical Slice | AMA-79 | Security·Credential Separation·Two-key Live Activation 구축 | Backlog |  |
| M6 — Paper, Replay & Validation | AMA-67 | Parameter Registry·Lifecycle·Evidence Linking 구축 | Backlog |  |

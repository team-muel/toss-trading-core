# Toss Trading Research and Asset-Management Core

Point-in-time 연구와 자산관리 의사결정 기반을 설계·검증하는 저장소입니다. 실거래는
활성화되어 있지 않습니다.

핵심 원칙은 단순합니다.

- 공식 provider statement는 계좌·NAV·결제 상태의 기준원장이 될 수 있으나, 현재
  production accounting truth source는 아직 승인되지 않았습니다.
- 외부 데이터 피드는 Toss를 대체하지 않고 신호, 필터, 리스크 판단을 보강합니다.
- research, paper, shadow, live 의미론은 분리되며 어느 artifact도 자동 승격되지 않습니다.
- 보고서의 숫자는 시장 법칙이 아니라 starter guardrail입니다. 실제 체결, 슬리피지, 손실분포, 대사 품질이 쌓인 뒤에만 calibration합니다.

## Target Architecture

```text
external data feeds -> normalization -> feature/risk gates
                                      \
Toss Open API -> broker adapter -> account state -> ledger/reconciliation
                                                     \
canonical evidence -> governed calculation -> risk/portfolio gates
                                                       \
                                                replayable artifacts
```

## Documentation Map

| Document | Purpose |
| --- | --- |
| `docs/00_report_digest.md` | 모든 보고서에서 확정한 운영 원칙 요약 |
| `docs/01_architecture.md` | 시스템 경계, 계층, source-of-truth 구조 |
| `docs/03_data_contracts.md` | 내부 로그, 장부, 외부 피드 데이터 계약 |
| `docs/04_risk_operations.md` | kill switch, runbook, 운영 리스크 절차 |
| `docs/06_toss_gap_checklist.md` | Toss API 확인 항목과 남은 gap |
| `docs/07_toss_api_key_and_adapter.md` | Toss 키, 계좌 헤더, 어댑터 계약 |
| `docs/09_toss_official_api_coverage.md` | 공식 Toss API coverage 기준 문서 |
| `docs/10_calibration_policy.md` | 숫자/임계값 calibration 정책 |
| `docs/11_rate_limit_token_bucket.md` | Toss API group별 rate limit 설계 |
| `docs/12_external_data_feeds.md` | Massive, FRED, SEC, issuer parser 등 외부 연속 데이터 피드 정책 |
| `docs/19_research_data_and_backtest.md` | 불변 raw/Parquet 데이터 계층과 재현 가능한 momentum baseline |
| `docs/20_data_provider_selection_and_collection.md` | 실제 데이터 공급자 선정, 라이선스 게이트와 수집 결과 |
| `docs/22_visual_reporting.md` | 운영·데이터 품질·전략 성과 통합 시각 보고와 BigQuery 이력 |
| `docs/23_direction_and_readiness.md` | 고정 live 후보 범위, 권장 실행 순서, 증거 게이트와 No-Go 기준 |
| `docs/24_gmail_research_digest.md` | Vertex AI 근거 기반 연구 해석 메일, Gmail OAuth와 안전 대체 경로 |
| `docs/28_p1_research_data_completion.md` | P1 8개 작업의 구현·운영 증거·배포 경계 점검 |
| `docs/29_daily_autonomous_research_operation.md` | 일일 자율 연구, 데이터 전진 조건, 중복 메일 억제 운영 규칙 |
| `docs/33_broad_stock_recommendations.md` | 2,000~3,000개 미국 주식 스크리닝·추천·전향 추적 기준 |
| `docs/34_variant_perception_focused_research.md` | 시장 기대 역산과 driver-based 손익·현금흐름·증분 경제성·Earnings Quality·민감도를 강제하는 집중연구 기준 |

## Current Operating Snapshot — 2026-09-13

- Production accounting provider와 full account statement가 미승인 상태입니다.
  따라서 canonical production run, Gate D2 PASS, M5, live trading은 BLOCKED입니다.
- Governance authority evidence와 research data contracts are fail-closed: stale,
  incomplete, conflicting, or unverifiable evidence cannot enter a decision run.
- The retired Foundation/Paper service definitions are not deployment instructions;
  no external GCP service state is changed by this repository cleanup.

## Repository Layout

```text
config/
  default_policy.yaml
docs/
schemas/
  trading_ledger.sql
data/
  universe.csv
  instrument_master.csv
src/toss_trading/
  broker/
  data/
  research/
src/asset_management/ # canonical decision, evidence, risk, and portfolio core
src/alpha_management/ # canonical research expression language
```

## Current Development Boundary

1. Immutable governance, provider, model, and calculation lineage must be bound to
   one `runtime_run_id` and cutoff before a decision is eligible for replay.
2. A full provider-issued accounting statement must evidence reported NAV,
   settled/unsettled cash, receivable/payable, holdings valuation, FX, and
   inclusion semantics. In its absence, the production path fails closed.
3. Research continues as non-production work; it cannot enable an order path.

Research 데이터와 baseline은 broker 주문 경로와 분리되어 있습니다.

```powershell
python -m pip install -r requirements-research.lock
$env:PYTHONPATH='src'
python -m toss_trading.cli.research_ingest_bars --help
python -m toss_trading.cli.research_backtest --help
```

GCP 연구 데이터 자동화는 독립 실행 snapshot을 검증한 뒤에만 private
GCS로 올립니다. 이것은 production accounting 또는 execution authority가 아닙니다.

```bash
sudo systemctl start toss-research-automation@daily.service
sudo systemctl status toss-research-automation@daily.service --no-pager
```

다방향 자율 퀀트 연구의 계열, 참신성 검사, 공통 검증 기준은
[`docs/30_multi_direction_quant_research.md`](docs/30_multi_direction_quant_research.md)에 정리되어 있습니다.
Fast Expression DSL과 거래 세션 기준 delay/decay 의미론은
[`docs/32_fast_expression_history.md`](docs/32_fast_expression_history.md)에 정리되어 있습니다.
ALFRED 빈티지 기반의 과거 거시경제 레짐 백테스트와 미래정보 방지 규칙은
[`docs/31_alfred_point_in_time_macro_regime.md`](docs/31_alfred_point_in_time_macro_regime.md)에 보존되어 있습니다.
AMA-180 이후 이 직접 risk-on/defensive 배분 경로는 historical replay 전용이며,
현재 canonical macro-state 및 후속 레짐 연구는 `asset_management` evidence 경계를 사용합니다.

광범위 주식 연구는 ETF baseline과 분리합니다. 미국 보통주 약 2,500개를 고정된
유동성·momentum·저변동성·trend 규칙으로 평가해 집중연구 후보를 만들고, 시장
컨센서스 → 가격 내재 기대 → 자체 추정 → 격차 → 촉매를 모두 검증한 dossier가 있는
종목만 연구상 `buy` 추천이 될 수 있습니다. 공급자 credential과 이용약관 gate가
완료되기 전에는 비활성이며 어떤 결과도 주문 권한을 갖지 않습니다.

집중연구 결과는 Investment Thesis → Variant View → Earnings Model → Earnings Quality →
Supply-chain Read-through → Earnings Call Diff / Management Calibration → Positioning
Analysis → Valuation → Catalyst Path → Risk/Disconfirming Evidence → Position Construction
순서로 기록합니다.
Investment Conviction 점수는 추천이나 비중 산정에 사용하지 않고 마지막 요약에만
표시합니다. Earnings Model의 매출·EPS·OCF·FCF는 segment driver에서 계산합니다.
증분 경제성은 성장·유지 CAPEX, D&A, 운전자본, 인수와 기타 자본변화를 기초·기말
투하자본에 완전히 연결한 뒤 증분 매출·영업이익·NOPAT, 증분 ROIC, hurdle rate 대비
spread·경제적 이익과 성장 CAPEX 생산성을 계산합니다. 비교기간과 투자-성과 시차도
명시하며, 경제 driver 충격은 같은 모델로 재계산합니다.
Earnings Quality는 매출채권·재고·계약부채·이연매출, accruals·현금전환·운전자본,
SBC·구조조정·인수조정·세제효과·일회성 이익, GAAP/non-GAAP, D&A/CAPEX와 자사주 EPS
기여도를 별도 bridge로 검증합니다.
Supply-chain Read-through는 고객·공급업체·경쟁사의 수치 신호를 관계·전달 메커니즘·
시차와 연결하고, 서로 다른 외부 기업의 1차 출처 세 곳 이상이 같은 가설을 확인하는지
계산합니다. 최소 하나의 외부 반대 신호도 제거하지 않고 별도로 보존합니다.
Variant View의 Estimate Revision은 같은 point-in-time 데이터셋에서 20~45일 전과 현재의
FY1·FY2 EPS, revenue, EBITDA/FCF, 목표가 분포와 애널리스트 상향·하향 breadth를
재계산합니다. 실적 발표 직전·직후 revision도 별도로 보존하며, FY1 EPS와 주가가 반대
방향으로 움직이면 research-priority divergence로 표시하되 추천이나 비중을 자동 결정하지
않습니다.
Earnings Call Diff는 연속 두 분기의 issuer transcript를 같은 topic·질문·guidance ID로
비교해 새로 등장하거나 사라진 표현, horizon·confidence 변화, 질문 주제와 회피 여부,
수치 guidance 범위의 확대·축소를 계산합니다. 또한 동일 지표의 직전 8개 분기
guidance와 실제 결과 및 과거 약속의 이행 여부로 경영진의 보수성·공격성을 보정합니다.
Positioning Analysis는 13F 변화와 보고 시차, 13D/G, Form 4, 주요 주주 집중도,
passive·ETF exposure, short interest·days-to-cover·short-sale volume·borrow, IV term
structure·percentile·realized 대비 implied·25Δ skew·OI 집중·실적 expected move와 과거
IV crush를 각각 계산합니다. Reddit sentiment는 이 필수 계약에 포함하지 않습니다.

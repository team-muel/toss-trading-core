# Toss Trading Auto-Trade Foundation

Toss Invest Open API를 기준원장으로 사용해 USD 중심 현물 자동매매 시스템을 설계하고 검증하는 저장소입니다.

핵심 원칙은 단순합니다.

- Toss는 실행, 계좌, 보유, 주문 상태의 기준원장입니다.
- 외부 데이터 피드는 Toss를 대체하지 않고 신호, 필터, 리스크 판단을 보강합니다.
- live 후보 범위는 전용 계좌의 미국 상장 USD long-only ETF 현물 주문으로 제한합니다.
- 옵션, 숏/대차, 직접 T-bill ladder, 마진 기반 전략은 연구 또는 별도 브로커 모듈로 분리합니다.
- 보고서의 숫자는 시장 법칙이 아니라 starter guardrail입니다. 실제 체결, 슬리피지, 손실분포, 대사 품질이 쌓인 뒤에만 calibration합니다.

## Target Architecture

```text
external data feeds -> normalization -> feature/risk gates
                                      \
Toss Open API -> broker adapter -> account state -> ledger/reconciliation
                                                     \
portfolio/risk hub -> order planner -> paper/live adapter -> audit logs
                                                     \
                                             monitoring/kill switch
```

## Documentation Map

| Document | Purpose |
| --- | --- |
| `docs/00_report_digest.md` | 모든 보고서에서 확정한 운영 원칙 요약 |
| `docs/01_architecture.md` | 시스템 경계, 계층, source-of-truth 구조 |
| `docs/02_strategy_engines.md` | live 후보와 research-only 전략 엔진 범위 |
| `docs/03_data_contracts.md` | 내부 로그, 장부, 외부 피드 데이터 계약 |
| `docs/04_risk_operations.md` | kill switch, runbook, 운영 리스크 절차 |
| `docs/05_roadmap.md` | 구현 순서와 Go/No-Go 흐름 |
| `docs/06_toss_gap_checklist.md` | Toss API 확인 항목과 남은 gap |
| `docs/07_toss_api_key_and_adapter.md` | Toss 키, 계좌 헤더, 어댑터 계약 |
| `docs/08_account_state_reconciliation.md` | 계좌 상태 엔진과 대사 정책 |
| `docs/09_toss_official_api_coverage.md` | 공식 Toss API coverage 기준 문서 |
| `docs/10_calibration_policy.md` | 숫자/임계값 calibration 정책 |
| `docs/11_rate_limit_token_bucket.md` | Toss API group별 rate limit 설계 |
| `docs/12_external_data_feeds.md` | Massive, FRED, SEC, issuer parser 등 외부 연속 데이터 피드 정책 |
| `docs/13_foundation_v1_funded_read_only_validation.md` | 실제 현금, 보유, 수동 주문, 체결, 수수료, 결제일이 있는 계좌 read-only 검증 |
| `docs/14_gcp_static_ip_runner.md` | GCP 고정 IP VM, Secret Manager, JSONL runner 운영 절차 |
| `docs/15_gcp_secret_manager_and_runtime.md` | GCP Secret Manager, VM 환경변수, runtime DB/log 경로 |
| `docs/16_cloud_monitoring_runner_health.md` | Cloud Logging/Monitoring 기반 runner health 기준 |
| `docs/17_remediation_and_live_gate.md` | 실제 GCP 배포·보안 조치 현황과 다음 live gate 우선순위 |
| `docs/18_alpha_expression_language.md` | research-only alpha 작성·채점 부록과 foundation 용어 매핑 |
| `docs/19_research_data_and_backtest.md` | 불변 raw/Parquet 데이터 계층과 재현 가능한 momentum baseline |
| `docs/20_data_provider_selection_and_collection.md` | 실제 데이터 공급자 선정, 라이선스 게이트와 수집 결과 |
| `docs/21_gcp_research_data_automation.md` | GCP 기반 daily/weekly 수집·QA·백업·자체검증 자동화 |
| `docs/22_visual_reporting.md` | 운영·데이터 품질·전략 성과 통합 시각 보고와 BigQuery 이력 |
| `docs/23_direction_and_readiness.md` | 고정 live 후보 범위, 권장 실행 순서, 증거 게이트와 No-Go 기준 |
| `docs/24_gmail_research_digest.md` | Vertex AI 근거 기반 연구 해석 메일, Gmail OAuth와 안전 대체 경로 |
| `docs/27_p0_identity_and_holdout_remediation.md` | P0 전향 표본 봉인·복구 증거와 research 전용 identity 전환 게이트 |
| `docs/28_p1_research_data_completion.md` | P1 8개 작업의 구현·운영 증거·배포 경계 점검 |
| `docs/29_daily_autonomous_research_operation.md` | 일일 자율 연구, 데이터 전진 조건, 중복 메일 억제 운영 규칙 |
| `docs/32_three_axis_reinforcement.md` | 활성 release 증거, 연구 신원 격리, persistent paper 운영 기준 |
| `docs/33_broad_stock_recommendations.md` | 2,000~3,000개 미국 주식 스크리닝·추천·전향 추적 기준 |
| `docs/34_variant_perception_focused_research.md` | 시장 컨센서스·가격 내재 기대·자체 추정·격차·촉매를 강제하는 집중연구 기준 |

## Current Operating Snapshot — 2026-08-08

- P1 이전 GCP research runtime 기준 release는 `36a60066a485`였습니다. 현재 활성
  revision은 VM의 content-addressed `current` symlink와 gold의 `code_revision`이
  일치하는지로 확인하며 자동 주문은 모든 정책에서 비활성입니다.
- `toss-foundation.timer`는 `OnUnitActiveSec=6h`, Foundation 경보 6개,
  research 경보 5개를 유지합니다.
- research daily/weekly timer, Ops Agent, private GCS, BigQuery 이력과 통합
  Cloud Monitoring dashboard가 운영 중입니다.
- 과거 `SPLG` 404는 2025-10-31 공식 ticker 변경을 반영하지 못한 매핑 문제로
  확정했습니다. 공식 유니버스는 `SPYM`, Toss 요청은 `SPYM`, Tiingo 연속 이력
  요청은 공급자 별칭 `SPLG`로 분리했으며 Toss `SPYM` read-only 조회는 HTTP 200으로
  검증했습니다.
- Toss 자료는 `raw`와 `split_adjusted/estimated`까지만 전략과 격리해
  보관합니다. Tiingo total-return과 FRED 승인 series는 활성화됐고, baseline의
  prospective 시작일은 2026-08-03입니다. 8월 8일 첫 daily 실행의 Tiingo
  timeout은 같은 날 복구 실행으로 회복했으며 성과는 126거래일 전까지 봉인합니다.
- OpenAPI 승인 계약은 1.2.13이며 조건주문 경로는 계약에만 등록하고 모든 쓰기
  capability는 비활성화했습니다.

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
  account/
  broker/
  data/
  engines/
  execution/
  ledger/
  monitoring/
  research/
  risk/
  alpha/            # research-only alpha authoring (operators, metrics, datafields)
    datafields/
```

## First Milestone

1. Toss OAuth2, `accountSeq`, holdings, orders, buying power, sellable quantity 호출을 검증합니다.
2. `data/universe.csv`와 `instrument_master`를 먼저 맞춥니다.
3. `raw_api_response`, 내부 ledger, reconciliation을 구현합니다.
4. paper 모드에서 주문 상태, 부분체결, 수수료, 세금, 결제예정일 반영을 검증합니다.
5. 외부 피드는 Tiingo EOD total-return, FRED/ALFRED, SEC EDGAR 순서로
   활성화하고 Massive·WebSocket·issuer parser는 전략 가설이 요구할 때
   확장합니다.
6. live는 shadow live 2주 이후 초소형 현물 주문만 허용합니다.

Foundation snapshot 검증 명령:

```powershell
$env:PYTHONPATH='src'
python -m toss_trading.cli.foundation_snapshot
python -m toss_trading.cli.foundation_audit
```

Foundation v1 funded read-only 검증:

```powershell
$env:PYTHONPATH='src'
python -m toss_trading.cli.foundation_snapshot --target-order-id "<OPEN 상태에서 확보한 orderId>"
python -m toss_trading.cli.foundation_audit --profile v1-funded-read-only
```

대상 주문은 토스 앱에서 직접 제출하고 실제로 소량 체결된 주문이어야 합니다.
취소만 된 주문은 체결량, 수수료, 결제일 증거가 없어 v1을 통과하지 못합니다.

저장된 raw broker 응답의 독립 replay 검증:

```powershell
$env:PYTHONPATH='src'
python -m toss_trading.cli.foundation_replay `
  --source-db "<복원한 Foundation SQLite>" `
  --destination-db "<존재하지 않는 새 SQLite 경로>"
python -m toss_trading.cli.foundation_audit `
  --db "<새 SQLite 경로>" `
  --profile v1-funded-read-only
```

Replay는 원본 또는 운영 DB를 덮어쓰지 않으며, 저장된 응답 hash가 다르면
즉시 실패합니다. 계좌 식별자는 raw 응답에서 계속 마스킹하고
`snapshot_run.account_seq`의 내부 대사 키만 복원합니다.

GCP VM runner:

```bash
export FOUNDATION_LOAD_GCP_SECRETS=1
export FOUNDATION_AUDIT_PROFILE=v0-empty-safe
./scripts/run_foundation_gcp.sh
```

Research 데이터와 baseline은 broker 주문 경로와 분리되어 있습니다.

```powershell
python -m pip install -r requirements-research.lock
$env:PYTHONPATH='src'
python -m toss_trading.cli.research_ingest_bars --help
python -m toss_trading.cli.research_backtest --help
```

GCP 연구 데이터 자동화는 독립 실행 snapshot을 검증한 뒤에만 private
GCS로 올립니다. 기존 6시간 Foundation timer와 경보 6개는 그대로
유지합니다.

```bash
sudo systemctl start toss-research-automation@daily.service
sudo systemctl status toss-research-automation@daily.service --no-pager
```

다방향 자율 퀀트 연구의 계열, 참신성 검사, 공통 검증 기준은
[`docs/30_multi_direction_quant_research.md`](docs/30_multi_direction_quant_research.md)에 정리되어 있습니다.
ALFRED 빈티지 기반 거시경제 레짐 연구와 미래정보 방지 규칙은
[`docs/31_alfred_point_in_time_macro_regime.md`](docs/31_alfred_point_in_time_macro_regime.md)에 정리되어 있습니다.

광범위 주식 연구는 ETF baseline과 분리합니다. 미국 보통주 약 2,500개를 고정된
유동성·momentum·저변동성·trend 규칙으로 평가해 집중연구 후보를 만들고, 시장
컨센서스 → 가격 내재 기대 → 자체 추정 → 격차 → 촉매를 모두 검증한 dossier가 있는
종목만 연구상 `buy` 추천이 될 수 있습니다. 공급자 credential과 이용약관 gate가
완료되기 전에는 비활성이며 어떤 결과도 주문 권한을 갖지 않습니다.

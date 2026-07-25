# Toss Trading Auto-Trade Foundation

Toss Invest Open API를 기준원장으로 사용해 USD 중심 현물 자동매매 시스템을 설계하고 검증하는 저장소입니다.

핵심 원칙은 단순합니다.

- Toss는 실행, 계좌, 보유, 주문 상태의 기준원장입니다.
- 외부 데이터 피드는 Toss를 대체하지 않고 신호, 필터, 리스크 판단을 보강합니다.
- live MVP는 국내/미국 주식 및 ETF 현물 주문으로 제한합니다.
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
5. 외부 피드는 Massive REST, FRED, SEC EDGAR 순서로 붙이고 WebSocket과 issuer parser는 뒤로 둡니다.
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

# Toss Trading Auto-Trade Foundation

이 저장소는 Toss Invest Open API를 기반으로 USD 기준 자동매매 시스템을 설계하고 검증하기 위한 기본 구조입니다.

공식 문서 확인 후 전제를 수정했습니다.

- Toss Open API는 OAuth2 기반 REST API입니다.
- 국내/미국 주식의 시세, 종목정보, 환율, 장 운영 시간, 계좌, 보유주식, 주문 생성/정정/취소/조회, buying power, sellable quantity, commissions를 제공합니다.
- 계좌/자산/주문 API는 `Authorization: Bearer {access_token}` 외에 `X-Tossinvest-Account: {accountSeq}` 헤더가 필요합니다.
- 공식 문서 기준으로 해외 옵션, 옵션체인, Greeks, 채권/T-bill 직접 보유, margin breakdown, borrow rate, webhook/stream, tax lot/cashflow 이벤트는 제공 범위에 없습니다.
- 따라서 Toss 단독 live MVP는 국내/미국 주식·ETF 현물 자동매매로 제한합니다.

## Target Architecture

```text
Toss Open API + external data -> account state/reconciliation -> signal engines
                                                    |
                                                    v
portfolio/risk hub -> order planner -> paper/live adapter -> audit ledger
                                                    |
                                                    v
                                            alerts/kill switch
```

## Repository Layout

```text
config/
  default_policy.yaml
docs/
  00_report_digest.md
  01_architecture.md
  02_strategy_engines.md
  03_data_contracts.md
  04_risk_operations.md
  05_roadmap.md
  06_toss_gap_checklist.md
  07_toss_api_key_and_adapter.md
  08_account_state_reconciliation.md
  09_toss_official_api_coverage.md
schemas/
  trading_ledger.sql
src/toss_trading/
  account/
  broker/
  data/
  engines/
  execution/
  ledger/
  monitoring/
  risk/
```

## First Milestone

1. `docs/09_toss_official_api_coverage.md`로 공식 API 제공 범위와 미지원 범위를 확인합니다.
2. `docs/07_toss_api_key_and_adapter.md` 기준으로 OAuth2 client credentials와 accountSeq를 설정합니다.
3. `docs/08_account_state_reconciliation.md` 기준으로 계좌 상태 엔진과 내부 장부 대사를 먼저 구현합니다.
4. `paper` 모드에서 주문 계획, 주문 상태 폴링, 체결 합계 반영, reconciliation을 검증합니다.
5. 초소형 현물 주문만 live 후보로 올리고, 옵션/T-bill/마진/대차 전략은 Toss 단독 live 범위 밖으로 둡니다.

실거래 전 세금, 브로커 약관, 주문 가능 상품, 장 운영 시간, high-value order 확인, rate limit을 별도로 확인해야 합니다.

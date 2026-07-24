# Research Data And Backtest Foundation

## 목적

이 계층은 Toss 계좌·주문 SQLite와 분리된 읽기 전용 연구 저장소입니다.
데이터 양 자체가 아니라 point-in-time 정확성, 재현성, 비용후 전략 검증을
목표로 합니다. 연구 결과는 broker 계좌 상태를 덮어쓰지 않으며 주문을
제출하지 않습니다.

## 저장 계층

기본 로컬 경로는 `research_data/`이고 Git에서 제외합니다. 운영에서는 같은
상대 경로를 private GCS bucket에 보존합니다.

```text
research_data/
  bronze/source=<provider>/dataset=<dataset>/ingest_date=<date>/
  silver/market_bars/source=<provider>/interval=<interval>/
    adjustment=<type>/year=<year>/
  gold/experiments/
  catalog/manifests/
```

- `bronze`: provider 원본을 SHA-256 이름으로 불변 저장
- `silver`: 품질검사를 통과한 ZSTD Parquet
- `gold`: 전략 실험 기록과 이후 feature/label 데이터셋
- `catalog`: source, availability, license, schema, code revision, parent
  manifest를 기록한 불변 JSON manifest

SQLite Foundation DB는 계좌·주문·체결 증거 전용으로 유지합니다. 대량
시계열을 SQLite에 계속 적재하지 않습니다.

## Market Bar 계약

필수 필드:

- `symbol`
- `event_time_utc`
- `available_at`
- `exchange_local_date`
- `interval`: `1d`, `1h`, `1m`
- `open`, `high`, `low`, `close`, `volume`
- `currency`, `session`
- `adjustment`: `raw`, `split_adjusted`, `total_return`
- `source_revision`

중복 key는 `(symbol, event_time_utc, interval, source, adjustment)`입니다.
OHLC 범위, 양수 가격, 음수가 아닌 거래량, 시간 순서, `available_at`을
수집 단계에서 검사합니다.

## CSV 수집

provider 라이선스에 따라 내보낸 CSV를 원본과 Parquet으로 함께 저장합니다.

```powershell
python -m pip install -r requirements-research.lock
$env:PYTHONPATH='src'
python -m toss_trading.cli.research_ingest_bars `
  --input "<provider-export.csv>" `
  --source "<approved-provider>" `
  --license-tag "<license-or-contract-id>" `
  --available-at "2026-07-23T00:00:00+00:00" `
  --code-revision "<git-sha>"
```

실제 provider adapter를 구현할 때도 먼저 raw object를 저장한 뒤 동일한
`MarketBar` 계약으로 정규화합니다. API key, 토큰, 계좌 식별자는 manifest나
원본 경로에 넣지 않습니다.

## 첫 사전등록 Baseline

`broad_etf_dual_momentum_v1`의 기본 규칙:

- total-return 일봉만 사용
- 252 거래일 lookback
- 최근 21 거래일 skip
- 월말 신호, 다음 거래일 적용
- 절대 모멘텀이 0보다 큰 후보 중 상위 1개
- 통과 후보가 없으면 `SGOV`
- 왕복 최적화를 하지 않고 commission 1.5 bps, slippage 2.0 bps를 각
  rebalance turnover에 적용

```powershell
python -m toss_trading.cli.research_backtest `
  --parquet "<silver market-bars parquet>" `
  --candidate SPY --candidate QQQ --candidate VTV `
  --candidate XLP --candidate XLU --candidate TLT --candidate GLD `
  --cash-symbol SGOV `
  --data-manifest-id "<manifest-id>" `
  --code-revision "<git-sha>"
```

결과에는 config, 입력 manifest IDs, code revision, benchmark 이름, equity
curve, rebalance, turnover, CAGR, 변동성, Sharpe, max drawdown, Calmar가
불변 experiment JSON으로 저장됩니다.

## 승격 기준

현재 baseline은 수익성이 입증된 전략이 아닙니다. 다음을 모두 통과하기
전에는 paper/shadow로 승격하지 않습니다.

1. 실제 ETF 상장일·ticker 변경·corporate action을 반영한 point-in-time
   universe
2. train/development/OOS 분리와 walk-forward
3. SPY buy-and-hold, 동일가중, 60/40, 현금성 benchmark 비교
4. 수수료·세금·slippage 2배 stress
5. 시기별 regime와 파라미터 주변 구간의 안정성
6. 한 시기·한 종목이 성과 대부분을 만드는지 확인
7. 이후 현실적인 persistent paper engine과 2주 shadow

## 다음 데이터 우선순위

1. 승인된 공급자의 장기 raw/adjusted/total-return ETF 일봉
2. 배당·분할·상장일·폐지·ticker 변경
3. 거래 캘린더와 benchmark
4. FRED/ALFRED 무위험금리
5. USD/KRW 세금 보고용 환율

옵션, 실시간 WebSocket, 뉴스, SEC/issuer parser는 승인된 전략 가설이
필요로 할 때 추가합니다.

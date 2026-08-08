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

원본 manifest의 `retrieved_at`은 실제 다운로드 시각입니다. 정규화된 과거
일봉의 `available_at`은 거래소 현지 20:00의 보수적 장후 추정치이고,
`source_revision`은 어떤 retrieval snapshot에서 생성됐는지 보존합니다.
두 시각을 같은 의미로 사용하지 않습니다.

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
- Foundation의 sanitized 현재 미국 수수료 schedule과 protocol에 사전 선언된
  주문금액별 slippage tier를 매도·매수 양쪽 체결금액에 적용합니다. calibration이
  없거나 만료되면 실행하지 않습니다.

```powershell
python -m toss_trading.cli.research_backtest `
  --parquet "<silver market-bars parquet>" `
  --candidate SPY --candidate QQQ --candidate VTV `
  --candidate XLP --candidate XLU --candidate TLT --candidate GLD `
  --cash-symbol SGOV `
  --cost-calibration "<sanitized calibration json>" `
  --manifest-root research_data/catalog/manifests `
  --validation-protocol config/research_validation_protocol.json `
  --code-revision "<git-sha>"
```

결과에는 config, 입력 manifest IDs, code revision, benchmark 이름과 실제
benchmark metrics, equity
curve, rebalance, turnover, CAGR, 변동성, Sharpe, max drawdown, Calmar가
불변 experiment JSON으로 저장됩니다.

기본 walk-forward는 504 거래일의 선행 context 뒤에 126 거래일 창을 순차
평가하지만, 이는 역사적 진단이며 승격용 OOS가 아닙니다. 승격용 지표는
`config/research_validation_protocol.json`에 2026-07-31 사전등록한 별도
prospective 구간만 사용합니다.

- prospective 시작: 2026-08-03
- 최소 표본: 126 거래일
- 최소 표본 전: 부분 성과와 benchmark 성과를 공개하지 않음
- 최소 표본 후: 처음 126 거래일을 한 번만 headline으로 공개
- headline 비교: 동일 날짜의 SPY buy-and-hold
- 실험 lineage: DuckDB가 실제로 읽은 Parquet 파일에 대응하는 silver
  manifest ID만 포함
- 수집 lineage: Tiingo 성공 run의 공통 최신 거래일을 append-only
  `prospective_collection_observations.jsonl`에 기록
- 전체 QA·백업·보고 작업이 성공한 run의 completion marker가 있을 때만 해당
  수집 evidence를 인정
- 허용 지연: 거래일 뒤 3 calendar day 이내. 더 늦은 백필은
  `invalid_data_gap`으로 차단

`full_sample_metrics`라는 호환 필드는 prospective 시작 전 역사 구간으로 고정한
진단값만 보존합니다. 표본 수집 중에는 prospective 시작 이후의 지표, benchmark,
equity curve, rebalance, walk-forward 상세를 산출물에서 제거하고 `metrics`를
`null`로 유지합니다. 처음 126개의 연속성 검증 거래일이 완성돼야 해당 구간만
공개합니다.

## 승격 기준

P1부터 walk-forward 통과 fold는 수익률이 0보다 큰지가 아니라 동일 기간의
`SPY buy-and-hold` 총수익률을 초과했는지로 판정한다. 수수료는 Foundation의 현재
미국 계좌 schedule을 percent에서 bps로 정규화하고, slippage는 포트폴리오 notional과
각 매수·매도 leg 규모에 따른 보수적 tier를 적용한다. 만료되거나 누락된 calibration은
weekly 후보평가와 baseline 생성을 중단시킨다.

현재 baseline은 수익성이 입증된 전략이 아닙니다. 다음을 모두 통과하기
전에는 paper/shadow로 승격하지 않습니다.

1. 실제 ETF 상장일·ticker 변경·corporate action을 반영한 point-in-time
   universe
2. 사전등록 prospective OOS 126거래일 완료
3. SPY buy-and-hold, 동일가중, 60/40, 현금성 benchmark 비교
4. 수수료·세금·slippage 2배 stress
5. 시기별 regime와 파라미터 주변 구간의 안정성
6. 한 시기·한 종목이 성과 대부분을 만드는지 확인
7. 이후 현실적인 persistent paper engine과 2주 shadow

## AI 후보 연구

고정 baseline과 별도로 AI가 정책 제한형 후보를 제안할 수 있다. AI는 가격과
성과를 보지 않은 상태에서만 가설을 제안하며, 실행 가능한 코드를 만들지 않는다.
후보는 누적 등록 수를 반영한 Bonferroni 보정, block bootstrap, walk-forward,
비용 2배 stress를 통과해야 전향 관찰 protocol이 생성된다.

새 AI 후보의 전향 기준은 기존 baseline의 126일과 다르다. 최소 252거래일과
12회 리밸런싱을 모두 요구하고, 완료 전 성과는 숨긴다. 통과 뒤에도 paper와
shadow 실제 체결 증거가 없으면 승격하지 않는다. 상세 규약은
`docs/26_autonomous_research_governance.md`를 따른다.

## 다음 데이터 우선순위

1. 승인된 공급자의 장기 raw/adjusted/total-return ETF 일봉
2. 배당·분할·상장일·폐지·ticker 변경
3. 거래 캘린더와 benchmark
4. FRED/ALFRED 무위험금리
5. USD/KRW 세금 보고용 환율

옵션, 실시간 WebSocket, 뉴스, SEC/issuer parser는 승인된 전략 가설이
필요로 할 때 추가합니다.

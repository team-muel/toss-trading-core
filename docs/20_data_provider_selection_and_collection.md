# 데이터 공급자 선정 및 과거 데이터 수집

## 결정

2026-07-24 기준 개인·비공개 연구 범위에서 다음 조합을 채택한다.

| 역할 | 공급자 | 상태 | 핵심 이유 |
|---|---|---|---|
| 미국 ETF 일봉·총수익률 | Tiingo EOD | 계정·토큰 대기 | raw와 배당·분할 반영 adjusted OHLCV를 함께 제공 |
| 부트스트랩·교차 검증 | Toss Open API candles | 수집 가능 | 이미 승인된 고정 IP와 Secret Manager 자격증명을 사용 |
| 거시경제 point-in-time | FRED/ALFRED | 키·series 권리 검토 대기 | vintage/realtime period로 발표 당시 관측치를 재현 가능 |
| CIK·공시 이벤트 | SEC EDGAR | 수집 가능 | 인증 없는 공식 JSON과 nightly bulk 파일 제공 |
| 옵션·기업행동 보강 | Massive | 후순위 | 일봉 aggregate가 배당 조정되지 않아 총수익률 기준선에는 부적합 |

상세한 허용 범위, 금지 범위, URL과 검토된 호출 한도는
`config/data_sources.yaml`을 단일 기준으로 사용한다. 원본 또는 파생 공급자
데이터를 Git, 공개 저장소, 보고서 첨부 파일에 넣지 않는다.

## 조정 가격 정책

- Tiingo `adjOpen`, `adjHigh`, `adjLow`, `adjClose`, `adjVolume`만
  `adjustment=total_return`으로 정규화한다.
- Tiingo 비조정 필드는 `adjustment=raw`로 함께 보관한다.
- Toss `adjusted=false`는 `raw`로 보관한다.
- Toss `adjusted=true`는 현재 `split_adjusted`, `quality_flag=estimated`로
  격리한다. 배당까지 반영한다는 계약 근거가 확인되기 전에는
  `total_return`으로 승격하지 않는다.
- 전략 기준선은 `total_return` 파티션만 읽어야 한다. 이 규칙 때문에 현재
  Toss 자료만으로 수익성 결론을 내릴 수 없다.

## 자격증명 및 계약 게이트

Tiingo와 FRED는 사용자가 각 서비스에서 계정을 만들고 약관을 수락해야 한다.
Codex가 사용자를 대신해 약관을 수락하지 않는다.

필요한 Secret Manager 이름:

- `tiingo-api-token` → 런타임 환경 `TIINGO_API_TOKEN`
- `fred-api-key` → 런타임 환경 `FRED_API_KEY`

Tiingo Starter의 `Internal Use Only`는 개인 내부 연구만 허용하는 선택이다.
팀 공유, 웹 표시, 재배포가 필요해지면 수집을 중단하고 별도 상용 라이선스를
검토한다. FRED는 series마다 제3자 저작권이 다를 수 있으므로 series registry에
출처와 허용 조건을 기록한 후 활성화한다.

## 수집 명령

### Toss: 고정 IP VM에서 원본 bundle 수집

이 명령은 OAuth와 `GET /api/v1/candles`만 사용하며 주문 API를 호출하지 않는다.
운영 서비스와 다른 임시 작업 경로에서 실행한다.

```bash
export PYTHONPATH=src
python -m toss_trading.cli.research_collect_toss collect \
  --universe data/universe.csv \
  --start-date 2004-01-01 \
  --skip-unavailable-symbols \
  --raw \
  --output toss-candles-raw.json

python -m toss_trading.cli.research_collect_toss collect \
  --universe data/universe.csv \
  --start-date 2004-01-01 \
  --skip-unavailable-symbols \
  --adjusted \
  --output toss-candles-adjusted.json
```

bundle을 승인된 연구 저장소로 복사한 후 로컬에서 다음과 같이 원본과 Parquet를
만든다.

```powershell
$env:PYTHONPATH='src'
python -m toss_trading.cli.research_collect_toss ingest `
  --input toss-candles-raw.json `
  --output-root research_data `
  --through-date '<last-completed-US-session>' `
  --code-revision '<git-sha>'
```

### Tiingo: 계약 수락 및 토큰 등록 후

```powershell
$env:PYTHONPATH='src'
python -m toss_trading.cli.research_collect_tiingo `
  --universe data/universe.csv `
  --start-date 2004-01-01 `
  --output-root research_data `
  --code-revision '<git-sha>'
```

토큰은 인자나 파일로 전달하지 않고 `TIINGO_API_TOKEN` 환경 변수로만 읽는다.

### SEC EDGAR

```powershell
$env:PYTHONPATH='src'
$env:SEC_USER_AGENT='toss-trading-core research <approved-contact>'
python -m toss_trading.cli.research_collect_sec `
  --instrument-master data/instrument_master.csv `
  --output-root research_data `
  --code-revision '<git-sha>'
```

수집기는 SEC 제한보다 보수적인 간격으로 요청하며, ticker map과 중복 제거된
issuer CIK submission 원본을 bronze에 보관한다.

## 품질 및 재현성 확인

각 수집 실행에서 다음을 확인한다.

1. bronze 객체의 SHA-256과 request hash가 manifest에 있다.
2. request metadata와 로그에 토큰·client secret이 없다.
3. 동일 원본은 동일 manifest ID를 만들고 다른 바이트로 덮어쓰지 않는다.
4. OHLC 범위, 양수 가격, 음수가 아닌 거래량, 중복 키, 시간 순서를 검증한다.
5. 원본 manifest ID가 모든 silver market bar에 연결된다.
6. Toss raw/adjusted와 Tiingo raw의 겹치는 날짜를 비교해 이상치를 보고한다.
7. Tiingo total-return만 baseline에 투입하고 source revision과 license tag를
   실험 기록에 고정한다.

정규화 후 자동 검사는 다음 명령으로 실행한다.

```powershell
python -m toss_trading.cli.research_validate_bars `
  --parquet 'research_data/silver/market_bars/source=toss-openapi/**/*.parquet' `
  --require-adjustment raw `
  --require-adjustment split_adjusted
```

## 아직 해결되지 않은 데이터 문제

- `data/instrument_master.csv`의 `effective_from=2026-01-01`은 실제 상장·ticker
  이력이 아니라 초기 placeholder다. SEC와 issuer 자료로 실제 효력 구간을
  구축하기 전에는 point-in-time universe라고 부르면 안 된다.
- ETF 분배금, split, ticker 변경과 상장 폐지 이력을 별도 corporate-action
  테이블로 정규화해야 한다.
- 거래소 캘린더, benchmark, USD/KRW 환율, 세금·수수료 시계열을 추가해야
  실제 순수익 비교가 가능하다.
- FRED/ALFRED는 API 키와 series별 권리 registry가 준비될 때까지 수집하지 않는다.

## 2026-07-24 실제 수집 결과

승인된 고정 IP VM에서 운영 서비스와 분리된 임시 경로를 사용했다.
`toss-foundation.timer`, 현재 release symlink와 Cloud Monitoring 경보는 변경하지
않았다.

- 요청 universe: 15개 ETF
- 수집 성공: 14개
- 공급자 미지원: `SPLG` (`404 stock-not-found`)
- 원본 page: raw 347개, adjusted 347개
- bundle SHA-256:
  - raw:
    `738b36fc6da9c33c943b221a68962bbb628de98f012e3f23bbc09abad5e4bb7f`
  - adjusted:
    `5c252c49e4748643a1b527592fb68f50bdf9ac3b0c08ed0f40d4777f2b4ab512`
- 확정 세션 cutoff: `2026-07-23`
- 정규화 결과: raw 67,794행, split-adjusted estimated 67,794행
- 전체 QA: 중복 0행, OHLC/시간 오류 0행, adjustment key 불일치 0행
- manifest code revision:
  `a14483bba769ee198d5b2205eb3579f4f03b3e09`

현재 coverage:

| symbol | raw rows | first date | last date |
|---|---:|---|---|
| BIL | 4,818 | 2007-05-30 | 2026-07-23 |
| GLD | 5,452 | 2004-11-18 | 2026-07-23 |
| IVV | 5,674 | 2004-01-02 | 2026-07-23 |
| QQQ | 5,674 | 2004-01-02 | 2026-07-23 |
| SCHD | 3,709 | 2011-10-20 | 2026-07-23 |
| SGOV | 1,546 | 2020-05-28 | 2026-07-23 |
| SHV | 4,913 | 2007-01-11 | 2026-07-23 |
| SMH | 3,666 | 2011-12-21 | 2026-07-23 |
| SPY | 5,674 | 2004-01-02 | 2026-07-23 |
| TLT | 5,674 | 2004-01-02 | 2026-07-23 |
| VOO | 3,991 | 2010-09-09 | 2026-07-23 |
| VTV | 5,655 | 2004-01-30 | 2026-07-23 |
| XLP | 5,674 | 2004-01-02 | 2026-07-23 |
| XLU | 5,674 | 2004-01-02 | 2026-07-23 |

Toss가 뉴욕 장 시작 전에도 당일 일봉을 반환하는 것을 확인했으므로 수집 시각의
당일 행은 bronze에만 보존하고 silver에서는 제외했다. `SMH` coverage가
2011-12-21부터 시작하는 이유와 `SPLG`의 Toss vendor symbol은 별도 매핑 검증
대상이다.

Tiingo는 주 공급자로 선정했지만 계정 약관 수락과
`TIINGO_API_TOKEN` 등록 전이므로 아직 실제 raw/total-return 수집을 시작하지
않았다. SEC는 개인 연락처를 외부 전송하지 않는 URL형 User-Agent로 시도했을 때
403이어서, 사용자가 SEC에 보낼 연락 이메일을 명시적으로 승인하기 전까지
중단했다.

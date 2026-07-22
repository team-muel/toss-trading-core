# Alpha Expression Language (Research Appendix)

이 문서는 새로운 정책을 만들지 않습니다. 기존 foundation 위에서 **research 신호를 어떻게
쓰고 채점하는지**를 위한 부록입니다. 용어는 fast-expression alpha 연구 플랫폼(예: WorldQuant
BRAIN) 관례를 따르되, 산출물은 이 저장소의 기존 `Signal`과 `RiskHub`에 그대로 들어갑니다.

핵심 불변식은 그대로입니다.

- 이 계층은 **research-only**입니다. 주문을 내지 않습니다.
- alpha가 만든 position은 `toss_trading.engines.Signal`로 변환되어 기존 `RiskHub`,
  kill switch, reconciliation, `live_trading_enabled` gate를 **모두** 통과해야 합니다.
- 외부/파생 데이터는 신호 입력일 뿐이며 Toss 계좌 상태를 덮어쓰지 않습니다.

## 개념 매핑 (foundation 용어 ↔ alpha 용어)

| Foundation 용어 | 이 부록의 alpha 용어 | 위치 |
| --- | --- | --- |
| `Signal.raw_score` | raw alpha value | `alpha/expression.py` |
| `Signal.adjusted_score` | neutralized alpha value | `operators.group_neutralize` |
| `Signal.target_weight` | position weight (post-truncation) | `operators.truncate` |
| Engine Matrix (`docs/02`) | alpha expression 카탈로그 | `alpha/__init__.py` |
| RiskHub 게이트 | simulation constraints | `SimulationSettings` |
| ALLOW/REDUCE/BLOCK | discretized alpha → BUY/SELL/drop | `to_signals` |
| (기존에 없던 것) | operator library | `alpha/operators.py` |
| (기존에 없던 것) | 성과지표: Sharpe·turnover·fitness·IS/OS | `alpha/metrics.py` |
| 외부 피드(`docs/12`) | datafield | `alpha/datafields/` |

이 표가 "지문"입니다. 기존 용어를 지우지 않고, 그 옆에 alias를 붙여 alpha 언어를 공용어로
얹습니다.

## Operator Library

`alpha/operators.py`는 순수 파이썬(stdlib only)입니다. numpy/pandas 의존성을 추가하지
않습니다.

Cross-sectional (`Mapping[str, float]` → `dict[str, float]`):
`rank`, `zscore`, `scale`, `sign`, `winsorize`, `group_neutralize`, `group_rank`,
`truncate`.

Time-series (`Sequence[float]` → `list[float | None]`, warm-up은 `None`):
`ts_delay`, `ts_delta`, `ts_sum`, `ts_mean`, `ts_stddev`, `ts_zscore`, `ts_rank`,
`ts_decay_linear`, `ts_max`, `ts_min`.

## Simulation Settings

`SimulationSettings`는 BRAIN alpha의 설정과 대응합니다.

| 필드 | 의미 | foundation 대응 |
| --- | --- | --- |
| `region` | 시장 지역 (기본 `KOR`) | universe 메타데이터 |
| `universe` | 거래 후보 집합 | `data/universe.csv` |
| `delay` | 신호→체결 지연 (기본 1) | REST polling 지연 |
| `decay` | 신호 평활 창 | (연구용) |
| `neutralization` | `none` / `market` / `group` | risk neutralize |
| `truncation` | 단일 종목 최대 비중 | starter guardrail |
| `book_size` | 총 배분 규모 | portfolio NAV |
| `long_only` | 숏 금지 (기본 True) | live scope: 현물 long-only |
| `stop_loss_frac` | `expected_max_loss` 산정용 | RiskHub 단일손실 gate |

`truncation`은 water-filling으로 적용됩니다. 활성 종목이 적어
`n_active * truncation < 1`이면 상한을 어기는 대신 **의도적으로 under-invest**합니다.

## Metrics

`alpha/metrics.py`가 이 저장소에 없던 **평가 어휘**를 채웁니다.

- `sharpe` = `sqrt(252) * mean(pnl) / std(pnl)`
- `annualized_returns` = `mean(pnl) / book_size * 252`
- `turnover` = 기간별 `sum_i |w_t - w_{t-1}|` 의 평균 / 평균 gross book
- `max_drawdown` = 누적 PnL 곡선의 최대 낙폭 (≥ 0)
- `fitness` = `sharpe * sqrt(|returns| / max(turnover, 0.125))` (BRAIN fitness, 0.125 floor)
- `is_os_split` = walk-forward in-sample / out-of-sample 분할

`is_os_split`은 `greenfield_trading`의 walk-forward 규율과 같은 취지입니다. alpha는
in-sample 적합만으로 채택하지 않고 out-of-sample 확인을 요구합니다.

## First Datafield — Naver Search HUB (KOR)

`docs/12`의 외부 스택(Massive·FRED·SEC)은 전부 미국 시장 전용입니다. 그래서 KOSPI/KOSDAQ
종목은 Toss 시세 외에 이벤트·심리·관심도 피드가 없습니다. `alpha/datafields/naver_sentiment.py`가
그 **국내 데이터 공백**을 research-only로 채웁니다.

- `attention_datafield` — 종목별 뉴스량의 `log1p` (관심도 proxy)
- `sentiment_datafield` — 최근 헤드라인의 소형 한국어 극성 사전 점수 (−1..1)

adapter 계약(`docs/12`)을 지킵니다: 크리덴셜은 환경변수(`NAVER_HUB_KEY_ID` /
`NAVER_HUB_KEY`)에서만 읽고 snapshot에 담지 않으며, provider/observed 타임스탬프를 분리하고,
`stale` 플래그로 의존 alpha를 비활성화합니다. HTTP fetcher는 주입 가능해서 backtest/test는
완전히 오프라인으로 돕니다.

## 예시 alpha

```python
from toss_trading.alpha import Alpha, SimulationSettings, simulate_cross_section, to_signals
from toss_trading.alpha import operators as ops

# 관심도(datafield)가 낮은데 sentiment가 양수인 소외 반등 후보를 롱
alpha = Alpha(
    name="kor_neglect_reversal",
    expression=lambda ctx: ops.zscore(ctx["sentiment"]),  # datafield in, raw alpha out
)
settings = SimulationSettings(universe="KOSDAQ_SMALL", book_size=1.0,
                              neutralization="market", truncation=0.1)
positions = simulate_cross_section(alpha, {"sentiment": sentiment_values}, settings)
signals = to_signals(positions)   # -> 기존 RiskHub가 그대로 게이팅
```

이 alpha는 신호까지만 만듭니다. 체결은 기존 gate가 전부 통과된 뒤에야, 그리고
`live_trading_enabled`가 별도 승인될 때에만 가능합니다.

## Alpha Lifecycle

1. datafield 확보 (`alpha/datafields/`)
2. fast expression 작성 (`operators` 조합)
3. `simulate_cross_section`으로 position 산출
4. `metrics.evaluate` + `is_os_split`로 IS/OS 채점
5. fitness/turnover/self-correlation 게이트
6. `to_signals` → 기존 `RiskHub` → paper → (별도 승인) live

# Toss Trading Core

Toss Invest Open API를 기반으로 계좌 진실, point-in-time 데이터, 불변 데이터 계층, Feature/State, 자산가격결정, 기대수익, 위험모형과 목표 포트폴리오를 감사 가능하게 계산하는 모듈형 모놀리스입니다.

현재 구현 범위는 단계 0~16입니다. 운영 모드는 `READ_ONLY`이고 `live_trading_enabled: false`입니다. 목표 비중과 목표수량은 계산할 수 있지만 주문 생성·정정·취소를 전송하지 않습니다.

## 구현 범위

```text
0  거버넌스와 정책
1  공통 타입·UTC·설정·migration
2  Toss read-only 원본과 계좌 truth
3  주문 상태와 체결 delta 원장
4  현금·포지션·결제·tax lot
5  계좌 대사와 거래 gate
6  point-in-time 시간 truth
7  instrument·Universe·calendar·corporate action
8  immutable bronze/silver/gold/catalog
9  시장·거시·기업 데이터 수집 계약
10 데이터 품질과 source health
11 point-in-time Feature Store
12 Market·Company·Portfolio·System State
13 자산가격결정과 required return
14 expected return과 Alpha
15 covariance·tail·stress 위험모형
16 6계층 목표 포트폴리오 구성
```

단계별 완료조건과 운영 제한은 [구현 단계 0~16 전체 감사](docs/implementation_phases_0_16.md)를 기준으로 봅니다.

## 런타임 의존 순서

```text
investment policy -> account truth -> time truth -> data truth
-> financial calculation -> target portfolio -> risk control -> order
```

각 단계는 같은 runtime run에 속한 불변 evidence와 content hash를 요구합니다. 누락, stale, conflict, unknown 또는 unreconciled 입력은 다음 단계에서 보정하지 않고 `NO_TRADE`, `ABSTAIN` 또는 명시적 차단 결과로 끝납니다.

## 데이터와 모델

- Provider 원본은 secret 제거 후 bronze에 먼저 저장합니다.
- Silver는 정규화 데이터이고 gold는 Feature·State·검증 결과입니다.
- 조회는 `information_cutoff` 이전에 실제 사용 가능했던 vintage만 사용합니다.
- Feature와 State는 BUY/SELL을 만들지 않습니다.
- Required return, expected return과 Alpha는 별도 계약으로 저장합니다.
- 위험모형 실패나 covariance 역행렬 fallback은 optimizer를 차단합니다.
- Optimizer는 raw, risk-constrained, executable 목표를 반환하며 주문을 만들지 않습니다.

## Toss와 GCP 운영

Toss OAuth는 등록된 GCP 고정 IP VM에서 read-only로 검증됐습니다. Toss, Tiingo와 FRED 자격증명은 GCP Secret Manager에서 런타임에 주입하며 Git, 로그, bronze 원본과 manifest에 저장하지 않습니다.

```bash
export FOUNDATION_LOAD_GCP_SECRETS=1
./scripts/run_foundation_gcp.sh
```

VM에서는 Foundation timer만 활성화합니다. 단계 9 이후 계산 파이프라인의 24시간 scheduler 활성화와 실주문 활성화는 현재 범위에 포함되지 않습니다.

## 로컬 검증

```powershell
$env:PYTHONPATH='src'
python -m pytest -q
python scripts/check_governance.py
python scripts/check_toss_openapi.py
python -m build --wheel
```

현재 단계 16 기준 전체 회귀 테스트는 313개이며 Python 3.11과 3.12 CI를 통과했습니다.

## 주요 문서

- [단계 0~16 전체 감사](docs/implementation_phases_0_16.md)
- [아키텍처](docs/architecture.md)
- [불변 데이터 저장소](docs/immutable_datasets.md)
- [단계 9 데이터 수집](docs/data_collection_phase9.md)
- [단계 10 데이터 품질](docs/data_quality_phase10.md)
- [단계 11 Feature Store](docs/feature_store_phase11.md)
- [단계 12 State Engine](docs/state_engines_phase12.md)
- [단계 13 자산가격결정](docs/asset_pricing_phase13.md)
- [단계 14 기대수익과 Alpha](docs/expected_returns_phase14.md)
- [단계 15 위험모형](docs/risk_models_phase15.md)
- [단계 16 포트폴리오 구성](docs/portfolio_construction_phase16.md)
- [Toss API와 adapter](docs/07_toss_api_key_and_adapter.md)
- [GCP 고정 IP VM](docs/14_gcp_static_ip_runner.md)
- [Secret Manager와 런타임](docs/15_gcp_secret_manager_and_runtime.md)

## 현재 제한

- 실주문 승인과 전송 기능은 비활성입니다.
- Investment, risk와 execution 정책의 운영 승격은 별도 승인 대상입니다.
- ETF-first 운영 범위를 유지하며 개별주 연구 계약은 종목선정 운영에 활성화하지 않습니다.
- 실제 공급자 이용권, 최신성, calendar와 현금 source-of-truth는 매 실행의 manifest, source health와 reconciliation으로 확인해야 합니다.

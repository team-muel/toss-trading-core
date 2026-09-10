# Report Digest

이 문서는 현재 `toss-trading-core`의 운영·검증 원칙을 짧게 요약한 문서입니다. 과거 Foundation/Paper/standalone research runtime의 운용 계획이 아니라, `asset_management`를 단일 production boundary로 사용하는 현재 구조를 기준으로 합니다.

상세 구조는 `docs/architecture.md`와 `src/asset_management/ARCHITECTURE.md`, 연구 재구성은 `docs/research_reconstruction.md`, 레거시 정리와 실제 VM/GCP retirement 절차는 `docs/pr79_convergence.md`를 기준으로 합니다.

## 현재 결정된 원칙

1. `asset_management`만 production runtime authority를 가집니다.
2. `asset_management.toss`는 Toss 계약과 read-only broker/provider boundary를 제공하지만 투자 판단 권한을 가지지 않습니다.
3. `research_platform`과 `alpha_management`는 연구 계층입니다. 연구 결과는 직접 주문, 포지션, risk approval 또는 broker-write authority가 될 수 없습니다.
4. 모든 운영 판단은 point-in-time 데이터와 immutable evidence lineage를 보존해야 합니다.
5. 누락·stale·충돌·검증 불가 evidence는 추정으로 메우지 않고 fail-closed 처리합니다.
6. 과거 Foundation 계좌 evidence는 `asset_management.compatibility`를 통해 finalized/read-only 형태로만 읽습니다. 호환성 계층을 두 번째 runtime으로 복원하지 않습니다.
7. `cashBuyingPower` 같은 broker 값은 계약에 정의된 의미 그대로 사용하며 내부 현금 장부나 회계 NAV와 임의로 동일시하지 않습니다.
8. Research -> Signal/Forecast -> Portfolio/Risk 사이의 의미 전환은 명시적인 outer integration boundary에서만 일어납니다.
9. 테스트 통과, hash 일치, receipt 존재만으로 데이터 진실성·경제적 유용성·운영 승인 또는 live 권한을 주장하지 않습니다.
10. live trading은 현재 비활성화 상태이며 문서나 migration helper가 이를 우회할 수 없습니다.

## Canonical runtime order

```text
investment policy
  -> account truth
    -> time truth
      -> data truth
        -> financial calculation
          -> target portfolio
            -> risk control
              -> order
```

각 단계는 같은 runtime run 아래 immutable evidence identifier와 content hash에 결속됩니다. 뒤 단계가 앞 단계의 누락을 보정하거나 새로 계산해 권한을 만들어서는 안 됩니다.

## 데이터와 연구 역할

현재 주요 데이터 계층은 다음처럼 구분합니다.

- Toss: 계좌·보유·주문·buying-power 등 broker/account evidence의 외부 계약 경계.
- FRED/ALFRED: 금리와 vintage-aware 거시 시계열.
- Tiingo/Massive 등 시장 데이터 provider: 연구 및 검증용 시장 시계열.
- SEC/issuer source: 재무·공시·fundamental research 입력.
- immutable dataset/reference stores: PIT universe, source/schema/version/availability lineage의 기준.

외부 데이터가 풍부해도 account truth나 execution authority를 대체하지 않습니다. 반대로 broker 응답도 연구 모델의 경제적 유효성을 증명하지 않습니다.

## 현재 연구 범위

재구성된 연구 계층에는 다음이 포함됩니다.

- canonical ResearchSpec/ResearchRun receipt와 replay coordinates
- 여섯 개 Quant research family
- ALFRED 기반 canonical MacroState
- 검증된 재무 입력을 사용하는 Fundamental research feature

이 결과물은 연구 메커니즘과 재현성 계약을 제공하지만 실데이터 OOS 성과, Signal/Forecast 승인, portfolio allocation 또는 live execution acceptance를 의미하지 않습니다.

## 폐기된 운영 개념

다음은 현재 지원되는 독립 실행 경로가 아닙니다.

- `toss_trading` production runtime
- Foundation runner
- former Paper operation runtime
- standalone stock-recommendation service/timer
- standalone research cloud scheduler/application
- Gmail/report/GCS delivery를 독립 runtime으로 사용하는 경로

역사적 코드는 Git history에 남고 필요한 immutable evidence는 compatibility reader로 읽을 수 있지만, 이 경로들을 다시 production entry point로 복원하지 않습니다.

## 남아 있는 acceptance gap

Repository 수준 정리와 실제 운영 acceptance는 별개입니다. 특히 다음은 별도 증거가 필요합니다.

- 실제 VM의 legacy unit/scheduler retirement
- 실제 GCP alert/log metric 등 cloud resource ownership 및 retirement
- raw return -> factor estimator -> factor-risk evidence의 완전한 lineage/replay
- real-data OOS 및 Signal/Forecast acceptance
- production consumer cutover와 no-second-scheduler 증명

소유권이나 selector가 불명확한 cloud resource는 추정 삭제하지 않고 보존하여 manual review 대상으로 남깁니다.

## 검증 원칙

대표적인 repository 검증은 다음을 포함합니다.

```bash
python -m pytest -q
python scripts/check_toss_openapi.py
python scripts/check_maintenance_registry.py
python -m research_platform.cli.research_validate_instruments
python -m asset_management.cli.runtime_validate
python -m build --wheel
```

변경 surface에 따라 필요한 검증 범위는 달라집니다. `docs/maintenance_workflow.md`의 실제 diff 기반 분류와 정확한 head 기준 review/evidence를 따릅니다.

## No-Go 원칙

다음 상황에서는 후속 단계로 진행하지 않습니다.

- account/data/time truth가 일치하거나 검증되지 않음
- stale 또는 미래 정보가 PIT 경계를 침범함
- model/scope/calculation lineage가 서로 결속되지 않음
- risk approval과 실제 target/order identity가 다름
- research receipt 또는 hash만으로 production authority를 만들려고 함
- retirement plan 이후 실제 systemd/cloud 대상이 변경됨
- cloud resource가 shared인지 legacy-only인지 증명할 수 없음
- live trading을 명시적으로 승인하는 별도 gate 없이 broker-write path를 활성화하려 함

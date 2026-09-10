# 자율 전략 연구 검증 규약 — 역사 문서 / standalone 운영 폐기

> **운영 상태: retired.** 이 문서는 과거 standalone autonomous-research application의 연구·승격 규칙을 보존하는 역사 자료다. 현재 운영자가 `scripts/run_research_automation_gcp.sh` 또는 과거 research systemd unit을 실행하도록 지시하지 않는다.

## 현재 canonical 경계

현재 저장소의 유일한 operational application은 Point-in-Time Asset Management OS의 `asset_management` runtime이다. `alpha_management`와 `research_platform`은 내부·오프라인 연구 capability이며 독립 account/risk/portfolio/paper/execution authority나 별도 cloud scheduler를 갖지 않는다.

과거 가설의 다음 원칙은 연구 증거로 계속 유효하다.

- hypothesis/spec은 실행 전에 고정하고 변경 시 새로운 identity를 사용한다.
- historical qualification은 수익성 승인이나 거래 승격을 의미하지 않는다.
- OOS/prospective evidence, multiple-testing control, 비용 민감도와 반증 결과를 분리 보존한다.
- 연구 산출물은 직접 Forecast, portfolio weight, order 또는 live authority가 되지 않는다.
- negative result도 재현 가능한 연구 결과로 보존한다.

## 역사 증거

기존 `hypothesis-ledger`의 hypothesis/protocol/evaluation, immutable dataset, QA/OOS artifact는 provenance가 유지되는 범위에서 read-only evidence로 남길 수 있다. 이 자료를 읽을 수 있다는 사실은 과거 standalone runner를 복원하거나 활성화할 권한을 만들지 않는다.

## 신규 연구의 경로

신규 연구는 canonical PIT/reference truth와 `ResearchSpec`/research receipt를 사용하고, 실제 Signal/Forecast 후보화와 OOS 승격은 현행 `asset_management` validation/production contract를 거쳐야 한다. 공통 horizon/currency/basis, chronological OOS, purge/embargo, repeated-trial registry 및 비용 민감도는 해당 현재 이슈/게이트에서 증명한다.

## 폐기·운영 확인

과거 standalone service/timer 또는 cloud monitoring 잔재를 점검할 때는 `asset_management.cli.legacy_retirement`의 dry-run inventory를 사용한다. 실제 apply는 검토된 최신 plan hash가 있어야 하며, unknown/stale/shared resource는 fail-closed 또는 manual review로 남긴다.

이 문서의 과거 Git history에 존재하는 GCP runner 실행 지침은 더 이상 지원되는 runbook이 아니다. 현재 배포/운영은 Point-in-Time Asset Management OS의 canonical runbook과 acceptance gate만 따른다.

## 역사적 구현 참고

과거 정책·가설 원장·평가 알고리즘은 감사와 모델 기원 추적을 위해 source history에서 참조할 수 있다. 그 위치나 존재 여부를 standalone runtime 활성화 지침으로 해석하지 않는다.

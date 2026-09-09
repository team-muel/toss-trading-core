# 일일 자율 연구 운영 규칙 — 역사 문서 / standalone 일일 루프 폐기

> **운영 상태: retired.** 이 문서는 2026-08 당시 standalone daily research loop의 진단 기록이다. 현재 `toss-research-*` timer, Gmail digest, standalone GCP runner, 별도 BigQuery heartbeat를 계속 운영하라는 지침이 아니다.

## 역사적 배경

과거 daily/weekly 연구 자동화는 장기 Tiingo/FRED/Toss 자료, AI hypothesis generation, candidate evaluation, reporting/Gmail을 독립 application으로 묶어 운영했다. 당시에는 반복 메일, daily snapshot과 long-history 연결, prospective observation cadence를 조정하기 위한 운영 규칙이 필요했다.

이 구조는 현재 canonical single-runtime 결정에 따라 retired되었다. 역사 데이터와 연구 결과는 provenance가 유지되는 범위에서 읽을 수 있지만, 과거 timer나 runner를 다시 켜서는 안 된다.

## 현재 연구 진행 방식

- 신규 연구는 `alpha_management`/`research_platform` 내부 capability로 실행하며 canonical PIT/reference truth를 사용한다.
- 실제 OOS 증거는 현행 research/validation issue에서 chronological split, purge/embargo, repeated-trial registry, multiple-testing, turnover/cost sensitivity와 함께 남긴다.
- raw score는 Signal/Forecast/return/order가 아니며 현행 bridge와 gate를 통과하기 전 production 권한이 없다.
- 데이터가 추가되지 않았다는 이유로 standalone daily application이나 Gmail side-channel을 복원하지 않는다.

## 운영 확인 및 retirement

과거 문서의 다음 종류 명령은 더 이상 지원되지 않는다.

- `systemctl ... toss-research-*`
- standalone `scripts/run_research_automation_gcp.sh`
- Gmail/BigQuery/reporting heartbeat를 독립 research application의 건강성으로 사용하는 절차

기존 VM/GCP 잔재는 `python -m asset_management.cli.legacy_retirement ...` dry-run으로 inventory한다. 실제 변경은 검토된 최신 plan hash가 있어야 하며, unit identity나 symlink target이 변했으면 첫 destructive command 전에 실패한다. 공유·동적·미확인 cloud resource는 자동 삭제하지 않는다.

## 남겨 두는 역사적 의미

과거의 “새 시장 관측치가 생길 때만 연구 상태를 전진시키고 같은 데이터를 반복 검정하지 않는다”는 연구 원칙은 유효하다. 다만 그 원칙의 현재 구현 위치는 standalone daily scheduler가 아니라 canonical research specification, immutable evidence, OOS/Signal/Forecast validation 공정이다.

현재 운영 절차는 Point-in-Time Asset Management OS의 canonical runbook과 acceptance gate만 따른다.

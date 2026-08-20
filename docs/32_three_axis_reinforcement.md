# 운영 증거·신원 격리·persistent paper 보강

## 중심축

이 단계에서는 새로운 전략 계열을 늘리는 대신 다음 세 가지를 먼저 완성한다.

1. 활성 release와 같은 revision의 daily·weekly 불변 산출물, checksum, prune 성공을
   하나의 운영 감사로 묶는다.
2. Foundation과 research를 서로 다른 VM, service account, static IP, Secret Manager
   권한으로 분리한다.
3. 공개된 baseline rebalance를 `RiskDecision -> OrderPlan -> PaperBrokerAdapter ->
   체결 -> 현금·보유 대사`로 연결해 매일 지속되는 paper 장부를 만든다.

## 안전 경계

- 연구 VM에는 Foundation service/timer를 설치하지 않는다.
- 연구 service account는 Foundation client, account sequence, broker URL secret에
  접근할 수 없다.
- 비용 보정은 Foundation이 개인 식별자를 제거한 JSON만 별도 Secret Manager
  secret으로 발행한다.
- paper runner는 Toss broker adapter를 import하거나 client credential을 받지 않는다.
- research, paper, broad-stock systemd는 각각 writable한 전용 runtime의 `gcloud`
  config를 사용하며 Foundation의 `runtime/gcloud` credential cache를 읽거나
  갱신하지 않는다.
- baseline의 전향 검증이 끝나기 전 paper 결과는
  `infrastructure_validation_only`이며 strategy promotion 증거가 아니다.
- paper와 연구의 모든 정책에서 `live_orders_enabled=false`를 유지한다.

## 운영 완료 증거

`scripts/audit_active_research_release.sh`가 다음을 모두 확인해야 한다.

- `current`가 40자리 content-addressed release
- latest daily와 weekly의 `code_revision`이 current와 일치
- 두 run의 `ready_for_upload=true` 및 `SHA256SUMS` 통과
- daily, weekly, prune timer 활성
- prune service 마지막 결과 성공
- paper를 요구하는 단계에서는 paper report revision 일치와 대사 성공

revision 비교는 전략 artifact가 아니라 run reporting summary의 top-level
`code_revision`을 사용한다. 시장 날짜가 전진하지 않아 daily 전략 artifact가 정상
생략된 경우에도 유효한 run revision을 `None`으로 오판하지 않기 위해서다.

## 별도 연구 VM 전환

1. `scripts/provision_research_automation_gcp.sh`로 연구 service account, 예약 IP,
   VM, 최소 IAM을 만든다.
2. 깨끗한 Ubuntu VM에서 `scripts/bootstrap_research_vm.sh`를 실행한다.
3. immutable release를 설치하고 `scripts/install_research_automation_vm.sh`를 실행한다.
4. Toss 연구 client에 새 static IP를 등록한다.
5. `scripts/check_research_identity_gcp.sh`를 통과한다.
6. 새 VM에서 7회 연속 daily와 1회 weekly가 성공한 뒤 기존 VM의 research timer만
   중지한다. Foundation timer는 기존 VM에 남긴다.

VM을 즉시 만들 수 없더라도 Foundation과 research를 같은 service account로
영구 승인하지 않는다. 이는 명시적인 migration 상태다.

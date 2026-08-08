# GCP 연구 데이터 자동화

## 목적

이 자동화는 전략 연구에 필요한 데이터를 계속 최신화하면서도 잘못된
데이터가 백테스트에 조용히 섞이는 일을 막습니다. 계좌·주문 기준원장은
계속 Toss Foundation이 담당하고, 연구 데이터는 별도 실행 경로와 별도
GCS 버킷에 보관합니다.

기존 운영 감시는 변경하지 않습니다.

- `toss-foundation.timer`: 6시간 실행 유지
- 기존 Cloud Monitoring 경보: 6개 유지
- 자동 주문: 계속 비활성
- 연구 자동화: 읽기 전용 API만 호출

## 자동 실행

| 실행 | 시각 | 범위 | 목적 |
| --- | --- | --- | --- |
| daily | 매일 23:30 UTC 이후 최대 15분 지연 | 최근 45일, FRED revision 90일 | 미국 시장 데이터 확정 후 새 봉과 최근 정정분 수집 |
| weekly | 일요일 03:30 UTC 이후 최대 15분 지연 | 2004년 이후 전체 | 장기 누락·정정 재대사 |
| prune | 월요일 04:30 UTC 이후 최대 15분 지연 | 검증 완료 로컬 실행·불변 릴리스 | 일간 14일, 주간 90일, `previous` 롤백 링크를 포함해 현재+직전 릴리스 2개 유지 |

systemd 단위:

- `toss-research-daily.timer`
- `toss-research-weekly.timer`
- `toss-research-prune.timer`
- `toss-research-automation@daily.service`
- `toss-research-automation@weekly.service`

중복 실행은 `flock`으로 막습니다. daily와 weekly가 겹치면 하나만 실행하며
`research_automation_lock_busy` 이벤트를 남깁니다. Foundation과 연구
runner는 `/home/seoje/toss-trading/runtime/toss_api.lock`도 공유합니다.
Toss 토큰 재발급이 기존 토큰을 무효화하므로 두 작업이 동시에 OAuth/API를
호출하지 못하게 하는 안전장치입니다.

## 데이터 제품

매 실행은 이전 실행과 섞지 않고 다음 독립 경로에 저장합니다.

```text
research-runtime/runs/<mode>-<UTC timestamp>-<revision>/
  input/                 Toss raw/adjusted candle bundle
  lake/bronze/           공급자 원본
  lake/silver/           정규화된 Parquet market bars
  lake/catalog/          요청 메타데이터와 lineage manifest
  reports/               수집·정규화·QA 결과
    reporting-summary.json  시각화·BigQuery 공통 요약
    visual-report.html      비공개 최신 실행 보고서
  SHA256SUMS             전체 산출물 checksum
  run-status.json        업로드 승인 상태
```

`run-status.json`의 `ready_for_upload=true`가 되려면 다음이 모두 통과해야
합니다.

1. Toss raw와 split-adjusted bundle이 모두 존재하고 페이지가 비어 있지 않음
2. 두 bundle의 요청 universe가 같음
3. 허용된 공급자 미지원 오류만 존재함
4. OHLC, 음수 거래량, 시간 역전, 중복 key 검사가 모두 통과함
5. raw/split-adjusted 날짜 coverage가 일치함
6. bronze manifest와 silver Parquet이 존재함
7. 모든 산출물의 SHA-256이 생성됨

검증이 실패하면 GCS 업로드와 BigQuery 이력 갱신을 하지 않습니다. GCS에는
`run_id`별 새 객체만 생성하며 mutable `latest` 객체를 두지 않습니다.

## 자동 수집 범위

Toss는 현재 등록된 GCP 고정 IP에서 매일 다음 read-only 데이터를
수집합니다.

- raw/split-adjusted 일봉
- 종목 기본정보와 현재가
- 종목별 매수 유의사항
- USD/KRW 참고 환율
- 미국 장 캘린더
- 미국 거래대금·거래량·상승·하락·Toss 체결 기준 랭킹
- KOSPI/KOSDAQ과 한국 국채 2·3·5·10·20·30년 지표 현재가·일봉
- KOSPI/KOSDAQ 투자자별 매매대금

이 중 환율은 Toss 문서상 참고용 표시 환율이므로 실제 환전 체결 환율을
대체하지 않습니다. Toss adjusted candle은 배당까지 포함한 총수익률로
검증되지 않았으므로 계속 `split_adjusted/estimated`로 격리합니다.

## 외부 공급자 자동 활성화 게이트

외부 키가 없거나 계약·연락처 승인이 없으면 Toss 수집은 계속 성공하고
해당 공급자만 `research_provider_skipped`로 기록합니다.

| 공급자 | 자동 실행 조건 | 기본 상태 |
| --- | --- | --- |
| Tiingo | 약관 수락, `tiingo-api-token` 최신 버전 | 차단 |
| FRED/ALFRED | series별 권리 검토 승인, registry enabled, `fred-api-key` | 차단 |
| SEC EDGAR | 승인된 연락처 User-Agent, `sec-user-agent`, weekly 실행 | 차단 |

게이트는 `/etc/toss-trading/research.env`에서 관리합니다. 값은 사용자가
실제로 승인한 뒤에만 `1`로 바꿉니다. API key와 User-Agent 원문은
Secret Manager에만 저장하고 Git·로그·manifest에는 넣지 않습니다.

FRED 수집은 공식 API의 `output_type=3`을 사용해 지정된 realtime
구간에서 새로 발표되거나 수정된 관측값을 원본 그대로 보관합니다.
SEC weekly 수집은 ticker map, submissions, companyfacts를 보관합니다.

## GCP 사용 범위

- Compute Engine: 고정 IP가 있는 수집 실행기
- 현재 VM workload identity는 Foundation과 공유합니다. research 프로세스는
  account secret을 로드하지 않지만, live 검토 전에는 research 전용
  VM/service account로 분리해야 합니다.
- Secret Manager: Toss와 승인된 외부 공급자 자격증명
- Cloud Storage: private 연구 데이터, 버전 관리, 이전 버전 90일 lifecycle
- runtime bucket 권한: `roles/storage.objectCreator`; run-id 경로의 새 객체만 생성.
  일반 `gcloud storage cp`가 수행하는 사전 객체 조회를 쓰지 않고,
  `ifGenerationMatch=0`인 resumable create-only 업로더로 기존 객체의
  조회·덮어쓰기·삭제 권한 없이 업로드합니다. 작은 Parquet 객체가 많은
  주간 run은 최대 16개 worker로 제한해 병렬 생성하며, worker 상한은
  코드에서 32개로 강제합니다.
- Ops Agent와 Cloud Logging: JSONL 구조화 로그 수집
- Cloud Monitoring: 실패·QA·heartbeat·백업·중복 실행 경보
- Cloud Build: 전용 `toss-research-build` 서비스 계정으로 고정 의존성 테스트,
  wheel build, shellcheck 후 artifact 보관. 이 계정은 Cloud Build 소스
  버킷의 객체 조회와 연구 버킷의 artifact 생성만 허용

Cloud Run/Functions/Scheduler를 추가하지 않은 이유는 Toss 허용 IP가 현재
VM 고정 IP에 묶여 있고 systemd persistent timer가 같은 실행 책임을 더
단순하게 충족하기 때문입니다.

## 운영 이벤트

안정적으로 사용하는 이벤트:

```text
research_automation_start
research_automation_ok
research_automation_failed
research_automation_lock_busy
research_provider_ok
research_provider_skipped
research_validation_ok
research_validation_failed
research_backup_upload_ok
research_reporting_summary
research_reporting_upload_ok
research_reporting_upload_failed
research_email_ok
research_email_failed
research_interpretation_ok
research_interpretation_failed
research_strategy_artifact_ok
research_strategy_promotion_pending
research_strategy_promotion_blocked
research_weekly_automation_ok
research_weekly_stale
research_hypothesis_planning_failed
research_hypothesis_evaluation_failed
```

연구 자동화에는 기존 Foundation 경보와 별도로 15개 경보를 둡니다.

- runner 실패
- 데이터 검증 실패
- BigQuery reporting upload 실패
- 23시간 동안 성공 heartbeat 없음
- 23시간 동안 GCS 업로드 heartbeat 없음
- 실행 lock 경합
- Gmail 전달 실패
- Vertex AI 해석 fallback
- 전략 승격 차단
- 주간 연구가 8일 이상 오래됨
- VM 디스크 사용률 85% 초과
- VM 메모리 사용률 85% 초과
- Ops Agent heartbeat 30분 누락
- AI 가설 제안 실패
- 후보 통계 평가 실패

전향 OOS 최소 표본을 정상적으로 수집하는 동안에는
`research_strategy_promotion_pending`만 기록하며 실패 경보를 보내지 않습니다.
최소 표본이 완성된 뒤 방법론 또는 벤치마크 관문을 통과하지 못한 경우에만
`research_strategy_promotion_blocked` 경보를 보냅니다.

대시보드 상시 확인을 줄이기 위해 현재·이전 실행의 검증 근거를 Vertex AI가
해석한 연구 보고서를 Gmail API로 발송합니다. 근거 계약, AI 실패 시 `FACTS`
대체 보고서, 최소 OAuth 범위와 실제 적용 순서는
`docs/24_gmail_research_digest.md`를 따릅니다. 장애 알림은 Gmail 및 Vertex AI
경로와 독립적인 Cloud Monitoring notification channel을 계속 사용합니다.

## 배포와 검증

새 release에서 먼저 전체 테스트·wheel build·shellcheck를 통과시킵니다.
그 다음 Cloud Shell용 provisioner가 버킷, bucket IAM, log metrics와
선택적인 alert policy를 구성합니다. VM용 installer는 systemd timer와
Ops Agent를 구성합니다. 서로 다른 권한 경계를 한 스크립트에 섞지
않습니다.

```bash
# Cloud Shell
export MONITORING_NOTIFICATION_CHANNEL='projects/toss-trading-core-lab/notificationChannels/<id>'
./scripts/provision_research_automation_gcp.sh

# personal-agent-vm
./scripts/install_research_automation_vm.sh
sudo systemctl start toss-research-automation@daily.service
```

배포 완료 증거:

```bash
systemctl is-enabled toss-foundation.timer
systemctl is-enabled toss-research-daily.timer
systemctl is-enabled toss-research-weekly.timer
systemctl status toss-research-automation@daily.service --no-pager
gcloud storage ls gs://toss-trading-core-lab-research-data/research/status/
tail -n 20 /home/seoje/toss-trading/research-runtime/research_automation.jsonl
```

## 아직 자동화할 수 없는 결정

기술적으로 키를 읽고 수집을 실행하는 부분은 자동화되어 있습니다.
그러나 다음 결정은 사용자를 대신해 자동 승인하지 않습니다.

- Tiingo 라이선스 수락
- FRED series별 저작권·출처·표시 의무 검토
- SEC에 전송할 실제 연락처 승인
- 유료 공급자 구매와 예산
- 연구 결과를 live 주문에 연결하는 승인

이 게이트를 해제하기 전에도 Toss 기반 데이터 최신화, 품질검사, GCS
백업과 운영 경보는 계속 자동 실행됩니다.

## 2026-07-27 운영 배포 재검증

Cloud Shell, VM, private GCS, BigQuery, Cloud Logging과 dashboard를 다시
대조한 현재 상태입니다.

릴리스와 빌드:

- VM 활성 릴리스: `a6c1471`
- 로컬 branch HEAD: `72c352d`
- 두 revision의 차이는 마지막 commit이 dashboard aligner와 관련 테스트·문서만
  변경했기 때문이며 VM research runtime drift가 아닙니다.
- 최종 dashboard 배포 Cloud Build:
  `79fe84ec-36c6-494f-9390-16fbd103a889`, `SUCCESS`
- Cloud Build 완료: `2026-07-27 21:22:23 KST`

최신 daily 실행:

- run: `daily-20260727T121203Z-a6c1471`
- verified: `2026-07-27 21:12:46 KST`
- BigQuery ingest: `2026-07-27 21:13:38 KST`
- GCS `latest-daily.json`: `ready_for_upload=true`,
  `code_revision=a6c1471`, `mode=daily`
- Toss: `collected`; Tiingo, FRED/ALFRED, SEC EDGAR는 승인 gate에 의해
  의도대로 `skipped`
- 요청 종목 15개, 검증 종목 14개
- raw page 14개, split-adjusted page 14개
- `SPLG` raw/adjusted가 각각 `404 stock-not-found`, dashboard 수집 실패
  요청은 정확히 2건
- 중복·OHLC·거래량·시간·coverage 품질 오류는 0행
- strategy 상태는 `not_available`,
  이유는 `verified_total_return_history_not_available`

`ready_for_upload`는 허용된 provider 미지원 항목 외의 산출물이 QA와
checksum을 통과했다는 뜻입니다. strategy-ready 또는 전 종목 완전 수집을
뜻하지 않습니다.

운영 감시:

- VM: `RUNNING`, 삭제 방지 활성
- VM 서비스 계정:
  `toss-foundation-runner@toss-trading-core-lab.iam.gserviceaccount.com`
- `toss-foundation.timer`: `OnUnitActiveSec=6h`, `Persistent=true`,
  `active`, `enabled`
- research daily/weekly timer: 모두 `active`, `enabled`
- Ops Agent: `active`
- 기존 Foundation 경보 6개와 research 경보 6개, 전체 12개 유지
- research log metric 18개 유지
- dashboard의 분포 metric aligner는 `ALIGN_SUM`만 사용
- dashboard 실측값: 품질 오류 0, 검증 종목 14, 수집 실패 요청 2
- 모든 `live_trading_enabled`와 `live_orders_enabled` 정책은 `false`

마지막 Foundation 성공 로그는 `2026-07-27 19:02:29 KST`,
`code_revision=383d9db`입니다. 그 뒤 VM symlink가 `a6c1471`로 바뀌었으므로
다음 6시간 scheduled run이 새 릴리스의 첫 Foundation 성공 증거를 남길
예정입니다. timer·service 실패는 관찰되지 않았고 마지막 journal도
snapshot, audit, 로컬 백업과 GCS 백업 성공으로 종료됐습니다.

첫 배포 검증 중 생성된 `daily-20260727T094237Z-unknown`은 불변 이력으로
남아 있습니다. 압축 릴리스에 Git 메타데이터가 없어 revision이
`unknown`이 된 원인을 수정했으며, 최신 상태 포인터와 이후 실행은
검증된 릴리스 디렉터리 이름에서 revision을 자동 복구합니다.

## 2026-08-08 P0 runtime 재검증

- `personal-agent-vm`: `RUNNING`
- foundation, research daily, research weekly timers: 모두 `active`, `enabled`
- Foundation `v0-empty-safe`: 2026-08-08 13:12:05 KST 성공, snapshot·audit·
  로컬/GCS 백업 성공, `code_revision=36a60066a485...`
- 2026-08-03~06 daily: Toss, Tiingo, FRED 수집과 전체 automation 성공
- 2026-08-08 첫 실행: Tiingo read timeout으로 실패
- 당일 복구 run `daily-20260808T050927Z-36a60066a485`: 전체 성공, GCS와
  BigQuery 업로드 및 digest 전송 성공
- Tiingo/FRED gate: 활성, SEC contact gate: 비활성
- 디스크 사용률: 25%
- 현재 VM service account는 여전히 Foundation 계정과 공유한다. 목표 분리
  절차와 금지 권한은 `docs/27_p0_identity_and_holdout_remediation.md`에 고정했다.

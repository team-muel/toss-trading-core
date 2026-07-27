# 운영·데이터 품질·전략 성과 통합 시각 보고

## 목적

한 화면에서 다음 세 질문에 답하는 것이 목표입니다.

1. 수집기와 기존 Foundation 운영 감시는 정상인가?
2. 이번 데이터는 백테스트에 사용할 수 있을 만큼 완전한가?
3. 검증된 전략 기준선의 수익과 위험은 시간에 따라 어떻게 변하는가?

보고 기능은 주문 경로와 완전히 분리되어 있습니다.
`live_orders_enabled=false`, 6시간 `toss-foundation.timer`, 기존 Foundation
경보 6개와 연구 경보 5개는 변경하지 않습니다.

## 구조

```text
daily/weekly research run
  -> QA + immutable manifest
  -> reporting-summary.json + visual-report.html
  -> private GCS latest snapshot
  -> Cloud Logging structured summary event
       -> distribution log metrics
       -> Cloud Monitoring integrated dashboard
  -> BigQuery run_summaries
       -> latest_run_summaries deduplicated view
```

각 실행의 공통 요약은
`reports/reporting-summary.json`에
`research-visual-report-v1` 계약으로 저장됩니다. 같은 값으로 HTML,
Cloud Logging 이벤트, BigQuery 행을 만들기 때문에 서로 다른 화면의
숫자가 조용히 어긋나지 않습니다. JSON과 HTML은 `SHA256SUMS`에 포함됩니다.
검증·BigQuery 전송 명령의 가변 stdout은 run 밖의 `last-*.json`에
저장하여 checksum 계산 뒤 불변 run을 수정하지 않습니다.

## 전략 성과 표시 원칙

Toss `adjusted=true` 일봉은 현재 분할 조정으로만 검증됐습니다. 배당을
포함한 total-return 자료가 아니므로 전략 성과 입력으로 사용하지 않습니다.

전략 차트는 다음 조건을 모두 만족하는 불변 experiment가 연결된 경우에만
값을 표시합니다.

- `adjustment=total_return`
- 입력 manifest ID 보존
- 코드 revision 보존
- 유한한 `total_return`, `cagr`, 변동성, Sharpe, 최대 낙폭, Calmar,
  turnover, trading days

조건을 충족하지 못하면 전략 상태는 `not_available`, 이유는
`verified_total_return_history_not_available`이며 모든 성과 숫자는
`null`입니다. 이를 0% 수익으로 바꾸지 않습니다.

## 데이터 품질 지표

실행마다 다음 값을 기록하고 추이를 시각화합니다.

- 중복 행 수
- OHLC·거래량·시점 유효성 오류 행 수
- raw/split-adjusted coverage 불일치 행 수
- 총 품질 오류 행 수
- Toss raw·adjusted 수집 실패 요청 수
- 검증 종목 수
- Toss raw+adjusted 페이지 수
- 검증 전 원천 산출물 수와 byte 크기
- 공급자별 `collected` 또는 승인 게이트에 따른 `skipped` 상태

## GCP 자원

### Cloud Monitoring

대시보드 이름:

```text
Toss Trading - Operations, Data Quality, Strategy
```

대시보드는 운영 성공·실패, Toss 수집 실패, 품질 오류, 종목 수, 수집량,
전략 수익률·CAGR, Sharpe·최대 낙폭, 최근 구조화 요약 로그를 표시합니다.
전략 metric은
`strategy_state=available` 로그만 추출합니다.
분포형 로그 지표는 백분위 버킷 중간값을 표시하지 않도록 실행 구간에서
`ALIGN_SUM`으로 합친 뒤 `REDUCE_MEAN`으로 실제 `sum/count` 평균을
표시합니다. daily/weekly 1회 실행 값은 원본 숫자와 동일합니다.

프로젝트 대시보드 목록:

```text
https://console.cloud.google.com/monitoring/dashboards?project=toss-trading-core-lab
```

### BigQuery

```text
toss-trading-core-lab.toss_research_reporting.run_summaries
toss-trading-core-lab.toss_research_reporting.latest_run_summaries
```

원본 테이블은 `verified_at` 날짜로 partition하고 `mode`,
`strategy_state`로 cluster합니다. `insertAll` 재시도에서 같은 `run_id`가
중복될 가능성은 deduplicated view가 `ingested_at` 최신 행 하나만
선택하도록 처리합니다. VM 전용 서비스 계정에는 이 dataset의
`roles/bigquery.dataEditor`만 부여하며 서비스 계정 키 파일을 만들지
않습니다.

### 비공개 GCS

```text
gs://toss-trading-core-lab-research-data/research/reports/latest-daily.json
gs://toss-trading-core-lab-research-data/research/reports/latest-daily.html
gs://toss-trading-core-lab-research-data/research/reports/latest-weekly.json
gs://toss-trading-core-lab-research-data/research/reports/latest-weekly.html
```

public ACL이나 익명 웹사이트는 만들지 않습니다.

## 배포

Cloud Shell에서 다음 명령은 BigQuery dataset/table/view, dataset IAM,
18개 research log metric, 통합 Cloud Monitoring dashboard를
idempotent하게 생성 또는 갱신합니다. 기존 경보는 삭제하지 않습니다.

```bash
export MONITORING_NOTIFICATION_CHANNEL='projects/toss-trading-core-lab/notificationChannels/<id>'
./scripts/provision_research_automation_gcp.sh
```

VM release를 설치한 뒤 최신 실행을 한 번 수행하면 품질 metric과 BigQuery
첫 행이 생깁니다.

```bash
./scripts/install_research_automation_vm.sh
sudo systemctl start toss-research-automation@daily.service
```

검증:

```bash
gcloud monitoring dashboards list \
  --project=toss-trading-core-lab \
  --filter='displayName="Toss Trading - Operations, Data Quality, Strategy"'
bq query --project_id=toss-trading-core-lab --use_legacy_sql=false \
  'SELECT run_id, verified_at, quality_error_rows, strategy_state
   FROM `toss-trading-core-lab.toss_research_reporting.latest_run_summaries`
   ORDER BY verified_at DESC LIMIT 10'
gcloud logging read \
  'resource.type="gce_instance" AND jsonPayload.event="research_reporting_summary"' \
  --project=toss-trading-core-lab --limit=5
```

## 아직 비어 있는 부분의 의미

현재 Toss 수집과 데이터 품질 보고는 즉시 동작합니다. Tiingo 라이선스·키가
승인되지 않았으므로 total-return 전략 차트는 비어 있는 것이 정상입니다.
이 화면은 데이터 부족을 감추지 않고 다음 투자 의사결정을 명확히 만드는
보고 체계이지, 아직 존재하지 않는 수익을 만들어 보여주는 화면이 아닙니다.

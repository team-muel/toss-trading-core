# Toss Invest Open API Foundation

토스증권 Open API의 인증, 계좌, 보유종목, 주문 조회, 거래 가능 정보와
read-only 시장 데이터를 안전하게 호출하고 감사 가능한 SQLite 원장으로
보존하는 최소 시스템입니다.

이 저장소에는 투자 연구, 종목 추천, 백테스트, 외부 데이터 공급자,
모의매매 또는 자동 주문 판단 기능은 포함하지 않습니다.

## 제공 기능

- OAuth2 Client Credentials 토큰 발급
- 계좌 목록과 보유종목 조회
- OPEN/CLOSED 주문 목록과 주문 상세 조회
- 매수 가능 금액, 매도 가능 수량, 수수료 조회
- 현재가, 호가, 최근 체결, 가격 제한, 1분봉·일봉 조회
- 국내·미국 거래 가능 종목과 종목 기본정보 조회
- 국내 종목 투자자·프로그램·공매도·신용·대차 동향 조회
- 환율, 장 운영 시간, 랭킹, 국내 지수·국채 지표 조회
- 원본 API 응답 저장, 계좌 상태 정규화, 체결 delta와 현금원장 대사
- GCP Secret Manager, 고정 IP VM, systemd Foundation timer 운영

주문 생성·정정·취소 및 조건주문 쓰기는 공식 계약에는 존재하지만 이
Foundation 어댑터에서는 비활성 상태입니다.

## 구조

```text
Toss Open API
  -> read-only broker adapter
  -> account snapshot / raw response ledger
  -> reconciliation / audit / replay
  -> Foundation monitoring and encrypted backup
```

## 로컬 실행

```powershell
$env:PYTHONPATH='src'
python -m toss_trading.cli.foundation_snapshot
python -m toss_trading.cli.foundation_audit
```

저장된 원본 응답으로 독립 replay를 실행할 수 있습니다.

```powershell
$env:PYTHONPATH='src'
python -m toss_trading.cli.foundation_replay `
  --source-db '<foundation.sqlite>' `
  --destination-db '<new-replay.sqlite>'
```

## VM 운영

```bash
export FOUNDATION_LOAD_GCP_SECRETS=1
./scripts/run_foundation_gcp.sh
```

VM에서는 `toss-foundation.timer`만 활성화합니다. 런타임 자격증명은 파일이나
Git에 저장하지 않고 GCP Secret Manager에서 로드합니다.

## 문서

- [구현 단계 9 Point-in-time 데이터 수집](docs/data_collection_phase9.md)
- [구현 단계 0~8 현황 및 완료 조건 감사](docs/implementation_phases_0_8.md)
- [불변 데이터 저장소와 공급자 공통 계약](docs/immutable_datasets.md)
- [API 키와 어댑터](docs/07_toss_api_key_and_adapter.md)
- [계좌 상태 대사](docs/08_account_state_reconciliation.md)
- [공식 API 범위](docs/09_toss_official_api_coverage.md)
- [Rate limit](docs/11_rate_limit_token_bucket.md)
- [실계좌 read-only 검증](docs/13_foundation_v1_funded_read_only_validation.md)
- [GCP 고정 IP VM](docs/14_gcp_static_ip_runner.md)
- [Secret Manager와 런타임](docs/15_gcp_secret_manager_and_runtime.md)
- [Foundation 모니터링](docs/16_cloud_monitoring_runner_health.md)

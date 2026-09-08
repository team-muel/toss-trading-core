# Foundation VM Monitoring

이 문서는 Toss Open API Foundation VM의 운영 상태만 다룹니다.

## Stable events

- `foundation_runner_start`
- `foundation_runner_ok`
- `foundation_runner_failed`
- `foundation_snapshot_start`
- `foundation_snapshot_ok`
- `foundation_snapshot_failed`
- `foundation_audit_start`
- `foundation_audit_ok`
- `foundation_audit_failed`
- `gcp_secret_environment_loaded`
- `foundation_runner_backup_ok`
- `foundation_runner_backup_upload_ok`
- `foundation_runner_lock_busy`

Ops Agent는 `/home/seoje/toss-trading/runtime/foundation_runner.jsonl`을 JSON으로
파싱해 Cloud Logging으로 전송합니다. Foundation 경보는 실행 실패, 스냅샷 실패,
감사 실패, heartbeat 누락, 백업 heartbeat 누락과 lock 충돌만 감시합니다.

VM에는 `toss-foundation.timer`만 활성화합니다. 다른 `toss-*` timer가 나타나면
구성 드리프트로 간주하고 비활성화합니다.

## 확인 명령

```bash
systemctl is-enabled toss-foundation.timer
systemctl is-active toss-foundation.timer
systemctl list-timers 'toss-*' --all --no-pager
tail -n 20 /home/seoje/toss-trading/runtime/foundation_runner.jsonl
```

자격증명 값은 로그에 기록하지 않습니다. Secret Manager 로더는 로드하거나
건너뛴 환경변수 이름만 기록합니다.

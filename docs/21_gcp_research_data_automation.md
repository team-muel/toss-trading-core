# GCP research-data automation

Research automation is a separate, read-only collection surface. It produces
immutable data products for `research_platform`; it never starts an account,
portfolio, risk, or execution runtime and cannot submit orders.

## Scheduled work

| Schedule | Scope | Output |
| --- | --- | --- |
| daily | Recent market bars and revision window | Immutable run directory and QA report |
| weekly | Long-horizon reconciliation and provider revisions | Immutable run directory and QA report |
| prune | Retention of validated local runs | Keeps the current and rollback releases |

The active systemd units are `toss-research-daily.timer`,
`toss-research-weekly.timer`, and `toss-research-prune.timer`. The installer is
`scripts/install_research_automation_vm.sh`. It installs only research and
stock-recommendation units; deprecated account and paper-operation units are
not installed.

## Controls

- `flock` prevents overlapping collection runs.
- Missing provider consent, credentials, or contact information emits a
  provider-specific skipped result; it does not silently change collection scope.
- A run uploads only after lineage, coverage, temporal ordering, and quality
  checks pass.
- GCS writes are create-only under the run id; mutable latest pointers are
  derived after validation.
- Secrets remain in Secret Manager or the protected research environment, never
  in Git, manifests, or logs.

## Deployment verification

```bash
./scripts/provision_research_automation_gcp.sh
./scripts/install_research_automation_vm.sh
sudo systemctl start toss-research-automation@daily.service
systemctl is-enabled toss-research-daily.timer
systemctl is-enabled toss-research-weekly.timer
```

This deployment does not authorize research outputs for trading. Any future
handoff must enter `asset_management` through its explicit governed integration
boundary and satisfy its normal evidence gates.

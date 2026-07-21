# Remediation And Live Gate

## Current rule

The repository is read-only Foundation software. Paper, shadow, and live order
submission remain blocked until the latest complete snapshot run proves the
approved OpenAPI contract, account binding, reconciliation, and recovery path.

## Required evidence

- clean-checkout CI imports the runtime package and passes tests
- OpenAPI 1.2.4 SHA-256
  `7000d89ea3d783b0fa36d32e31750e85e139098306dbfce53a75fc4891019f1b`
  is explicitly approved; CLOSED listing is disabled by default and available
  only as an explicit recovery path verified against the real API
- one complete run contains only 2xx broker evidence from one account
- v1 uses an exact order ID captured while OPEN or recovered from the verified
  CLOSED listing, then validates that exact order detail
- amounts retain decimal text and are reported per currency, never summed
  across KRW and USD without an FX source and timestamp
- backup/restore and IP allowlist checks have passed in the real GCP runtime

## Completed prerequisites

- GCP static external IP registered in the Toss Open API allowlist on 2026-07-21
- VM uses the dedicated service account
  `toss-foundation-runner@toss-trading-core-lab.iam.gserviceaccount.com`
- the default Compute Engine service account Editor grant was removed and the
  account was disabled
- direct public SSH/RDP is disabled; administrative access uses IAP and OS Login
- VM deletion protection is enabled and the boot disk does not auto-delete with
  the instance
- the private backup bucket enforces uniform bucket access, public access
  prevention, object versioning, and a 30-day soft-delete retention period
- the dedicated VM service account has bucket-scoped object write access
- the systemd runner uses `UMask=0077`, runs every six hours, and does not submit
  orders
- Cloud Logging metrics and runner/snapshot/audit/heartbeat alert policies are
  connected to the verified operator email channel
- GitHub `master` requires the passing CI check, applies the rule to admins,
  blocks force pushes and deletion, and requires linear history
- Toss Client ID and Client Secret were reissued on 2026-07-21; Secret Manager
  version 2 is enabled and version 1 is disabled for both secrets

## Latest Verified Runtime State — 2026-07-21 19:04 KST

The VM completed the read-only `v0-empty-safe` run with:

- `Result=success` and `ExecMainStatus=0`
- one account discovered
- all tested broker endpoints returning successful responses
- zero non-2xx broker rows
- two holdings, one KRW and one USD, and zero open orders
- USD holding market value of about 1.60 and USD buying power of 5.11
- two sellable-quantity rows
- `blockers=['none']`
- local SQLite backup creation and private GCS upload success

The run was repeated successfully after the old secret versions were disabled.
No automatic, paper, shadow, or live order was submitted.

## Manual Foundation v1 Attempt — 2026-07-21

The user manually purchased one domestic share for KRW 1,070. The order filled
before the read-only runner captured it in the OPEN order list.

- the holding and sellable quantity are visible to the Open API
- the order is outside the currently approved US-listed ETF universe
- the Toss web order-history identifier returned HTTP 400 from the Open API
- the numeric web display order number returned `invalid-request` and
  `유효하지 않은 주문 ID 입니다.`
- execution, commission, and settlement evidence could not be attached to the
  same Open API order detail
- Foundation v1 therefore remains **not passed**

After the two bounded read-only detail checks, the scheduled v0 service was run
again successfully and uploaded a fresh private GCS backup. Additional order-ID
guessing was stopped.

### Overseas-stock follow-up

The user then manually purchased one share of Richtech Robotics. The runner
confirmed the new USD holding, two commission-rate schedule rows, and a second
sellable-quantity row. The order had already filled before the OPEN snapshot.

The Toss web encrypted identifier and its numeric display order number were
each tested once against the read-only Open API order-detail endpoint and both
returned HTTP 400. A bounded `status=CLOSED` diagnostic then returned the real
Open API order ID. Using that exact ID, the v1 run produced the target detail,
filled execution, execution delta, actual commission, settlement date, and
sellable-quantity evidence.

Foundation v1 passed at 19:04 KST with `blockers=['none']`, and its SQLite
backup was uploaded to the private GCS bucket. The stock remains outside the
approved strategy universe and is not made strategy-eligible by this test.

## Remaining Gates In Priority Order

1. **P1 — Restore drill.** Restore a private GCS SQLite backup into an isolated
   temporary path and verify database integrity and audit evidence. Do not
   overwrite the live database during the drill.
2. **P1 — Scheduled-run observation.** Observe at least 24 hours of six-hour
   timer runs and verify that the notification path is operational.
3. **P2 — Foundation regression review.** Recheck universe/master 1:1 mapping,
   raw-response replay, source-health behavior, and clean-checkout CI.
4. **P3 — External minimum data stack.** Begin Massive REST, corporate actions,
   FRED, and SEC only after v1 passes.
5. **P4 — Signal safety and paper planner.** Add feature/signal separation and
   stale-data gates before any shadow operation.
6. **P5 — Shadow/live gate.** Complete two weeks of shadow operation and obtain
   a separate explicit approval before considering a micro-live run.

Foundation v1 is complete. The repository and VM remain read-only while the
next safety and external-data stages are implemented.

## Scope

The first strategy, once enabled, is US-listed USD broad ETF momentum only.
Relative-value, distribution/NAV/ROC, options, short, margin, and Korean/FX
multi-currency strategies remain research-only until their independent data
and risk gates exist.

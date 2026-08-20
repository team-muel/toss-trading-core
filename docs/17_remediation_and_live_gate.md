# Remediation And Live Gate

## Current rule

The repository is read-only Foundation software. Paper, shadow, and live order
submission remain blocked until the latest complete snapshot run proves the
approved OpenAPI contract, account binding, reconciliation, and recovery path.

Research collection, QA, reporting, and backtests may run in parallel because
they use read-only data paths and cannot enable broker order submission.

## Current Operating Gate — 2026-07-27

- The VM points to release `a6c1471`; research automation on that release
  completed successfully.
- The latest successful Foundation evidence was emitted at 19:02 KST from
  revision `383d9db`. The next six-hour timer run will provide the first
  Foundation evidence from the newer release; this is a pending evidence item,
  not a current service failure.
- Foundation, research daily, and research weekly timers are active and
  enabled. The Foundation unit remains `OnUnitActiveSec=6h` with
  `Persistent=true`.
- Ops Agent is active. Six Foundation alerts and five research alerts were
  deployed at the recorded snapshot; the repository now defines a sixth
  research alert for reporting-upload failures and requires redeployment.
- All `live_trading_enabled` and `live_orders_enabled` policy entries remain
  `false`.
- The historical Toss `SPLG` failure was a stale ticker mapping. The current
  canonical ticker and Toss request symbol are `SPYM`; Tiingo retains `SPLG`
  only as an auditable provider alias. A read-only Toss `SPYM` request returned
  HTTP 200 and the 18-day Toss/Tiingo raw close cross-check had 0.0 bps maximum
  error.
- Licensed Tiingo total-return history and a sealed immutable gold experiment
  now exist. Headline strategy performance remains hidden until the registered
  prospective sample completes; availability is not proof of strategy quality.

## Required evidence

- clean-checkout CI imports the runtime package and passes tests
- OpenAPI 1.2.14 SHA-256
  `0f2cb7ef938fe1c50b7d69348705632ad488ea68d63fc762847f6c9485a3a111`
  is explicitly approved; CLOSED listing runs with a bounded seven-day overlap
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
- six Cloud Monitoring alert policies are enabled and connected to the
  verified operator email channel
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
- this first domestic-order attempt did not pass Foundation v1 by itself

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

1. **Completed — Codex 24-hour focused observation.** Closed by user approval
   on 2026-07-23. This closes only the temporary Codex observation automation.
2. **Completed — Foundation regression review.** Universe/master mapping,
   source-health behavior, clean-checkout CI, and real v0/v1 backup raw replay
   passed on 2026-07-23.
3. **P0 safety lane — Account and order safety.** Complete currency-scoped cash
   ledger, independently evidenced opening balance, reserved cash,
   buying-power reconciliation, and EOD report. This lane blocks every broker
   order mode.
4. **P1 research lane — Total-return and point-in-time data (implemented).**
   Tiingo is license/token gated, `SPYM`/`SPLG` aliases and corporate-action
   effective dates are registered, raw providers are cross-checked, and v3
   uses benchmark-relative folds plus sanitized account-scale costs. Activation
   requires a matching immutable release and v3 gold revision.
5. **P1 research extension — Macro and filings.** Activate FRED/ALFRED after
   series-rights approval and SEC after the outbound contact identity is
   approved.
6. **P2 — Strategy evidence.** Run the preregistered dual-momentum baseline,
   benchmarks, cost stress, OOS, and walk-forward tests on verified
   total-return inputs.
7. **P3 — Signal safety and persistent paper.** Add feature/signal separation,
   engine-scoped stale gates, and a realistic persistent simulator.
8. **P6 — Shadow/live gate.** Complete two weeks of shadow operation and obtain
   a separate explicit approval before considering a micro-live run.

Foundation v1 is complete. The repository and VM remain read-only while the
next safety and external-data stages are implemented.

## Restore And Observation Status — 2026-07-23

The private v1 backup was restored to an isolated drill directory. SQLite
integrity and the full v1 audit passed from the restored copy; the live database
was not overwritten. The restored file remains mode `600` as audit evidence.

The six-hour `toss-foundation.timer` remains an active operational control.
All six Cloud Monitoring alert policies also remain enabled with the verified
operator notification channel.

The temporary Codex automation that reviewed the preceding 24 hours each day
at 19:00 KST was paused on 2026-07-23 with user approval. Pausing that Codex
automation does not disable, modify, or replace the VM timer, Cloud Logging,
Cloud Monitoring policies, or operator email notifications.

## Foundation Replay And Cash Event Status — 2026-07-23

The latest v0 backup and the retained v1 backup were downloaded to an isolated
temporary directory and replayed into new SQLite databases. Both replays
verified every stored response hash and passed their matching Foundation audit.
The live VM database and GCS objects were not modified.

Replay exposed a real normalization gap: raw account identifiers are correctly
redacted, so a replay must restore only the approved internal
`snapshot_run.account_seq` key. The replay path now does so without restoring or
exposing an account number.

Execution deltas now produce idempotent, currency-scoped `TRADE_COST`,
`TRADE_PROCEEDS`, `COMMISSION_FEE`, and `REGULATORY_FEE` cash events using exact
decimal text. Synthetic partial-fill-to-full-fill tests prove that only
incremental amounts are posted. Current OPEN buy orders now reserve their
remaining notional once per broker order, and an unresolvable notional becomes
an audit blocker. Independently evidenced opening cash, settlement availability,
and buying-power reconciliation remain blocked work; cash events and open-order
reservations alone do not make live trading eligible.

The official OpenAPI 1.2.14 document was downloaded again on 2026-08-20. Its
SHA-256 matches the approved value, and it still exposes buying power
rather than a separate cash-balance endpoint. An independently evidenced
opening balance is therefore a real external input, not a value the runtime may
infer from `cashBuyingPower`.

## Scope

The first strategy, once enabled, is US-listed USD broad ETF momentum only.
Relative-value, distribution/NAV/ROC, options, short, margin, and Korean/FX
multi-currency strategies remain research-only until their independent data
and risk gates exist.

## P0 And Research Foundation Work — 2026-07-23

The next implementation branch keeps all broker writes disabled and addresses
the audit findings before deployment:

- failed-run execution deltas are backfilled into exact cash events
- cash-event completeness is audited per principal, commission, tax, and
  settlement date
- unresolved historical reconciliation BLOCK records remain blocking until an
  explicit resolution note is recorded
- buying power is queried for every observed holding currency
- snapshot runs and JSON logs record the immutable code revision
- order plans require an approved RiskDecision, an AccountLedger idempotency
  reservation, an account, and an approved universe
- six alert policy templates, ten log-metric definitions, and systemd
  hardening are checked into the repository
- a separate immutable raw/Parquet/manifest research data layer and a
  next-day, cost-aware dual-momentum baseline are implemented

These changes are local branch work until CI, review, merge, and VM release-SHA
verification complete. They do not authorize or submit a Toss order.

# toss-trading-core

`toss-trading-core` is a point-in-time asset-management and research repository with one canonical production execution boundary: `asset_management`.

The supported runtime is fail-closed and starts in `READ_ONLY` mode. Live trading is disabled. Research, historical compatibility, and provider adapters may supply governed evidence, but they do not create a second execution path or grant broker-write authority.

## Canonical boundaries

| Package | Responsibility | Authority |
| --- | --- | --- |
| `asset_management` | Account/data/time truth, financial calculations, portfolio construction, risk, decision and execution contracts | Only production runtime boundary |
| `asset_management.toss` | Toss API contract and read-only broker/provider adapters | Outer adapter only; no investment-decision authority |
| `asset_management.compatibility` | Read/replay of finalized immutable Foundation-era evidence | Historical evidence only; no runtime entry point |
| `research_platform` | Data collection, immutable datasets, backtests, hypotheses, diagnostics and reporting | Research/offline only; cannot create orders |
| `alpha_management` | Research expression language, transforms, canonical quantitative templates and evaluation | Research only; cannot call brokers or bypass portfolio/risk policy |

The retired `toss_trading` runtime, Foundation runner, Paper operation, standalone research schedulers and superseded deployment entry points are not supported execution surfaces. Their Git history remains available, while retained historical account evidence is accessed only through the explicit read-only compatibility boundary.

## Governed runtime order

Every decision run must preserve the same evidence chain:

```text
investment policy
  -> account truth
    -> time truth
      -> data truth
        -> financial calculation
          -> target portfolio
            -> risk control
              -> order
```

Each accepted stage is bound to immutable evidence under the same runtime run. A later stage cannot infer, repair or substitute for an earlier stage. Missing, stale, conflicting or unverifiable prerequisites stop the run.

Research output reaches production only through an explicit outer integration boundary. A research score, backtest result or model hash is not itself a forecast, portfolio target, risk approval or order authorization.

## Validation

Canonical read-only validation:

```bash
python -m asset_management.cli.runtime_validate
python -m research_platform.cli.research_validate_instruments
```

Repository verification commonly includes:

```bash
python -m pytest -q
python scripts/check_toss_openapi.py
python scripts/check_maintenance_registry.py
python -m research_platform.cli.research_validate_instruments
python -m build --wheel
python -c "import asset_management.orchestration.runtime"
```

Exact required evidence depends on the changed maintenance surfaces. Follow `docs/maintenance_workflow.md`; a passing local suite or CI run does not grant semantic, operational or live-trading approval.

## Operational migration status

Repository cleanup does not prove that previously deployed VM units, schedulers, alert policies or log metrics have been retired. The reviewed fail-closed inventory/apply procedure is documented in `docs/pr79_convergence.md` and implemented by `asset_management.cli.legacy_retirement`. Unknown, shared or unverifiable cloud resources are retained for manual review rather than deleted speculatively.

The remaining acceptance work is intentionally separate from repository cleanup, including real host/cloud retirement evidence, raw-return-to-factor-estimator lineage, real-data OOS/Signal/Forecast acceptance, and production consumer cutover. None of those gaps is converted into authority by documentation or compatibility code.

## Documentation map

- `docs/00_report_digest.md` — current operating and safety digest.
- `docs/architecture.md` — runtime architecture and mandatory gate order.
- `src/asset_management/ARCHITECTURE.md` — compile-time dependency rules.
- `docs/pr79_convergence.md` — canonical-runtime convergence and legacy-retirement procedure.
- `docs/research_reconstruction.md` — research reconstruction and authority boundaries.
- `docs/maintenance_workflow.md` — finite-change and evergreen maintenance workflow.
- `AGENTS.md` — repository contribution and governance contract.

## Safety invariant

Live trading remains disabled in `config/application.yaml`. No documentation, research result, compatibility reader, migration helper or passing test may be interpreted as implicit broker-write authorization.

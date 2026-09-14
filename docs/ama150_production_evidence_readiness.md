# AMA-150 production evidence readiness

This is a read-only readiness boundary. It neither creates a runtime run nor
stores an `economic-input/*` observation, calculation, Gate D2 result, M5 state,
or live-trading authority.

Run it only against an already provisioned evidence database and immutable
research root:

```powershell
python -m asset_management.cli.ama150_production_readiness `
  --database <existing-evidence.sqlite> `
  --immutable-store <existing-research-root> `
  --runtime-run-id <existing-runtime-run-id>
```

The command opens the database read-only. A non-zero exit and `BLOCKED` output
are the expected fail-closed result until every prerequisite is independently
provisioned and qualified.

## Minimum source-bound inputs

| Instruments | Components | Required source class | Current state |
| --- | --- | --- | --- |
| SPY, QQQ, VTV | underlying growth, distribution yield, valuation reversion | official issuer PIT facts | UNASSIGNED |
| SPY, QQQ, VTV | factor exposure | approved factor-model evidence | UNASSIGNED |
| SPY, QQQ, VTV | momentum overlay | Tiingo total-return observations | credential required |
| TLT | yield, duration effect | official issuer PIT facts | UNASSIGNED |
| TLT | roll-down | approved PIT Treasury curve | UNASSIGNED |
| TLT | credit/default/expense | approved bond-component methodology | UNASSIGNED |
| GLD | spot change | approved PIT spot/fund valuation source | UNASSIGNED |
| GLD | carry, expense; physical-backed structure | official issuer PIT facts | UNASSIGNED |
| SGOV | current yield, expense | official issuer PIT facts | UNASSIGNED |
| SGOV | FX effect | approved PIT FX source and inclusion semantics | UNASSIGNED |

GLD's physical-backed structure may omit `roll_yield` only when immutable
structure evidence is supplied to the assembler. This checklist never supplies
or assumes a zero value. Existing FRED/ALFRED approved series are useful macro
lineage, but do not alone satisfy the TLT curve or any ETF component requirement.

## Sample-ingest acceptance

For each qualified source, retain provider-originated raw bytes and a Bronze
manifest, derive a Silver manifest with parent lineage, and bind the resulting
immutable dataset manifest to the same pre-existing `runtime_run_id`. The runtime
must already select an active model authorized for `EXPECTED_RETURN`. Only then
may a separate, reviewed ingestion adapter append the component's
`economic-input/<component>` observation. No caller payload, fixture, pricing
baseline, alpha, or synthetic value is an alternative.

This work does not authorize a canonical production run, Gate D2 PASS, M5, or
live trading.

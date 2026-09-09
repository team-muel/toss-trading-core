# Toss Trading

This repository has one canonical execution boundary: `asset_management`.
Its runtime starts only in `READ_ONLY` mode and preserves the documented
policy, point-in-time, reconciliation, risk, authorization, and immutable
evidence gates. It does not enable live trading.

## Package responsibilities

| Package | Responsibility | Authority |
| --- | --- | --- |
| `asset_management` | Canonical runtime, account/data/time truth, policy, portfolio, risk, and execution contracts | The only production runtime boundary |
| `asset_management.toss` | Read-only Toss API adapter and contract types | Outer adapter; no investment decision authority |
| `asset_management.compatibility` | Read/replay of immutable historical Foundation evidence | Historical evidence only; no runtime entry point |
| `research_platform` | Collection, immutable datasets, backtests, hypotheses, and reporting | Research only; cannot create orders |
| `alpha_management` | Expression language, operators, simulations, and evaluation | Research only; cannot call brokers or create orders |

The retired `toss_trading` package, Foundation runner, and former Paper
operation do not exist in the checkout or packaged wheel. Their source history
remains recoverable from Git; historical broker evidence remains readable via
the compatibility boundary without restoring a second execution path.

## Canonical validation

```powershell
python -m asset_management.cli.runtime_validate
python -m research_platform.cli.research_validate_instruments
```

`toss-runtime-validate` validates the canonical runtime against an in-memory
database at an explicit UTC instant. It neither contacts Toss nor uses broker
credentials. Research automation is separately scheduled through the
`toss-research-*` services and remains outside the runtime authority boundary.

## Operational rules

- Live trading stays disabled in `config/application.yaml`.
- Missing, stale, conflicting, or unverifiable evidence stops the relevant run.
- Research and alpha results must pass the explicit outer integration boundary
  before they can contribute to any portfolio proposal.
- Review the repository contract in [AGENTS.md](AGENTS.md), the runtime
  architecture in [docs/architecture.md](docs/architecture.md), and the
  maintenance workflow in [docs/maintenance_workflow.md](docs/maintenance_workflow.md).

## Verification baseline

```powershell
python -m pytest -q
python scripts/check_toss_openapi.py
python -m research_platform.cli.research_validate_instruments
python -m build --wheel
python -c "import asset_management.orchestration.runtime"
```

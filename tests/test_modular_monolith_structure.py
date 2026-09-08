from pathlib import Path
import ast


EXPECTED_MODULES = """
domain/money.py domain/quantity.py domain/identifiers.py domain/enums.py domain/errors.py domain/events.py
config/loader.py config/schemas.py config/versions.py
time/clock.py time/asof.py time/calendars.py time/timezone.py
broker/base.py broker/toss_read.py broker/toss_write.py broker/contracts.py broker/rate_limit.py broker/redaction.py
account/snapshots.py account/holdings.py account/orders.py account/executions.py
ledger/cash.py ledger/positions.py ledger/tax_lots.py ledger/settlement.py ledger/reconciliation.py ledger/replay.py
reference/instruments.py reference/aliases.py reference/universe.py reference/corporate_actions.py
data/raw_store.py data/manifests.py data/normalization.py data/repositories.py data/asof_query.py
quality/validators.py quality/source_health.py quality/issue_registry.py quality/quality_propagation.py
features/registry.py features/market.py features/macro.py features/company.py features/portfolio.py
states/market.py states/company.py states/portfolio.py states/system.py
pricing/risk_free.py pricing/capm.py pricing/factors.py pricing/black_litterman.py pricing/reverse_dcf.py
expectations/equity.py expectations/etf.py expectations/bond.py expectations/cash.py expectations/alpha.py expectations/confidence.py
risk/covariance.py risk/factor_risk.py risk/var.py risk/cvar.py risk/stress.py risk/contributions.py risk/liquidity.py risk/event_risk.py
portfolio/strategic.py portfolio/allocator.py portfolio/optimizer.py portfolio/constraints.py portfolio/costs.py portfolio/rebalance.py portfolio/rounding.py
decisions/governor.py decisions/journal.py decisions/reason_codes.py
execution/intents.py execution/planner.py execution/paper.py execution/shadow.py execution/live.py execution/fills.py
replay/event_store.py replay/engine.py replay/ordering.py
validation/walk_forward.py validation/bootstrap.py validation/perturbation.py validation/calibration.py validation/benchmarks.py validation/promotion.py
orchestration/runtime.py orchestration/scheduler.py orchestration/locks.py orchestration/pipelines.py
monitoring/metrics.py monitoring/alerts.py monitoring/health.py monitoring/kill_switch.py
reporting/daily.py reporting/weekly.py reporting/decision_report.py reporting/incident.py
""".split()


def test_requested_modular_monolith_boundaries_exist():
    root = Path(__file__).parents[1] / "src" / "asset_management"
    missing = [module for module in EXPECTED_MODULES if not (root / module).is_file()]
    assert missing == []


def test_domain_does_not_depend_on_outer_application_layers():
    domain = Path(__file__).parents[1] / "src" / "asset_management" / "domain"
    forbidden = {
        "broker", "account", "ledger", "data", "quality", "features", "states",
        "pricing", "expectations", "risk", "portfolio", "decisions", "execution",
        "replay", "validation", "orchestration", "monitoring", "reporting", "cli",
    }
    violations = []
    for path in domain.glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            names = []
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module]
            for name in names:
                parts = name.split(".")
                if len(parts) > 1 and parts[0] == "asset_management" and parts[1] in forbidden:
                    violations.append(f"{path.name}:{name}")
    assert violations == []


CORE_LEVEL = {
    "domain": 0,
    "time": 1,
    "config": 1,
    "reference": 1,
    "broker": 2,
    "account": 2,
    "ledger": 2,
    "data": 2,
    "quality": 2,
    "features": 3,
    "states": 4,
    "pricing": 5,
    "expectations": 5,
    "risk": 5,
    "portfolio": 6,
    "decisions": 7,
    "execution": 8,
}


def _asset_management_imports(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    imports: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imports.append(node.module)
    return [name for name in imports if name.startswith("asset_management.")]


def test_core_dependencies_only_point_inward():
    """Lower-level modules must never know about a higher-level module."""

    root = Path(__file__).parents[1] / "src" / "asset_management"
    violations = []
    for source_name, source_level in CORE_LEVEL.items():
        for path in (root / source_name).rglob("*.py"):
            for imported in _asset_management_imports(path):
                parts = imported.split(".")
                if len(parts) < 2 or parts[1] not in CORE_LEVEL:
                    continue
                target_name = parts[1]
                if CORE_LEVEL[target_name] > source_level:
                    violations.append(f"{path.relative_to(root)} -> {imported}")
    assert violations == []


def test_core_does_not_import_application_shell():
    """Orchestration and delivery concerns remain outside the core dependency graph."""

    root = Path(__file__).parents[1] / "src" / "asset_management"
    shell = {"replay", "validation", "orchestration", "monitoring", "reporting", "cli"}
    violations = []
    for source_name in CORE_LEVEL:
        for path in (root / source_name).rglob("*.py"):
            for imported in _asset_management_imports(path):
                parts = imported.split(".")
                if len(parts) > 1 and parts[1] in shell:
                    violations.append(f"{path.relative_to(root)} -> {imported}")
    assert violations == []

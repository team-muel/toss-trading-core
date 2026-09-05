"""Compile-time ownership checks for the research-only alpha package."""

import ast
from pathlib import Path


ALPHA_ROOT = Path(__file__).parents[1] / "src" / "alpha_management"
FORBIDDEN_PARTS = {"account", "broker", "execution", "ledger", "orders"}
FORBIDDEN_PROVIDER_PARTS = {"naver", "toss", "fred", "sec"}


def imported_modules(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    modules = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
    return modules


def test_alpha_has_no_account_broker_order_or_provider_dependency():
    violations = []
    for path in ALPHA_ROOT.glob("*.py"):
        for module in imported_modules(path):
            parts = set(module.lower().split("."))
            if parts & (FORBIDDEN_PARTS | FORBIDDEN_PROVIDER_PARTS):
                violations.append(f"{path.name}: {module}")
    assert not violations, "alpha ownership boundary violated: " + ", ".join(violations)


def test_legacy_alpha_modules_do_not_reimplement_canonical_operators_or_metrics():
    legacy_root = ALPHA_ROOT.parent / "toss_trading" / "alpha"
    for name in ("operators.py", "metrics.py"):
        tree = ast.parse((legacy_root / name).read_text(encoding="utf-8"))
        definitions = [
            node.name for node in tree.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
        ]
        assert definitions == [], f"{name} duplicated canonical behavior: {definitions}"


def test_legacy_alpha_exports_are_canonical_objects():
    from alpha_management import Alpha as CanonicalAlpha
    from alpha_management import metrics as canonical_metrics
    from alpha_management import operators as canonical_operators
    from toss_trading.alpha import Alpha as LegacyAlpha
    from toss_trading.alpha import metrics as legacy_metrics
    from toss_trading.alpha import operators as legacy_operators

    assert LegacyAlpha is CanonicalAlpha
    assert legacy_operators.rank is canonical_operators.rank
    assert legacy_metrics.evaluate is canonical_metrics.evaluate

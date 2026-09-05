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

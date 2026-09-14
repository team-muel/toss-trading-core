"""Enforce AMA-156's remaining legacy boundary without deleting readers or CLIs."""

from __future__ import annotations

import argparse
import ast
from fnmatch import fnmatchcase
import json
from pathlib import Path
import sys
import tomllib
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
INVENTORY_PATH = Path("config/legacy_boundary_inventory.json")
_DYNAMIC_IMPORT_MODULES = {"builtins", "importlib", "pkgutil", "runpy", "zipimport"}
_DYNAMIC_IMPORT_NAMES = {
    "__builtins__",
    "__import__",
    "compile",
    "eval",
    "exec",
    "globals",
    "locals",
    "vars",
}
_DYNAMIC_IMPORT_ATTRIBUTES = {
    "__import__",
    "__loader__",
    "__spec__",
    "create_module",
    "exec_module",
    "find_spec",
    "import_module",
    "loader",
    "meta_path",
    "modules",
}
_DYNAMIC_IMPORT_LOOKUP_NAMES = _DYNAMIC_IMPORT_ATTRIBUTES | _DYNAMIC_IMPORT_NAMES


def relative_paths(root: Path, pattern: str) -> set[str]:
    return {
        path.relative_to(root).as_posix()
        for path in root.glob(pattern)
        if path.is_file()
    }


def toss_trading_imports(path: Path) -> tuple[set[str], bool]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    imports: set[str] = set()
    dynamic_import = False
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.update(
                alias.name
                for alias in node.names
                if alias.name == "toss_trading" or alias.name.startswith("toss_trading.")
            )
            # Import machinery makes an arbitrary target impossible to prove
            # outside a local AST scan.  Canonical asset-management code has no
            # reviewed dynamic import mechanism, so reject it fail-closed.
            if any(
                alias.name.split(".", 1)[0] in _DYNAMIC_IMPORT_MODULES
                for alias in node.names
            ):
                dynamic_import = True
            # The sole canonical `sys` use is `sys.prefix`, used to find a
            # packaged provider-contract file.  A local alias could otherwise
            # hide `sys.modules` or import machinery from this structural
            # check, so it is not a reviewed form.
            if any(alias.name == "sys" and alias.asname for alias in node.names):
                dynamic_import = True
        elif isinstance(node, ast.ImportFrom) and node.module:
            if node.module == "toss_trading" or node.module.startswith("toss_trading."):
                imports.add(node.module)
            if node.module.split(".", 1)[0] in _DYNAMIC_IMPORT_MODULES:
                dynamic_import = True
            if node.module == "sys":
                dynamic_import = True
        elif isinstance(node, ast.Name) and node.id in _DYNAMIC_IMPORT_NAMES:
            dynamic_import = True
        elif isinstance(node, ast.Attribute) and node.attr in _DYNAMIC_IMPORT_ATTRIBUTES:
            dynamic_import = True
        elif (
            isinstance(node, ast.Attribute)
            and isinstance(node.value, ast.Name)
            and node.value.id == "sys"
            and node.attr != "prefix"
        ):
            dynamic_import = True
        elif (
            isinstance(node, ast.Assign)
            and isinstance(node.value, ast.Name)
            and node.value.id == "sys"
        ):
            dynamic_import = True
        elif isinstance(node, ast.Call):
            function_name = (
                node.func.id
                if isinstance(node.func, ast.Name)
                else node.func.attr if isinstance(node.func, ast.Attribute) else ""
            )
            if function_name in {"__import__", "import_module"}:
                # A non-literal dynamic target cannot be proven to be outside
                # toss_trading.  Canonical modules have no reviewed dynamic
                # import mechanism, so reject every such call fail-closed.
                dynamic_import = True
            if function_name == "getattr":
                # Do not permit reflective recovery of import machinery.  The
                # existing canonical getattr uses inspect ordinary domain
                # fields, never these loader names.
                if (
                    node.args
                    and isinstance(node.args[0], ast.Name)
                    and node.args[0].id == "sys"
                ) or any(
                    isinstance(argument, ast.Constant)
                    and argument.value in _DYNAMIC_IMPORT_LOOKUP_NAMES
                    for argument in node.args[1:]
                ):
                    dynamic_import = True
    return imports, dynamic_import


def boundary_import_violations(
    root: Path, allowlist: dict[str, set[str]]
) -> list[str]:
    violations: list[str] = []
    for path in sorted((root / "src/asset_management").glob("**/*.py")):
        relative = path.relative_to(root).as_posix()
        imports, dynamic_import = toss_trading_imports(path)
        allowed_targets = allowlist.get(relative, set())
        for target in sorted(imports - allowed_targets):
            violations.append(f"{relative}:{target}")
        if dynamic_import:
            violations.append(f"{relative}:dynamic-import")
    return violations


def classified_toss_sources(root: Path, inventory: dict[str, Any]) -> dict[str, list[str]]:
    sources = relative_paths(root, "src/toss_trading/**/*.py")
    groups = inventory["toss_trading_source_classification"]
    result: dict[str, list[str]] = {}
    unclassified: list[str] = []
    ambiguous: list[str] = []
    for source in sorted(sources):
        categories = [
            category
            for category, patterns in groups.items()
            if any(fnmatchcase(source, pattern) for pattern in patterns)
        ]
        if len(categories) == 1:
            result[source] = categories
        elif not categories:
            unclassified.append(source)
        else:
            ambiguous.append(f"{source}:{','.join(categories)}")
    if unclassified or ambiguous:
        raise ValueError(
            "toss_trading inventory must classify each source exactly once; "
            f"unclassified={unclassified}, ambiguous={ambiguous}"
        )
    return result


def validate_inventory(root: Path, inventory: dict[str, Any]) -> dict[str, Any]:
    allowlist = {
        path: set(targets)
        for path, targets in inventory[
            "canonical_asset_management_toss_import_allowlist"
        ].items()
    }
    for path, targets in allowlist.items():
        if not (root / path).is_file():
            raise ValueError(f"allowlisted path does not exist: {path}")
        if not targets:
            raise ValueError(f"allowlisted path has no approved import targets: {path}")

    classifications = classified_toss_sources(root, inventory)
    project = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    scripts = project["project"]["scripts"]
    expected_scripts = inventory["packaging_entry_points"]
    actual_legacy_scripts = {
        name: target
        for name, target in scripts.items()
        if isinstance(target, str) and target.startswith("toss_trading.")
    }
    if actual_legacy_scripts != expected_scripts:
        raise ValueError(
            "research packaging entry-point inventory drift; "
            f"expected={expected_scripts}, actual={actual_legacy_scripts}"
        )

    actual_cli = relative_paths(root, "src/toss_trading/cli/*.py") - {
        "src/toss_trading/cli/__init__.py"
    }
    expected_cli = set(inventory["legacy_cli_modules"])
    if actual_cli != expected_cli:
        raise ValueError(
            f"research CLI inventory drift; expected={sorted(expected_cli)}, actual={sorted(actual_cli)}"
        )

    actual_units = {
        path.relative_to(root).as_posix()
        for path in (root / "deploy/systemd").iterdir()
        if path.is_file() and path.suffix in {".service", ".timer"}
    }
    expected_units = set(inventory["systemd_units"])
    if actual_units != expected_units:
        raise ValueError(
            f"systemd inventory drift; expected={sorted(expected_units)}, actual={sorted(actual_units)}"
        )

    violations = boundary_import_violations(root, allowlist)
    if violations:
        raise ValueError(
            "canonical asset_management may import toss_trading only through the "
            f"read-only compatibility allowlist; violations={violations}"
        )
    return {
        "allowlist": {path: sorted(targets) for path, targets in sorted(allowlist.items())},
        "classified_toss_sources": len(classifications),
        "research_cli_modules": len(actual_cli),
        "research_packaging_entry_points": len(actual_legacy_scripts),
        "systemd_units": len(actual_units),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--inventory", type=Path, default=INVENTORY_PATH)
    args = parser.parse_args(argv)
    root = args.root.resolve()
    inventory_path = args.inventory if args.inventory.is_absolute() else root / args.inventory
    try:
        inventory = json.loads(inventory_path.read_text(encoding="utf-8"))
        result = validate_inventory(root, inventory)
    except (OSError, ValueError, KeyError, tomllib.TOMLDecodeError, SyntaxError) as error:
        print(f"legacy boundary check failed: {error}", file=sys.stderr)
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

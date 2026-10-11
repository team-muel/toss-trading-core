"""Check canonical core import direction without importing application code."""

from __future__ import annotations

import argparse
import ast
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
LEVELS = (
    ("domain",),
    ("time", "config", "reference"),
    ("broker", "account", "ledger", "data", "quality"),
    ("features", "governance", "recovery"),
    ("states",),
    ("pricing", "expectations", "risk", "calculations", "signals"),
    ("portfolio",), ("decisions",), ("execution",),
)
RANK = {name: rank for rank, names in enumerate(LEVELS) for name in names}
SHELLS = frozenset(("replay", "validation", "orchestration", "monitoring", "reporting", "cli"))


def import_targets(node: ast.AST, package: tuple[str, ...]) -> list[str]:
    if isinstance(node, ast.Import):
        return [alias.name for alias in node.names]
    if not isinstance(node, ast.ImportFrom):
        return []
    if node.level:
        if node.level > len(package):
            raise ValueError("relative import escapes canonical package")
        base = ".".join((*package[:len(package) - node.level + 1], *((node.module or "").split(".") if node.module else ())))
    else:
        base = node.module or ""
    if base == "asset_management":
        return [f"{base}.{alias.name}" for alias in node.names]
    return [base]


def check_layers(root: Path) -> dict[str, object]:
    source_root = root / "src" / "asset_management"
    paths = sorted(source_root.rglob("*.py"))
    if not paths:
        raise ValueError("canonical sources missing")
    violations: list[str] = []
    for path in paths:
        parts = path.relative_to(root / "src").with_suffix("").parts
        package = parts[:-1]
        source = parts[1] if parts[1] != "__init__" else None
        if source is not None and source not in RANK and source not in SHELLS:
            violations.append(f"{path.relative_to(root).as_posix()}: unclassified layer {source}")
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            for target in import_targets(node, package):
                if (target == "alpha_management" or target.startswith("alpha_management.")) and source not in SHELLS:
                    violations.append(f"{path.relative_to(root).as_posix()}:{node.lineno}: {target}: core cannot import sibling research")
                if target == "asset_management" or target.startswith("asset_management."):
                    layer = target.split(".")[1] if "." in target else None
                    reason = None
                    if layer not in RANK and layer not in SHELLS:
                        reason = "unclassified target"
                    elif source is None:
                        reason = "package root cannot compose core"
                    elif source in RANK and (layer in SHELLS or RANK[layer] > RANK[source]):
                        reason = "outward core dependency"
                    elif source == "execution" and layer in {"features", "states", "pricing", "expectations", "risk", "calculations", "signals", "portfolio"}:
                        reason = "execution cannot manufacture decisions"
                    if reason:
                        violations.append(f"{path.relative_to(root).as_posix()}:{node.lineno}: {target}: {reason}")
    if violations:
        raise ValueError("; ".join(violations))
    return {"checked_sources": len(paths), "core_layers": sorted(RANK), "shell_layers": sorted(SHELLS)}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    args = parser.parse_args(argv)
    try:
        result = check_layers(args.root.resolve())
    except (OSError, ValueError, SyntaxError) as error:
        print(f"layer dependency check failed: {error}", file=sys.stderr)
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

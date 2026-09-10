from __future__ import annotations

import sys
from pathlib import Path


def resolve_resource(path: str | Path) -> Path:
    """Resolve a repository resource from a checkout or an installed wheel."""

    candidate = Path(path)
    if candidate.is_file():
        return candidate
    installed = Path(sys.prefix) / "share" / "toss-trading" / candidate
    if installed.is_file():
        return installed
    raise FileNotFoundError(
        f"toss-trading resource not found in checkout or installed data: {candidate}"
    )

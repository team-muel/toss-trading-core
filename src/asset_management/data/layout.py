from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class DataLakeLayout:
    root: Path

    def bronze(self) -> Path:
        return self.root / "bronze"

    def silver(self) -> Path:
        return self.root / "silver"

    def gold(self) -> Path:
        return self.root / "gold"

    def manifests(self) -> Path:
        return self.root / "manifests"

    def catalog(self) -> Path:
        return self.root / "catalog"

    def resolve(self, layer: str, relative: str) -> Path:
        if layer not in {"bronze", "silver", "gold", "manifests", "catalog"}:
            raise ValueError("unknown data layer")
        layer_root = (self.root / layer).resolve()
        unresolved = self.root / layer / relative
        if unresolved.name in {".", "..", ""}:
            raise ValueError("data path requires a file or directory name")
        # Resolving an existing hard-link leaf on Windows can race publication.
        # Resolve its directory and reject leaf symlinks rather than follow them.
        candidate = unresolved.parent.resolve() / unresolved.name
        if candidate.is_symlink():
            raise ValueError("data path cannot be a symbolic link")
        if candidate != layer_root and layer_root not in candidate.parents:
            raise ValueError("data path escapes its configured layer")
        return candidate

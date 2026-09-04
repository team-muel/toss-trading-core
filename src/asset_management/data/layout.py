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

    def resolve(self, layer: str, relative: str) -> Path:
        if layer not in {"bronze", "silver", "gold", "manifests"}:
            raise ValueError("unknown data layer")
        candidate = (self.root / layer / relative).resolve()
        layer_root = (self.root / layer).resolve()
        if candidate != layer_root and layer_root not in candidate.parents:
            raise ValueError("data path escapes its configured layer")
        return candidate

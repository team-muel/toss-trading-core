"""Resolve shipped runtime contracts independently of the process directory."""
from pathlib import Path
import sys


def resource_root() -> Path:
    installed = Path(sys.prefix) / "share" / "toss-trading"
    checkout = Path(__file__).resolve().parents[3]
    for root in (checkout, installed):
        if (root / "config/application.yaml").is_file() and (root / "schemas/asset_management.sql").is_file():
            return root
    raise FileNotFoundError("canonical runtime resources are not installed")

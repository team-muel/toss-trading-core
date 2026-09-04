from dataclasses import dataclass
from datetime import datetime, timezone


def _require_utc(value: datetime, name: str) -> None:
    if value.tzinfo is None or value.utcoffset() != timezone.utc.utcoffset(None):
        raise ValueError(f"{name} must be timezone-aware UTC")


@dataclass(frozen=True, slots=True)
class AsOfContext:
    run_id: str
    as_of_utc: datetime
    information_cutoff_utc: datetime
    policy_version: str
    parameter_set_id: str
    code_revision: str

    def __post_init__(self) -> None:
        _require_utc(self.as_of_utc, "as_of_utc")
        _require_utc(self.information_cutoff_utc, "information_cutoff_utc")
        if self.information_cutoff_utc > self.as_of_utc:
            raise ValueError("information cutoff cannot be after as-of time")
        for name in ("run_id", "policy_version", "parameter_set_id", "code_revision"):
            if not getattr(self, name).strip():
                raise ValueError(f"{name} cannot be blank")

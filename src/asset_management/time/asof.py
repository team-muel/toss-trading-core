from dataclasses import dataclass
from datetime import datetime, timezone

from asset_management.domain.errors import TemporalViolation


def _require_utc(value: datetime, name: str) -> None:
    if value.tzinfo is None or value.utcoffset() != timezone.utc.utcoffset(None):
        raise TemporalViolation(f"{name} must be timezone-aware UTC")


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
            raise TemporalViolation("information cutoff cannot be after as-of time")
        for name in ("run_id", "policy_version", "parameter_set_id", "code_revision"):
            if not getattr(self, name).strip():
                raise ValueError(f"{name} cannot be blank")

    def require_known_at(self, available_at: datetime, *, label: str = "information") -> None:
        """Reject information that was not knowable at this run's cutoff."""

        _require_utc(available_at, "available_at")
        if available_at > self.information_cutoff_utc:
            raise TemporalViolation(
                f"{label} became available after information_cutoff_utc"
            )


def require_as_of_context(context: AsOfContext) -> AsOfContext:
    """Shared calculation-boundary guard; a missing context must never be inferred."""

    if not isinstance(context, AsOfContext):
        raise TemporalViolation("an explicit AsOfContext is required")
    return context

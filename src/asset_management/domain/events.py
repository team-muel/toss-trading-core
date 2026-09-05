from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Mapping
from uuid import UUID, uuid4


@dataclass(frozen=True, slots=True)
class DomainEvent:
    event_type: str
    aggregate_id: str
    payload: Mapping[str, object]
    occurred_at_utc: datetime
    event_id: UUID = field(default_factory=uuid4)

    def __post_init__(self) -> None:
        if self.occurred_at_utc.tzinfo is None or self.occurred_at_utc.utcoffset() != timezone.utc.utcoffset(None):
            raise ValueError("occurred_at_utc must be timezone-aware UTC")

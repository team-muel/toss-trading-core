from dataclasses import dataclass
from datetime import datetime
from typing import Mapping


@dataclass(frozen=True, slots=True)
class PolicyRecord:
    kind: str
    version: str
    effective_from: datetime
    effective_to: datetime | None
    approved_by: str
    approval_reason: str
    values: Mapping[str, object]
    content_hash: str


@dataclass(frozen=True, slots=True)
class PolicyBundle:
    investment: PolicyRecord
    risk: PolicyRecord
    execution: PolicyRecord
    tax: PolicyRecord

from dataclasses import dataclass
from datetime import datetime
from typing import Mapping


@dataclass(frozen=True, slots=True)
class PolicyDescriptor:
    kind: str
    version: str
    status: str
    document: str
    document_hash: str
    effective_from: datetime | None
    effective_to: datetime | None
    approved_by: str | None

    def is_effective(self, at: datetime) -> bool:
        return (
            self.status == "ACCEPTED"
            and self.approved_by is not None
            and self.effective_from is not None
            and self.effective_from <= at
            and (self.effective_to is None or at < self.effective_to)
        )


@dataclass(frozen=True, slots=True)
class PolicyRegistry:
    version: str
    live_trading_enabled: bool
    policies: Mapping[str, PolicyDescriptor]

    def require_effective(self, kinds: tuple[str, ...], at: datetime) -> None:
        from asset_management.domain.errors import ConfigurationError

        invalid = [kind for kind in kinds if kind not in self.policies or not self.policies[kind].is_effective(at)]
        if invalid:
            raise ConfigurationError(f"policies are not approved and effective: {invalid}")


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

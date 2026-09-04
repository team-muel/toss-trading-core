from dataclasses import dataclass

from .errors import InvariantViolation


@dataclass(frozen=True, slots=True)
class Identifier:
    value: str

    def __post_init__(self) -> None:
        normalized = self.value.strip()
        if not normalized:
            raise InvariantViolation(f"{type(self).__name__} cannot be blank")
        object.__setattr__(self, "value", normalized)

    def __str__(self) -> str:
        return self.value


class AccountId(Identifier):
    pass


class InstrumentId(Identifier):
    pass


class OrderId(Identifier):
    pass


class RunId(Identifier):
    pass


class IngestionRunId(Identifier):
    pass


class DatasetManifestId(Identifier):
    pass


class FeatureRunId(Identifier):
    pass


class StateRunId(Identifier):
    pass


class PricingRunId(Identifier):
    pass


class ExpectationRunId(Identifier):
    pass


class RiskModelRunId(Identifier):
    pass


class PortfolioTargetId(Identifier):
    pass


class RiskDecisionId(Identifier):
    pass


class OrderIntentId(Identifier):
    pass


class ClientOrderId(Identifier):
    pass


class ExecutionId(Identifier):
    pass


class ReconciliationRunId(Identifier):
    pass

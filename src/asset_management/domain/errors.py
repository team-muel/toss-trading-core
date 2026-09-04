class AssetManagementError(Exception):
    """Base application error."""


class InvariantViolation(AssetManagementError):
    """A system invariant was violated."""


class NoTrade(AssetManagementError):
    """Fail-closed outcome: no order may be produced."""


class ReconciliationRequired(NoTrade):
    """Broker and internal ledger are not reconciled."""


class ConfigurationError(AssetManagementError):
    """Configuration is incomplete, inconsistent, or unapproved."""


class TemporalViolation(AssetManagementError):
    """A time or point-in-time invariant was violated."""


class DataQualityError(NoTrade):
    """Input data is not eligible for a decision."""


class ReconciliationError(ReconciliationRequired):
    """Account or ledger reconciliation failed."""


class RiskLimitError(NoTrade):
    """A policy-derived risk limit was breached or invalid."""


class ExecutionError(NoTrade):
    """An execution operation failed safely."""


class UnknownBrokerState(ReconciliationError):
    """The broker returned a state the application cannot classify."""

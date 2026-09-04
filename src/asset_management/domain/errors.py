class AssetManagementError(Exception):
    """Base application error."""


class InvariantViolation(AssetManagementError):
    """A system invariant was violated."""


class NoTrade(AssetManagementError):
    """Fail-closed outcome: no order may be produced."""


class ReconciliationRequired(NoTrade):
    """Broker and internal ledger are not reconciled."""

"""Operational state only; this engine cannot create investment signals."""

from .engine import StateEngine
from .models import StateType

SYSTEM_COMPONENTS = ("broker_health", "data_health", "clock_health", "reconciliation_health",
                     "storage_health", "model_health", "execution_health")


class SystemStateEngine(StateEngine):
    def __init__(self) -> None:
        super().__init__(state_type=StateType.SYSTEM, component_names=SYSTEM_COMPONENTS)

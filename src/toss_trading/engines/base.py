from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class Signal:
    engine: str
    symbol_or_pair: str
    side: str
    raw_score: float
    adjusted_score: float | None
    target_weight: float
    expected_max_loss: float
    reason_code: str


class SignalEngine(Protocol):
    name: str

    def generate(self, context: dict) -> list[Signal]:
        ...

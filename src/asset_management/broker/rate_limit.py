"""Broker-specific rate-limit policies; implementation is adapted from toss_trading.runtime."""

from toss_trading.runtime.rate_limit import PriorityTokenBucket, TokenBucket
from dataclasses import dataclass
from enum import IntEnum
from typing import Iterable


class ReadPriority(IntEnum):
    EXISTING_ORDER_STATE = 0
    CANCEL_OR_MODIFY_STATE = 1
    ACCOUNT_RECONCILIATION = 2
    NEW_MARKET_DATA = 3


@dataclass(frozen=True, slots=True)
class PrioritizedRead:
    priority: ReadPriority
    endpoint: str
    sequence: int


def priority_order(requests: Iterable[PrioritizedRead]) -> tuple[PrioritizedRead, ...]:
    return tuple(sorted(requests, key=lambda item: (item.priority, item.sequence)))

__all__ = ["PrioritizedRead", "PriorityTokenBucket", "ReadPriority", "TokenBucket", "priority_order"]

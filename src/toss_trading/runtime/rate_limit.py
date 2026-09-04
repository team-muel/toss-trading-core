from __future__ import annotations

import time
import heapq
from itertools import count
from threading import Condition, Lock
from dataclasses import dataclass


@dataclass
class TokenBucket:
    capacity: float
    refill_per_second: float
    tokens: float | None = None
    updated_at: float | None = None
    _lock: Lock | None = None

    def __post_init__(self) -> None:
        if self.capacity <= 0:
            raise ValueError("capacity must be positive")
        if self.refill_per_second <= 0:
            raise ValueError("refill_per_second must be positive")
        now = time.monotonic()
        if self.tokens is None:
            self.tokens = self.capacity
        if self.updated_at is None:
            self.updated_at = now
        if self._lock is None:
            self._lock = Lock()

    def acquire(self, tokens: float = 1.0) -> float:
        if tokens <= 0:
            raise ValueError("tokens must be positive")
        waited = 0.0
        while True:
            wait_seconds = self.try_acquire(tokens)
            if wait_seconds is None:
                return waited
            time.sleep(wait_seconds)
            waited += wait_seconds

    def try_acquire(self, tokens: float = 1.0) -> float | None:
        if tokens <= 0:
            raise ValueError("tokens must be positive")
        with self._lock or Lock():
            now = time.monotonic()
            elapsed = now - float(self.updated_at)
            self.tokens = min(
                self.capacity,
                float(self.tokens) + elapsed * self.refill_per_second,
            )
            self.updated_at = now
            if self.tokens >= tokens:
                self.tokens -= tokens
                return None
            return (tokens - self.tokens) / self.refill_per_second

    def update_from_headers(self, headers: dict[str, str]) -> None:
        lower = {key.lower(): value for key, value in headers.items()}
        remaining = lower.get("x-ratelimit-remaining")
        limit = lower.get("x-ratelimit-limit")
        with self._lock or Lock():
            if limit:
                try:
                    self.capacity = max(1.0, float(limit))
                except ValueError:
                    pass
            if remaining:
                try:
                    self.tokens = max(0.0, min(self.capacity, float(remaining)))
                except ValueError:
                    pass

    @staticmethod
    def retry_after_seconds(headers: dict[str, str]) -> float | None:
        for key, value in headers.items():
            if key.lower() == "retry-after":
                try:
                    return max(0.0, float(value))
                except ValueError:
                    return None
        return None


class PriorityTokenBucket:
    """Serialize contenders by priority before consuming a shared group bucket."""

    def __init__(self, bucket: TokenBucket) -> None:
        self.bucket = bucket
        self._condition = Condition()
        self._sequence = count()
        self._queue: list[tuple[int, int, object]] = []

    def acquire(self, priority: int, tokens: float = 1.0) -> float:
        marker = object()
        queued = (priority, next(self._sequence), marker)
        with self._condition:
            heapq.heappush(self._queue, queued)
            waited = 0.0
            while True:
                if self._queue[0][2] is marker:
                    wait_seconds = self.bucket.try_acquire(tokens)
                    if wait_seconds is None:
                        heapq.heappop(self._queue)
                        self._condition.notify_all()
                        return waited
                    timeout = wait_seconds
                else:
                    timeout = None
                before = time.monotonic()
                self._condition.wait(timeout=timeout)
                waited += time.monotonic() - before

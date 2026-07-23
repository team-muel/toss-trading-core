from __future__ import annotations

import time
from threading import Lock
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
                    return waited
                needed = tokens - self.tokens
                wait_seconds = needed / self.refill_per_second
            time.sleep(wait_seconds)
            waited += wait_seconds

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

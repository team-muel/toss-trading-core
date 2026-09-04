"""Replay verified immutable raw responses without network access."""

from dataclasses import dataclass

from asset_management.data.raw_store import RawApiResponse, SQLiteRawResponseStore


@dataclass(frozen=True, slots=True)
class RawReplayResult:
    raw_response_ids: tuple[str, ...]
    responses: tuple[RawApiResponse, ...]


class RawReplayEngine:
    def __init__(self, store: SQLiteRawResponseStore) -> None:
        self._store = store

    def replay(self, raw_response_ids: tuple[str, ...]) -> RawReplayResult:
        if not raw_response_ids:
            raise ValueError("replay requires raw response evidence")
        return RawReplayResult(
            raw_response_ids,
            tuple(self._store.verified(identifier) for identifier in raw_response_ids),
        )

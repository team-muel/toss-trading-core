from dataclasses import dataclass
import hashlib
import json
from typing import Mapping

from asset_management.domain.enums import DataStatus


@dataclass(frozen=True, slots=True)
class ConsistencyResult:
    status: DataStatus
    differing_sections: tuple[str, ...]


def compare_repeated_reads(
    first: Mapping[str, object], second: Mapping[str, object]
) -> ConsistencyResult:
    sections = sorted(set(first) | set(second))
    differing = tuple(
        section for section in sections if _digest(first.get(section)) != _digest(second.get(section))
    )
    return ConsistencyResult(DataStatus.CONFLICT if differing else DataStatus.KNOWN, differing)


def _digest(value: object) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()

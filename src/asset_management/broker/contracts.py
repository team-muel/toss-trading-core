from dataclasses import dataclass
from enum import Enum
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Mapping, Sequence, TypeVar

from asset_management.domain.enums import DataStatus
from asset_management.domain.errors import DataQualityError, UnknownBrokerState


E = TypeVar("E", bound=Enum)


@dataclass(frozen=True, slots=True)
class ParsedEnum:
    status: DataStatus
    value: Enum | None
    raw_value: str


def parse_broker_enum(value: object, enum_type: type[E]) -> ParsedEnum:
    raw = str(value)
    try:
        return ParsedEnum(DataStatus.KNOWN, enum_type(raw), raw)
    except ValueError:
        return ParsedEnum(DataStatus.UNKNOWN, None, raw)


def require_result(body: Any, endpoint: str) -> Any:
    if not isinstance(body, dict) or "result" not in body:
        raise DataQualityError(f"{endpoint}: required result field is missing")
    return body["result"]


def require_fields(value: Any, fields: Sequence[str], endpoint: str) -> Mapping[str, Any]:
    if not isinstance(value, dict):
        raise DataQualityError(f"{endpoint}: expected object")
    missing = [field for field in fields if field not in value]
    if missing:
        raise DataQualityError(f"{endpoint}: missing required fields {missing}")
    return value


def require_pagination(result: Any, endpoint: str) -> tuple[bool, str | None]:
    page = require_fields(result, ("hasNext", "nextCursor"), endpoint)
    if not isinstance(page["hasNext"], bool):
        raise DataQualityError(f"{endpoint}: hasNext must be boolean")
    cursor = page["nextCursor"]
    if cursor is not None and not isinstance(cursor, str):
        raise DataQualityError(f"{endpoint}: nextCursor must be string or null")
    if page["hasNext"] and not cursor:
        raise DataQualityError(f"{endpoint}: paginated response has no next cursor")
    return page["hasNext"], cursor


def require_known_broker_enum(parsed: ParsedEnum, field: str) -> Enum:
    if parsed.status is not DataStatus.KNOWN or parsed.value is None:
        raise UnknownBrokerState(f"unknown broker enum for {field}: {parsed.raw_value}")
    return parsed.value


def require_decimal_string(value: object, field: str) -> Decimal:
    if not isinstance(value, str):
        raise DataQualityError(f"{field}: broker decimal must be a string")
    try:
        parsed = Decimal(value)
    except InvalidOperation as exc:
        raise DataQualityError(f"{field}: invalid decimal") from exc
    if not parsed.is_finite():
        raise DataQualityError(f"{field}: decimal must be finite")
    return parsed


def require_timestamp(value: object, field: str) -> datetime:
    if not isinstance(value, str):
        raise DataQualityError(f"{field}: timestamp must be a string")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise DataQualityError(f"{field}: invalid ISO-8601 timestamp") from exc
    if parsed.tzinfo is None:
        raise DataQualityError(f"{field}: timestamp must include an offset")
    return parsed

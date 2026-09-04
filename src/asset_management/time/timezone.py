from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from asset_management.domain.errors import TemporalViolation

KST = ZoneInfo("Asia/Seoul")
US_EASTERN = ZoneInfo("America/New_York")


def display_in(value: datetime, zone: ZoneInfo) -> datetime:
    if value.tzinfo is None:
        raise TemporalViolation("cannot convert a naive datetime")
    return value.astimezone(zone)


def utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise TemporalViolation("cannot convert a naive datetime")
    return value.astimezone(timezone.utc)

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

KST = ZoneInfo("Asia/Seoul")
US_EASTERN = ZoneInfo("America/New_York")


def display_in(value: datetime, zone: ZoneInfo) -> datetime:
    if value.tzinfo is None:
        raise ValueError("cannot convert a naive datetime")
    return value.astimezone(zone)


def utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise ValueError("cannot convert a naive datetime")
    return value.astimezone(timezone.utc)

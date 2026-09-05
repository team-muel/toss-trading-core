"""Explicit exchange sessions; unknown dates never imply an open session."""
from datetime import date
from zoneinfo import ZoneInfo
from asset_management.domain.errors import DataQualityError
from asset_management.time.timezone import utc
from .history import ReferenceHistory


class SessionRepository(ReferenceHistory):
    def record(self, *, exchange, local_date, timezone, session_status,
               regular_open=None, regular_close=None, premarket_open=None,
               afterhours_close=None, early_close=False, **history):
        day, zone = date.fromisoformat(local_date), ZoneInfo(timezone)
        times = dict(regular_open=regular_open, regular_close=regular_close,
                     premarket_open=premarket_open, afterhours_close=afterhours_close)
        if session_status not in {'OPEN', 'CLOSED'} or type(early_close) is not bool:
            raise DataQualityError('SESSION_STATUS_UNKNOWN')
        if session_status == 'CLOSED':
            if any(value is not None for value in times.values()) or early_close:
                raise DataQualityError('CLOSED_SESSION_HAS_TRADING_HOURS')
        else:
            if regular_open is None or regular_close is None:
                raise DataQualityError('SESSION_HOURS_MISSING')
            converted = {key: utc(value) for key, value in times.items() if value is not None}
            if any(value.astimezone(zone).date() != day for value in converted.values()):
                raise DataQualityError('SESSION_LOCAL_DATE_CONFLICT')
            if not converted['regular_open'] < converted['regular_close']:
                raise DataQualityError('SESSION_HOURS_REVERSED')
            if premarket_open is not None and utc(premarket_open) > utc(regular_open):
                raise DataQualityError('PREMARKET_AFTER_OPEN')
            if afterhours_close is not None and utc(afterhours_close) < utc(regular_close):
                raise DataQualityError('AFTERHOURS_BEFORE_CLOSE')
        body = dict(exchange=exchange, local_date=local_date, timezone=timezone,
                    session_status=session_status, early_close=early_close,
                    **{key: utc(value).isoformat() if value is not None else None
                       for key, value in times.items()})
        return self.append('SESSION', exchange + ':' + local_date, body, **history)

    def session(self, exchange, local_date, context):
        row = self.active('SESSION', context).get(exchange + ':' + local_date)
        if row is None:
            raise DataQualityError('SESSION_MISSING')
        return row

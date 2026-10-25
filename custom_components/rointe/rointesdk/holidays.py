"""Timezone-aware holiday periods for the installation-level Nexa API."""

from datetime import datetime, timezone


def holiday_payload(periods: list[dict]) -> list[dict]:
    """Convert aware datetimes to the API's UTC strings; never guess a zone."""
    if not isinstance(periods, list):
        raise ValueError("Holiday periods must be a list")
    result = []
    previous_end = None
    for index, period in enumerate(periods):
        if not isinstance(period, dict) or set(period) - {"from", "to"} or "from" not in period:
            raise ValueError("Each holiday period needs from and optional to")
        def parse(value):
            value = datetime.fromisoformat(value) if isinstance(value, str) else value
            if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
                raise ValueError("Holiday times must include a timezone offset")
            return value.astimezone(timezone.utc)
        start = parse(period["from"])
        end = parse(period["to"]) if period.get("to") is not None else None
        if end is None and index != len(periods) - 1:
            raise ValueError("Only the last holiday period can be indefinite")
        if end is not None and end <= start:
            raise ValueError("Holiday end must follow its start")
        if previous_end is not None and start < previous_end:
            raise ValueError("Holiday periods must be ordered and not overlap")
        result.append({"from": start.strftime("%Y-%m-%d %H:%M:%S"),
                       "to": end.strftime("%Y-%m-%d %H:%M:%S") if end else None})
        previous_end = end
    return result

"""Human-readable schedule details for Nexa and Connect devices."""

from __future__ import annotations

from datetime import timedelta, timezone
from itertools import groupby

from .rointesdk import utils

MODES = {"C": "Comfort", "E": "Eco", "O": "Off", "I": "Ice"}


def day_schedule(schedule, weekday: int) -> str | None:
    """Read a validated daily schedule from either supported API shape."""
    if isinstance(schedule, dict):
        value = schedule.get(str(weekday), schedule.get(weekday))
    elif isinstance(schedule, list) and weekday < len(schedule):
        value = schedule[weekday]
    else:
        return None
    if not isinstance(value, str) or len(value) != 24:
        return None
    value = value.upper()
    return value if all(char in MODES for char in value) else None


def hour_ranges(schedule: str, mode: str) -> str:
    """Format consecutive inclusive hour ranges, matching the older integration."""
    ranges = []
    for char, group in groupby(enumerate(schedule), key=lambda item: item[1]):
        hours = [hour for hour, _ in group]
        if char == mode:
            ranges.append(
                str(hours[0]) if len(hours) == 1 else f"{hours[0]}-{hours[-1]}"
            )
    return ", ".join(ranges) if ranges else "None"


def schedule_attributes(device) -> dict | None:
    """Show today's periods and the next known change, including across midnight."""
    now = utils.now()
    today = day_schedule(device.schedule, now.weekday())
    if today is None:
        return None
    current = today[now.hour]
    next_change = None
    next_mode = None
    # Advance through actual hours so daylight-saving transitions do not invent
    # a nonexistent local hour or skip the repeated hour when clocks go back.
    start = now.replace(minute=0, second=0, microsecond=0).astimezone(timezone.utc)
    for step in range(1, 170):
        candidate = (start + timedelta(hours=step)).astimezone(now.tzinfo)
        schedule = day_schedule(device.schedule, candidate.weekday())
        if schedule is None:
            break  # Unknown intervening hours cannot promise a next change.
        if schedule[candidate.hour] != current:
            next_change = candidate
            next_mode = MODES[schedule[candidate.hour]]
            break
    return {
        "current_hour": now.hour,
        "current_mode": MODES[current],
        "today_schedule": today,
        "schedule_active": device.power and device.mode == "auto",
        "timezone": str(now.tzinfo),
        "next_change_hour": next_change.hour if next_change else None,
        "next_change_mode": next_mode,
        "next_change_at": next_change.isoformat() if next_change else None,
        "eco_hours": hour_ranges(today, "E"),
        "comfort_hours": hour_ranges(today, "C"),
        "ice_hours": hour_ranges(today, "I"),
        "off_hours": hour_ranges(today, "O"),
    }

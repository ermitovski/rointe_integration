"""Validate optional controls against the public Nexa app's field rules."""

from __future__ import annotations

import math

DISPLAY_FIELDS = ("backlight", "backlight_on", "backlight_time", "buzzer")
NIGHT_FIELDS = ("dont_disturb_mode", "dont_disturb_start", "dont_disturb_end", "dont_disturb_decrease")
OPTIONAL_FIELDS = frozenset((*DISPLAY_FIELDS, *NIGHT_FIELDS, "adelanto_enable"))
DISPLAY_TIMEOUT_OPTIONS = {"Always on": 0, "10 seconds": 10, "30 seconds": 30}
# Nexa's app defaults to 15 even though its buttons only offer 0, 10 and 30.
# Live devices also report 15. Recognise it without inventing a 15 -> 30 alias
# or offering an unverified new command to change a different value to 15.
REPORTED_DISPLAY_TIMEOUT_OPTIONS = {
    "Always on": 0, "10 seconds": 10, "15 seconds": 15, "30 seconds": 30,
}


def display_timeout_option(value) -> str | None:
    """Interpret a reported timeout without guessing missing or invalid values."""
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        return None
    try:
        value = float(value)
    except (ValueError, OverflowError):
        return None
    return next((name for name, seconds in REPORTED_DISPLAY_TIMEOUT_OPTIONS.items()
                 if seconds == value), None)


def prepare_optional_settings(device, changes: dict) -> dict:
    """Preserve reported companion values and reject invalid optional updates."""
    if set(changes).intersection(OPTIONAL_FIELDS):
        power = device._data.get("power")
        if not isinstance(power, bool) and power == 0:
            raise ValueError("The device is switched off at the unit")
        restricted = {"adelanto_enable", *NIGHT_FIELDS}
        if set(changes).intersection(restricted) and (device.user_mode or device.pilot_mode):
            raise ValueError("Disable limited mode and pilot mode before changing early start or night mode")
    payload = dict(changes)
    for key in changes:
        if not device.supports_setting(key):
            raise ValueError(f"Setting {key} is not supported by this device")
    for fields in (DISPLAY_FIELDS, NIGHT_FIELDS):
        if set(changes).intersection(fields):
            for key in fields:
                if device.supports_setting(key):
                    payload.setdefault(key, device._data[key])
    for key, value in payload.items():
        if key not in OPTIONAL_FIELDS:
            continue
        if key in {"adelanto_enable", "dont_disturb_mode", "dont_disturb_decrease"}:
            if not isinstance(value, bool):
                raise ValueError(f"{key} must be a boolean")
            continue
        if isinstance(value, bool):
            raise ValueError(f"{key} must be an integer")
        number = float(value)
        if not math.isfinite(number) or not number.is_integer():
            raise ValueError(f"{key} must be a finite integer")
        number = int(number)
        if key == "backlight_time":
            valid = number in DISPLAY_TIMEOUT_OPTIONS.values() or (
                key not in changes and number == 15
            )
        elif key in {"dont_disturb_start", "dont_disturb_end"}:
            valid = 0 <= number <= 86399
        else:
            valid = (1 if key == "backlight_on" else 0) <= number <= 10
        if not valid:
            raise ValueError(f"Invalid value for {key}")
        payload[key] = number
    return payload

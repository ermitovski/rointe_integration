"""Validate optional controls against the public Nexa app's field rules."""

from __future__ import annotations

import math

DISPLAY_FIELDS = ("backlight", "backlight_on", "backlight_time", "buzzer")
NIGHT_FIELDS = ("dont_disturb_mode", "dont_disturb_start", "dont_disturb_end", "dont_disturb_decrease")
OPTIONAL_FIELDS = frozenset((*DISPLAY_FIELDS, *NIGHT_FIELDS, "adelanto_enable"))


def prepare_optional_settings(device, changes: dict) -> dict:
    """Preserve reported companion values and reject invalid optional updates."""
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
            valid = number in {0, 10, 30}
        elif key in {"dont_disturb_start", "dont_disturb_end"}:
            valid = 0 <= number <= 86399
        else:
            valid = (1 if key == "backlight_on" else 0) <= number <= 10
        if not valid:
            raise ValueError(f"Invalid value for {key}")
        payload[key] = number
    return payload

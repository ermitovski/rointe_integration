"""Storage-heater charge controls, validated against Nexa's app limits."""

import math

CHARGE_LIMITS = ("charge_percentage_min", "charge_percentage_limit")
EXTRA_SETTINGS = (
    "charge_smart",
    "silence_mode",
    "res_auto_config",
    "res_man_config",
    "res_boost_config",
    "res_holiday_config",
)
STORAGE_FIELDS = frozenset(
    (*CHARGE_LIMITS, *EXTRA_SETTINGS, "additional_charge", "schedule_charging")
)


def prepare_storage_settings(device, commanded, changes):
    """Validate fresh zone state and preserve paired settings without defaults."""
    power = commanded.get("power", device._data.get("power"))
    if not isinstance(power, bool) and power == 0:
        raise ValueError("The device is switched off at the unit")
    if not changes or set(changes) - STORAGE_FIELDS:
        raise ValueError("Unsupported storage setting")
    for key in changes:
        if (
            not device.supports_setting(key)
            or key not in commanded
            or commanded[key] is None
        ):
            raise ValueError(f"Storage setting {key} is unavailable")
    payload = dict(changes)
    for group in (CHARGE_LIMITS, EXTRA_SETTINGS):
        if set(group).intersection(changes):
            for key in group:
                if key in commanded and device.supports_setting(key):
                    payload.setdefault(key, commanded[key])
    for key, value in payload.items():
        if key == "schedule_charging":
            continue  # Validated by the same day/hour validator as the heating schedule.
        if key in EXTRA_SETTINGS:
            if not isinstance(value, bool):
                raise ValueError(f"{key} must be a boolean")
            continue
        number = float(value)
        if (
            isinstance(value, bool)
            or not math.isfinite(number)
            or not number.is_integer()
        ):
            raise ValueError(f"{key} must be a finite integer")
        number = int(number)
        low, high, step = {
            "charge_percentage_min": (0, 30, 5),
            "charge_percentage_limit": (60, 100, 5),
            "additional_charge": (0, 7, 1),
        }[key]
        if not low <= number <= high or number % step:
            raise ValueError(f"{key} must be {low}–{high} in steps of {step}")
        payload[key] = number
    if set(CHARGE_LIMITS).intersection(changes):
        if not all(key in payload for key in CHARGE_LIMITS):
            raise ValueError("Both charge limits must be available")
        if payload[CHARGE_LIMITS[0]] >= payload[CHARGE_LIMITS[1]]:
            raise ValueError("Minimum charge must be below maximum charge")
    if "schedule_charging" in payload:
        payload["schedule_charge_type"] = 0  # Select custom programming.
    return payload

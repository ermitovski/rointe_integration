"""Installed-power adjustment observed in Nexa's public web app."""

from __future__ import annotations

import math

# DeviceConfig.getConfigRadiator, Nexa build 8b8da08c (2026-10-25).
POWER_ADJUSTABLE_MODELS = frozenset({20, 21, 22, 23, 25, 26, 27, 28, 29, 30, 31, 32})


def positive_watts(value) -> float:
    """Reject missing/invalid ratings instead of manufacturing a nominal power."""
    if isinstance(value, bool):
        raise ValueError("Power ratings must be finite positive numbers")
    watts = float(value)
    if not math.isfinite(watts) or watts <= 0:
        raise ValueError("Power ratings must be finite positive numbers")
    return watts


def supports_power_reduction(data: dict) -> bool:
    """A shared firmware field alone does not establish support."""
    identifiers = (
        data.get("product_type"),
        data.get("product_brand"),
        data.get("product_model"),
    )
    if not all(
        isinstance(value, int) and not isinstance(value, bool) for value in identifiers
    ):
        return False
    if identifiers[:2] != (1, 1) or identifiers[2] not in POWER_ADJUSTABLE_MODELS:
        return False
    try:
        positive_watts(data.get("nominal_power"))
        positive_watts(data.get("nominal_effective_power"))
    except (TypeError, ValueError, OverflowError):
        return False
    return True


def validate_reduction(value) -> float:
    """Use the seven choices offered by Nexa: 0, 5, …, 30 percent."""
    if isinstance(value, bool):
        raise ValueError("Power reduction must be 0–30% in steps of 5%")
    reduction = float(value)
    if not math.isfinite(reduction) or not 0 <= reduction <= 30 or reduction % 5:
        raise ValueError("Power reduction must be 0–30% in steps of 5%")
    return reduction


def power_reduction_payload(data: dict, reduction) -> dict:
    """Always calculate from nominal rating, never compound an earlier reduction."""
    reduction = validate_reduction(reduction)
    if not supports_power_reduction(data):
        raise ValueError(
            "This device does not report supported power-adjustment settings"
        )
    return {
        "nominal_effective_power": round(
            positive_watts(data["nominal_power"]) * (100 - reduction) / 100, 6
        )
    }


def reduction_percentage(data: dict) -> float | None:
    """Report the configured reduction without pretending it measures consumption."""
    if not supports_power_reduction(data):
        return None
    return round(
        100
        * (
            1
            - positive_watts(data["nominal_effective_power"])
            / positive_watts(data["nominal_power"])
        ),
        6,
    )

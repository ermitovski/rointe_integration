"""Calendar-based accumulation of daily API readings."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
import math


@dataclass
class DailyEnergyTotal:
    """Keep a monotonic observed total without treating corrections as resets."""

    period: date | None = None
    offset: float = 0.0
    daily: float = 0.0

    @property
    def value(self) -> float | None:
        return round(self.offset + self.daily, 4) if self.period is not None else None

    def update(self, period: date, value: float) -> float:
        value = float(value)
        if not math.isfinite(value) or value < 0:
            raise ValueError("Energy totals must be finite and non-negative")
        if self.period is not None and period < self.period:
            return self.value  # Ignore stale responses for previous days.
        if self.period is not None and period > self.period:
            self.offset += self.daily
            self.daily = 0.0
        self.period = period
        self.daily = max(self.daily, value)
        return self.value

    def attributes(self) -> dict:
        return {
            "energy_period_date": self.period.isoformat() if self.period else None,
            "cumulative_offset": self.offset,
            "last_daily_value": self.daily,
        }

    @classmethod
    def restore(cls, attributes: dict) -> DailyEnergyTotal:
        """Restore validated internal counters, independently of display units."""
        try:
            period = date.fromisoformat(attributes["energy_period_date"])
            offset = float(attributes["cumulative_offset"])
            daily = float(attributes["last_daily_value"])
            if any(not math.isfinite(value) or value < 0 for value in (offset, daily)):
                raise ValueError("Invalid restored counters")
            return cls(period, offset, daily)
        except (KeyError, ValueError, TypeError, OverflowError):
            return cls()

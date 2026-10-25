"""Schedule attributes work across API formats, week boundaries and DST."""

from datetime import datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest
from custom_components.rointe.schedule import schedule_attributes
from custom_components.rointe.rointesdk import utils


def details(monkeypatch, when, schedule):
    monkeypatch.setattr(utils, "now", lambda: when)
    return schedule_attributes(
        SimpleNamespace(schedule=schedule, power=True, mode="auto")
    )


@pytest.mark.parametrize("format", ["dict", "list"])
def test_today_periods_and_next_change(monkeypatch, format):
    day = "O" * 6 + "C" * 3 + "E" * 11 + "C" * 2 + "O" * 2
    week = {str(d): day for d in range(7)} if format == "dict" else [day] * 7
    result = details(
        monkeypatch,
        datetime(2026, 10, 26, 7, 30, tzinfo=ZoneInfo("Europe/Madrid")),
        week,
    )
    assert result["current_mode"] == "Comfort"
    assert result["comfort_hours"] == "6-8, 20-21"
    assert result["eco_hours"] == "9-19"
    assert result["off_hours"] == "0-5, 22-23"
    assert result["next_change_hour"] == 9
    assert result["next_change_mode"] == "Eco"
    assert result["next_change_at"] == "2026-10-26T09:00:00+01:00"


def test_next_change_crosses_sunday_midnight(monkeypatch):
    week = {str(day): "O" * 24 for day in range(7)}
    week["0"] = "C" * 24
    result = details(
        monkeypatch,
        datetime(2026, 10, 25, 23, 30, tzinfo=ZoneInfo("Europe/Madrid")),
        week,
    )
    assert result["next_change_at"] == "2026-10-26T00:00:00+01:00"
    assert result["next_change_mode"] == "Comfort"


def test_dst_spring_forward_skips_nonexistent_hour(monkeypatch):
    week = {str(day): "O" * 24 for day in range(7)}
    week["6"] = "OO" + "C" * 22
    result = details(
        monkeypatch,
        datetime(2026, 3, 29, 1, 30, tzinfo=ZoneInfo("Europe/Madrid")),
        week,
    )
    assert result["next_change_at"] == "2026-03-29T03:00:00+02:00"


def test_dst_fall_back_does_not_predict_change_in_repeated_hour(monkeypatch):
    week = {str(day): "O" * 24 for day in range(7)}
    week["6"] = "OOO" + "C" * 21
    result = details(
        monkeypatch,
        datetime(2026, 10, 25, 2, 30, fold=0, tzinfo=ZoneInfo("Europe/Madrid")),
        week,
    )
    assert result["next_change_at"] == "2026-10-25T03:00:00+01:00"


@pytest.mark.parametrize(
    "schedule", [None, {}, [], {"0": None}, {"0": "short"}, {"0": "X" * 24}]
)
def test_invalid_schedules_have_no_attributes(monkeypatch, schedule):
    assert (
        details(
            monkeypatch,
            datetime(2026, 10, 26, tzinfo=ZoneInfo("Europe/Madrid")),
            schedule,
        )
        is None
    )


def test_constant_week_and_missing_days_do_not_invent_change(monkeypatch):
    when = datetime(2026, 10, 26, 23, 30, tzinfo=ZoneInfo("Europe/Madrid"))
    week = {str(day): "E" * 24 for day in range(7)}
    assert details(monkeypatch, when, week)["next_change_at"] is None
    assert (
        details(monkeypatch, when, {"0": "E" * 24, "2": "C" * 24})["next_change_at"]
        is None
    )


def test_schedule_can_be_inspected_in_manual_mode(monkeypatch):
    monkeypatch.setattr(
        utils, "now", lambda: datetime(2026, 10, 26, tzinfo=ZoneInfo("Europe/Madrid"))
    )
    result = schedule_attributes(
        SimpleNamespace(schedule=["O" * 24] * 7, power=True, mode="manual")
    )
    assert not result["schedule_active"]
    assert result["current_mode"] == "Off"

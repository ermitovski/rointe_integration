"""Allowlisted fields consumed by the integration, with diagnostic constraints.

These describe received values, not permission to send commands. Coverage tests
compare this catalogue with the device model and the entity control descriptions.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class Field:
    kind: str
    minimum: float | None = None
    maximum: float | None = None
    step: float | None = None
    attribute: str | None = None
    private: bool = False


DEVICE_FIELDS = {
    **{key: Field("boolean") for key in (
        "adelanto_enable", "block_local", "block_remote", "boost_active",
        "charging", "charge_smart", "dont_disturb_mode", "dont_disturb_decrease",
        "has_floor_probe", "ice_mode", "is_alive", "legionella_status",
        "pilot_mode", "pir_mode", "silence_mode", "timer_mode", "use_floor_probe",
        "user_mode", "windows_open_mode", "windows_open_status", "res_auto_config",
        "res_man_config", "res_boost_config", "res_holiday_config",
    )},
    **{key: Field("number") for key in (
        "temp", "temp_calc", "temp_floor", "temp_probe", "temp_surface", "timer_temp",
        "timer_config_temp",
    )},
    **{key: Field("integer", 0) for key in (
        "boost_countdown", "timer_time", "timer_config_time", "legionella_mode",
        "product_brand", "product_model", "product_type",
    )},
    "type": Field("string"),
    "product_version": Field("string"),
    "name": Field("string"),
    "power": Field("power"),
    "mode": Field("mode"),
    "status": Field("string", attribute="preset"),
    "status_warming": Field("integer", 0, 2),
    "comfort": Field("number", 7, 30, 0.5, "comfort_temp"),
    "eco": Field("number", 7, 30, 0.5, "eco_temp"),
    "ice": Field("number", 5, 15, 0.5, "ice_temp"),
    "um_min_temp": Field("number", 5, 20, 1),
    "um_max_temp": Field("number", 15, 35, 1),
    "nominal_power": Field("number", 0),
    "nominal_effective_power": Field("number", 0),
    "wifisignal": Field("integer", attribute="wifi_signal"),
    "wifissid": Field("string", attribute="wifi_ssid", private=True),
    "backlight": Field("integer", 0, 10),
    "backlight_on": Field("integer", 1, 10),
    "backlight_time": Field("timeout"),
    "buzzer": Field("integer", 0, 10),
    "ledbar_on": Field("integer"),
    "dont_disturb_start": Field("wall_time", 0, 86399),
    "dont_disturb_end": Field("wall_time", 0, 86399),
    "charge_percentage": Field("integer", 0, 100),
    "charge_percentage_min": Field("integer", 0, 30, 5),
    "charge_percentage_limit": Field("integer", 60, 100, 5),
    "additional_charge": Field("integer", 0, 7),
    "schedule": Field("schedule"),
    "schedule_charging": Field("charging_schedule"),
    "schedule_charge_type": Field("integer", 0),
    "schedule_day": Field("integer", 0, 6),
    "schedule_hour": Field("integer", 0, 23),
    "last_sync_datetime_app": Field("timestamp_ms"),
    "last_sync_datetime_device": Field("timestamp_ms"),
}

ZONE_FIELDS = {key: DEVICE_FIELDS[key] for key in (
    "power", "charge_percentage_min", "charge_percentage_limit", "additional_charge",
    "charge_smart", "silence_mode", "res_auto_config", "res_man_config",
    "res_boost_config", "res_holiday_config", "schedule_charging", "schedule_charge_type",
)}
FIRMWARE_FIELDS = {key: Field("version") for key in (
    "firmware_version_device", "firmware_version", "hardware_version",
)}
INSTALLATION_FIELDS = {
    "holidaysMode": Field("boolean"), "holidays": Field("holidays"),
    "timeZone": Field("timezone"),
}
METADATA_FIELDS = {
    "installation_id": Field("identifier", private=True),
    "zone_id": Field("identifier", private=True),
}
ENERGY_FIELDS = {
    **{key: Field("datetime") for key in ("start", "end", "created")},
    **{key: Field("number", 0) for key in ("kwh", "effective_power")},
    **{key: Field("number") for key in ("cost", "saving", "temperature", "temperature_probe")},
    "daily": Field("boolean"),
}

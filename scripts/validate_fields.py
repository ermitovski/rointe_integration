#!/usr/bin/env python3
"""Read and validate all integration field groups without changing device settings."""

from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import json
import logging
import math
from pathlib import Path
import sys
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

if __package__:
    from . import validate_display_timeout as common
    from .field_schema import (DEVICE_FIELDS, ENERGY_FIELDS, FIRMWARE_FIELDS,
                               INSTALLATION_FIELDS, METADATA_FIELDS, ZONE_FIELDS, Field)
else:
    import validate_display_timeout as common
    from field_schema import (DEVICE_FIELDS, ENERGY_FIELDS, FIRMWARE_FIELDS,
                              INSTALLATION_FIELDS, METADATA_FIELDS, ZONE_FIELDS, Field)

DEFAULT_OUTPUT = common.ROOT / ".rointe-validation.json"
CORE_FIELDS = {"power", "mode", "temp", "temp_probe"}
# Shared firmware can report unused control fields with placeholder values.
FAMILY_GATED_FIELDS = set(ZONE_FIELDS) - {"power"} | {
    "adelanto_enable", "dont_disturb_mode", "dont_disturb_decrease",
    "dont_disturb_start", "dont_disturb_end", "backlight", "backlight_on",
    "backlight_time", "buzzer", "user_mode", "um_min_temp", "um_max_temp", "pir_mode",
    "timer_mode", "timer_time", "timer_temp", "timer_config_temp", "timer_config_time",
}


def safe_value(value, field: Field):
    """Keep non-finite values JSON-safe and redact identifiers and Wi-Fi names."""
    if value is None:
        return None
    if field.private:
        return "<redacted>"
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, float) and not math.isfinite(value):
        return str(value)
    if isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, list):
        return [safe_value(item, field) for item in value]
    if isinstance(value, dict):
        # Only known nested schedule/holiday keys, never arbitrary payload keys.
        allowed = {str(day) for day in range(7)} if "schedule" in field.kind else {"from", "to"}
        return {str(key): safe_value(item, field) for key, item in value.items() if str(key) in allowed}
    return f"<unexpected {type(value).__name__}>"


def check_value(value, spec: Field) -> list[str]:
    """Check types, finite values, known ranges, schedule shapes and time formats."""
    kind = spec.kind
    if kind == "boolean":
        return [] if isinstance(value, bool) else ["Expected a JSON boolean; Python truthiness can misinterpret this value."]
    if kind in {"string", "version", "identifier", "timezone"}:
        allowed = (str, int, float) if kind == "version" else (str, int) if kind == "identifier" else (str,)
        if isinstance(value, bool) or not isinstance(value, allowed):
            return [f"Unexpected type for {kind}."]
        if kind == "timezone":
            try:
                ZoneInfo(value)
            except (ZoneInfoNotFoundError, ValueError):
                return ["Unknown IANA timezone."]
        return []
    if kind == "power":
        return [] if isinstance(value, bool) or type(value) in (int, float) and value in (0, 1, 2) else ["Expected a legacy boolean or Nexa power code 0, 1 or 2."]
    if kind == "mode":
        return [] if not isinstance(value, bool) and value in (0, 1, "0", "1", "manual", "auto") else ["Unrecognised operation mode."]
    if kind == "timeout":
        return [] if common.display_timeout_option(value) is not None else ["Unrecognised display timeout (expected 0, 10, reported default 15, or 30)."]
    if kind in {"schedule", "charging_schedule"}:
        if not isinstance(value, (list, dict)):
            return ["Expected a weekly schedule as a list or dictionary."]
        days = dict(enumerate(value)) if isinstance(value, list) else value
        if not days:
            return ["No daily schedule values were reported."]
        alphabet = "01" if kind == "charging_schedule" else "CEOI"
        if any(str(day) not in {str(d) for d in range(7)} or not isinstance(hours, str)
               or len(hours) != 24 or any(c not in alphabet for c in hours.upper())
               for day, hours in days.items()):
            return [f"Schedule needs days 0–6 with 24 hourly characters from {alphabet}."]
        return []
    if kind == "datetime":
        return [] if isinstance(value, datetime) else ["Expected an SDK datetime."]
    if kind == "holidays":
        if not isinstance(value, list):
            return ["Expected a list of holiday periods."]
        previous_end = None
        for index, period in enumerate(value):
            try:
                start = datetime.fromisoformat(period["from"])
                end = datetime.fromisoformat(period["to"]) if period.get("to") is not None else None
                # API strings without offsets are UTC, as used by holiday_payload.
                start = start.replace(tzinfo=timezone.utc) if start.tzinfo is None else start
                end = end.replace(tzinfo=timezone.utc) if end is not None and end.tzinfo is None else end
                if (end is None and index != len(value) - 1 or end is not None and end <= start
                        or previous_end is not None and start < previous_end):
                    return ["Holiday periods overlap, are out of order, or have an invalid end."]
                previous_end = end
            except (KeyError, TypeError, ValueError, AttributeError):
                return ["Holiday periods need valid from/to datetime strings."]
        return []
    try:
        if isinstance(value, bool) or not isinstance(value, (int, float, str)):
            raise ValueError
        number = float(value)
        if not math.isfinite(number):
            raise ValueError
        if kind in {"integer", "wall_time", "timestamp_ms"} and not number.is_integer():
            return ["Expected a whole number; the device model may truncate this value."]
        if spec.minimum is not None and number < spec.minimum or spec.maximum is not None and number > spec.maximum:
            return [f"Outside the integration's range {spec.minimum}–{spec.maximum}."]
        if spec.step and not math.isclose(number / spec.step, round(number / spec.step), abs_tol=1e-6):
            return [f"Expected increments of {spec.step}."]
        if kind == "timestamp_ms":
            datetime.fromtimestamp(number / 1000, timezone.utc)
    except (ValueError, TypeError, OverflowError, OSError):
        return ["Expected a finite numeric value with a valid timestamp if applicable."]
    return []


def inspect_fields(data, catalogue, *, device=None, source="device", applicable=True):
    """Report every known field, distinguishing absence from invalid data."""
    result = {}
    data = data if isinstance(data, dict) else {}
    for key, spec in catalogue.items():
        if source == "device" and device is not None and device.user_mode and key in {"comfort", "eco"}:
            spec = Field(spec.kind, device.um_min_temp, device.um_max_temp, spec.step, spec.attribute)
        present = key in data
        value = data.get(key)
        supported = None
        if device is not None and source in {"device", "zone"}:
            try:
                supported = (device.supports_storage_setting(key) if source == "zone"
                             else device.supports_setting(key))
            except Exception:
                pass
        entry = {"present": present, "type": type(value).__name__,
                 "value": safe_value(value, spec), "expected": spec.kind,
                 "status": "valid", "issues": []}
        for constraint in ("minimum", "maximum", "step"):
            if getattr(spec, constraint) is not None:
                entry[constraint] = getattr(spec, constraint)
        if not applicable or (present and value is not None and source == "device"
                              and key in FAMILY_GATED_FIELDS and supported is False):
            entry["status"] = "not_applicable"
        elif not present:
            entry["status"] = "missing"
        elif value is None:
            entry["status"] = "null"
        elif (issues := check_value(value, spec)):
            entry.update(status="invalid", issues=issues)
        elif isinstance(value, str) and spec.kind in {"number", "integer", "timeout", "timestamp_ms", "wall_time"}:
            entry.update(status="coerced", issues=["Numeric text: inspect the parsed value and entity behavior."])
        if source == "device" and key in CORE_FIELDS and entry["status"] in {"missing", "null"}:
            entry["issues"].append("Core climate value missing; the integration may substitute a default.")
        if device is not None and source in {"device", "zone"}:
            entry["setting_supported"] = supported
            attribute = spec.attribute or key
            if source == "device":
                try:
                    if key == "backlight_time":
                        parsed = common.display_timeout_option(value)
                    elif key in {"dont_disturb_start", "dont_disturb_end"}:
                        parsed = (f"{int(value) // 3600:02}:{int(value) // 60 % 60:02}:{int(value) % 60:02}"
                                  if isinstance(value, (int, float)) and not isinstance(value, bool) and 0 <= value < 86400 else None)
                    else:
                        parsed = getattr(device, attribute)
                    entry["parsed_value"] = safe_value(parsed, spec)
                    if not present:
                        entry["uses_model_default"] = True
                except AttributeError:
                    pass  # Some controls read the raw field directly.
                except Exception:
                    entry["issues"].append("The device model could not interpret this field.")
        result[key] = entry
    return result


def diagnose_device(info, device_model, energy=None, latest_fw=None, expected_timeout=None):
    """Keep field diagnostics even when one bad value prevents model creation."""
    errors = []
    device = None
    if not isinstance(info, dict) or not isinstance(info.get("data"), dict):
        return {"errors": ["Device response has no data object."], "fields": {}, "derived": {}}
    for container in ("metadata", "firmware", "commanded"):
        if info.get(container) is not None and not isinstance(info[container], dict):
            errors.append(f"Malformed {container} object.")
    try:
        device = device_model("diagnostic-device", info, energy, latest_fw)
    except Exception:
        errors.append("Device model construction failed; inspect invalid field values below.")
    storage = device is not None and device.type == "storage_heater"
    metadata = info.get("metadata") if isinstance(info.get("metadata"), dict) else {}
    fields = {
        "device": inspect_fields(info["data"], DEVICE_FIELDS, device=device),
        "zone": inspect_fields(info.get("commanded"), ZONE_FIELDS, device=device, source="zone", applicable=storage),
        "firmware": inspect_fields(info.get("firmware"), FIRMWARE_FIELDS, source="firmware"),
        "metadata": inspect_fields({**metadata, "serialnumber": info.get("serialnumber")},
                                   {**METADATA_FIELDS, "serialnumber": Field("identifier", private=True)}, source="metadata"),
        "installation": inspect_fields(metadata.get("installation"), INSTALLATION_FIELDS, source="installation"),
        # Energy values are SDK-parsed statistics, not the raw HTTP response.
        "energy": inspect_fields(vars(energy) if energy is not None else {}, ENERGY_FIELDS, source="sdk_energy"),
    }
    derived = {}
    if device is not None:
        for name, getter in {
            "device_family": lambda: device.type,
            "model_name": lambda: device.model_name,
            "power_on": lambda: device.power,
            "mode": lambda: device.mode,
            "target_temperature": device.get_effective_target_temperature,
            "heating": lambda: device.heating,
            "schedule_mode": lambda: device.get_current_schedule_mode().value,
            "boost_active": lambda: device.boost_active,
            "boost_seconds_remaining": lambda: device.boost_countdown,
            "firmware_version": lambda: device.firmware_version,
            "latest_firmware_version": lambda: device.latest_firmware_version,
            "hardware_version": lambda: device.hardware_version,
        }.items():
            try:
                derived[name] = safe_value(getter(), Field("derived"))
            except Exception:
                errors.append(f"Unable to calculate {name}.")
        from _rointe_validation_sdk.power import reduction_percentage
        try:
            derived["power_reduction_percent"] = reduction_percentage(device._data)
        except Exception:
            errors.append("Unable to calculate power reduction.")
        timeout = common.diagnose_timeout(device._data, device.supports_setting("backlight_time"),
                                          expected_timeout if expected_timeout is not None else 30)
        timeout["reported_timeout"] = safe_value(timeout["reported_timeout"], Field("timeout"))
        if expected_timeout is None:
            timeout.pop("matches_expected")
        derived["display_timeout"] = timeout
        if device.user_mode and device.um_min_temp > device.um_max_temp:
            errors.append("Active user-mode minimum temperature exceeds its maximum.")
        for source in (info["data"], info.get("commanded") or {}):
            if not isinstance(source, dict):
                continue
            low, high = source.get("charge_percentage_min"), source.get("charge_percentage_limit")
            if type(low) in (int, float) and type(high) in (int, float) and low >= high:
                errors.append("Minimum charge is not below maximum charge.")
    return {"errors": errors, "fields": fields, "derived": derived}


def latest_firmware(info, firmware_map):
    """Follow the integration's legacy firmware map without importing HA."""
    from _rointe_validation_sdk.utils import get_product_by_type_version
    data, firmware = info.get("data") or {}, info.get("firmware") or {}
    current = firmware.get("firmware_version_device") or firmware.get("firmware_version")
    if not all((data.get("type"), data.get("product_version"), current)):
        return None
    product = get_product_by_type_version(data["type"], data["product_version"])
    if product is None:
        return None
    return (firmware_map.get(product) or {}).get(str(current), str(current))


def collect_report(api, device_model, expected_timeout=None):
    """Read the same inventory, device, zone, firmware and energy sources as HA."""
    report = {"checked_at": datetime.now(timezone.utc).isoformat(), "devices": [], "errors": [], "warnings": []}
    try:
        if not api.initialize_authentication().success:
            report["errors"].append("Authentication failed. Check credentials and connectivity.")
            return report
        installations = api.get_installations()
        if not installations.success or not installations.data:
            report["errors"].append("Unable to read installations, or none were found.")
            return report
        firmware_map = {}
        if not api.use_nexa_ws:
            try:
                firmware = api.get_latest_firmware()
                if firmware.success:
                    firmware_map = firmware.data or {}
                else:
                    report["warnings"].append("Latest firmware catalogue unavailable.")
            except Exception:
                report["warnings"].append("Latest firmware catalogue read failed.")
        for installation_index, installation_id in enumerate(installations.data, 1):
            inventory = api.get_installation_devices(installation_id)
            if not inventory.success:
                report["errors"].append(f"Installation {installation_index}: device inventory unavailable.")
                continue
            for device_index, device_id in enumerate(inventory.data, 1):
                label = f"Installation {installation_index}, device {device_index}"
                try:
                    response = api.get_device(device_id)
                    if not response.success:
                        report["errors"].append(f"{label}: device settings unavailable.")
                        continue
                    energy = None
                    try:
                        statistics = api.get_latest_energy_stats(device_id, installation_id)
                        if statistics.success:
                            energy = statistics.data
                        else:
                            report["warnings"].append(f"{label}: energy statistics unavailable (possibly no readings yet).")
                    except Exception:
                        report["warnings"].append(f"{label}: energy statistics read failed.")
                    latest = latest_firmware(response.data, firmware_map) if not api.use_nexa_ws else None
                    report["devices"].append({"device": label,
                        **diagnose_device(response.data, device_model, energy, latest, expected_timeout)})
                except Exception:
                    report["errors"].append(f"{label}: device read or diagnostic processing failed.")
    except Exception:
        # Do not print SDK exception strings, response bodies or authenticated URLs.
        report["errors"].append("Cloud discovery failed. Check network access and retry.")
    finally:
        api.close()
    if not report["devices"] and not report["errors"]:
        report["errors"].append("No devices were found.")
    return report


def summarise(report):
    counts = Counter()
    for device in report["devices"]:
        for group in device["fields"].values():
            counts.update(entry["status"] for entry in group.values())
    report["summary"] = dict(counts)
    failures = len(report["errors"]) + sum(len(d["errors"]) for d in report["devices"]) + counts["invalid"]
    for device in report["devices"]:
        if device["derived"].get("display_timeout", {}).get("matches_expected") is False:
            failures += 1
    return failures


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--init", action="store_true", help="Create a blank private credentials file and exit")
    parser.add_argument("--credentials", type=Path, default=common.DEFAULT_CREDENTIALS)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--expected-timeout", type=int, choices=(0, 10, 15, 30), help="Optionally compare all devices with an expected timeout (15 is a reported default)")
    parser.add_argument("--timezone", help="Home Assistant timezone, e.g. Europe/Madrid; defaults to this computer's current local timezone")
    args = parser.parse_args(argv)
    if args.credentials.resolve() == args.output.resolve():
        print("Credentials and report must use different paths.", file=sys.stderr)
        return 2
    try:
        if args.init:
            common.init_credentials(args.credentials)
            print(f"Created {args.credentials}; fill in username and password.")
            return 0
        local_zone = ZoneInfo(args.timezone) if args.timezone else datetime.now().astimezone().tzinfo
        credentials = common.read_credentials(args.credentials)
        api_class, device_model = common.load_sdk()
        from _rointe_validation_sdk import utils
        utils.DEFAULT_TIME_ZONE = local_zone
    except ZoneInfoNotFoundError:
        print("Unknown timezone. Use an IANA name such as Europe/Madrid.", file=sys.stderr)
        return 2
    except ImportError:
        print("Install dependencies with: python -m pip install -r scripts/requirements.txt", file=sys.stderr)
        return 2
    except (ValueError, OSError) as error:
        print(str(error), file=sys.stderr)
        return 2
    logging.disable(logging.CRITICAL)
    api = api_class(credentials["username"], credentials["password"], api_type=credentials.get("api_type", "nexa"))
    try:
        print("Reading integration fields; no device settings will be changed...", flush=True)
        report = collect_report(api, device_model, args.expected_timeout)
    except KeyboardInterrupt:
        print("\nCancelled; connection closed.")
        return 130
    report["timezone"] = str(local_zone)
    report["scope"] = "Cloud fields and SDK interpretation; physical application and HA entity lifecycle require a separate live check."
    failures = summarise(report)
    try:
        common.save_report(args.output, report)
    except OSError:
        print("Could not save report. Check output folder and permissions.", file=sys.stderr)
        return 2
    print(f"Checked {len(report['devices'])} device(s): {json.dumps(report['summary'], sort_keys=True)}")
    for device in report["devices"]:
        for group, fields in device["fields"].items():
            for key, entry in fields.items():
                if entry["issues"]:
                    print(f"{device['device']} / {group}.{key}: {entry['status']} — {' '.join(entry['issues'])}")
        for error in device["errors"]:
            print(f"{device['device']}: {error}")
        timeout = device["derived"].get("display_timeout")
        if timeout is not None and timeout.get("matches_expected") is False:
            print(f"{device['device']}: display timeout differs from --expected-timeout.")
    for message in report["errors"] + report["warnings"]:
        print(message)
    print(f"Saved report: {args.output}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())

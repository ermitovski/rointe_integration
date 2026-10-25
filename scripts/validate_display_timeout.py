#!/usr/bin/env python3
"""Read live Rointe timeouts without Home Assistant or device-setting writes."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import importlib.util
import json
import logging
import os
from pathlib import Path
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
# Load the SDK as its own package. Adding the integration folder to sys.path
# would shadow Python's standard-library select module with the HA platform.
SDK_PATH = ROOT / "custom_components" / "rointe" / "rointesdk"
SDK_SPEC = importlib.util.spec_from_file_location(
    "_rointe_validation_sdk", SDK_PATH / "__init__.py",
    submodule_search_locations=[str(SDK_PATH)],
)
SDK_PACKAGE = importlib.util.module_from_spec(SDK_SPEC)
sys.modules[SDK_SPEC.name] = SDK_PACKAGE
SDK_SPEC.loader.exec_module(SDK_PACKAGE)
from _rointe_validation_sdk.controls import (  # noqa: E402
    DISPLAY_TIMEOUT_OPTIONS, REPORTED_DISPLAY_TIMEOUT_OPTIONS, display_timeout_option,
)

DEFAULT_CREDENTIALS = ROOT / ".rointe-credentials.json"
DEFAULT_OUTPUT = ROOT / ".rointe-display-timeout.json"


def init_credentials(path: Path) -> None:
    """Create a private blank file, never replacing existing credentials."""
    try:
        with open(path, "x", opener=lambda p, flags: os.open(p, flags, 0o600)) as file:
            json.dump({"username": "", "password": "", "api_type": "nexa"}, file, indent=2)
            file.write("\n")
    except FileExistsError:
        raise ValueError("Credentials file already exists; it was left unchanged.") from None


def read_credentials(path: Path) -> dict:
    """Read JSON as data; never execute shell contents or echo secrets."""
    try:
        credentials = json.loads(path.read_text())
    except FileNotFoundError:
        raise ValueError("Credentials file missing. Run with --init first.") from None
    except (OSError, ValueError):
        raise ValueError("Cannot read credentials. Check the file is valid JSON.") from None
    if not isinstance(credentials, dict) or any(
        not isinstance(credentials.get(key), str) or not credentials[key].strip()
        for key in ("username", "password")
    ):
        raise ValueError("Fill in username and password in the credentials file.")
    if credentials.get("api_type", "nexa") not in {"nexa", "rointe", "auto"}:
        raise ValueError("api_type must be nexa, rointe, or auto.")
    return credentials


def diagnose_timeout(data: dict, supported: bool, expected: int) -> dict:
    """Compare the original and fixed parsing against the actual cloud value."""
    value = data.get("backlight_time")
    old_option = next((name for name, seconds in DISPLAY_TIMEOUT_OPTIONS.items()
                       if seconds == value), None)
    option = display_timeout_option(value)
    matches = supported and option is not None and REPORTED_DISPLAY_TIMEOUT_OPTIONS[option] == expected
    reported_only = option is not None and option not in DISPLAY_TIMEOUT_OPTIONS
    if "backlight_time" not in data:
        diagnosis = "The cloud did not return backlight_time. Parsing cannot fix a missing field."
    elif value is None:
        diagnosis = "The cloud returned null for backlight_time. No timeout is available."
    elif not supported:
        diagnosis = "The integration's device capability rules do not expose this setting."
    elif option is None:
        diagnosis = "The cloud returned an unrecognised timeout; the parsing fix does not resolve it."
    elif reported_only:
        diagnosis = "The cloud reports Nexa's 15-second default. It is not an alias for the app's 30-second button."
    elif old_option != option:
        diagnosis = "Confirmed: the original parser missed this value; the fixed parser recognises it."
    else:
        diagnosis = "Both parsers recognise this value. The reported unknown state has another cause."
    return {
        "field_present": "backlight_time" in data,
        "reported_timeout": value,
        "reported_timeout_type": type(value).__name__,
        "supported": supported,
        "original_option": old_option,
        "fixed_option": option,
        "reported_only": reported_only,
        "matches_expected": matches,
        "diagnosis": diagnosis,
    }


def load_sdk():
    """Load network dependencies only for a live check, not for --init/help."""
    from _rointe_validation_sdk.device import RointeDevice
    from _rointe_validation_sdk.rointe_api import RointeAPI

    return RointeAPI, RointeDevice


def collect_report(api, device_model, expected: int) -> dict:
    """Use only authentication, inventory and device reads; always disconnect."""
    report = {
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "expected_seconds": expected,
        "devices": [],
        "errors": [],
    }
    try:
        if not api.initialize_authentication().success:
            report["errors"].append("Authentication failed. Check credentials and connectivity.")
            return report
        installations = api.get_installations()
        if not installations.success or not installations.data:
            report["errors"].append("Unable to read installations, or none were found.")
            return report
        for installation_index, installation_id in enumerate(installations.data, 1):
            inventory = api.get_installation_devices(installation_id)
            if not inventory.success:
                report["errors"].append(f"Unable to read devices in installation {installation_index}.")
                continue
            for device_index, device_id in enumerate(inventory.data, 1):
                label = f"Installation {installation_index}, device {device_index}"
                try:
                    response = api.get_device(device_id)
                    if not response.success:
                        report["errors"].append(f"{label}: unable to read device settings.")
                        continue
                    device = device_model(device_id, response.data, None, None)
                    report["devices"].append({
                        "device": label,
                        "name": device.name,
                        **diagnose_timeout(device._data, device.supports_setting("backlight_time"), expected),
                    })
                except Exception:
                    # SDK exception strings may contain authenticated URLs.
                    report["errors"].append(f"{label}: device read or data decoding failed.")
    except Exception:
        report["errors"].append("Cloud discovery failed. Check network access and try again.")
    finally:
        api.close()
    if not report["devices"] and not report["errors"]:
        report["errors"].append("No devices were found.")
    return report


def save_report(path: Path, report: dict) -> None:
    """Save only selected diagnostic fields, without credentials or tokens."""
    # Private temporary file plus replacement avoids following an existing symlink.
    with tempfile.NamedTemporaryFile(mode="w", dir=path.parent, delete=False) as file:
        temporary = Path(file.name)
        try:
            json.dump(report, file, indent=2, ensure_ascii=True)
            file.write("\n")
            file.close()
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--init", action="store_true", help="Create a blank private credentials file and exit")
    parser.add_argument("--credentials", type=Path, default=DEFAULT_CREDENTIALS)
    parser.add_argument("--expected", type=int, choices=(0, 10, 15, 30), default=30,
                        help="Expected timeout in seconds (default: 30; 0 means always on; 15 is a reported default)")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT,
                        help="Diagnostic report destination (default: gitignored file in the repository)")
    args = parser.parse_args(argv)
    # Never overwrite credentials with a report, including through a symlink.
    if args.credentials.resolve() == args.output.resolve():
        print("Credentials and report must use different paths.", file=sys.stderr)
        return 2
    try:
        if args.init:
            init_credentials(args.credentials)
            print(f"Created {args.credentials}. Fill in username and password, then run again.")
            return 0
        credentials = read_credentials(args.credentials)
        api_class, device_model = load_sdk()
    except ImportError:
        print("Install dependencies with: python -m pip install -r scripts/requirements.txt", file=sys.stderr)
        return 2
    except (OSError, ValueError) as error:
        print(str(error), file=sys.stderr)
        return 2
    # Suppress SDK logs, including errors which can contain server response bodies.
    logging.disable(logging.CRITICAL)
    api = api_class(credentials["username"], credentials["password"],
                    api_type=credentials.get("api_type", "nexa"))
    try:
        print("Reading live timeout settings; no device settings will be changed...", flush=True)
        report = collect_report(api, device_model, args.expected)
    except KeyboardInterrupt:
        print("\nCancelled; connection closed.")
        return 130
    try:
        save_report(args.output, report)
    except OSError:
        print("Could not save the report. Check the output folder and permissions.", file=sys.stderr)
        return 2
    print(json.dumps(report, indent=2, ensure_ascii=True))
    print(f"\nSaved report: {args.output}")
    return 0 if report["devices"] and not report["errors"] and all(
        device["matches_expected"] for device in report["devices"]
    ) else 1


if __name__ == "__main__":
    sys.exit(main())

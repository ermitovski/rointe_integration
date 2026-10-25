"""Read-only local diagnosis uses the shared parser and keeps secrets private."""

import json
from pathlib import Path
import stat
import subprocess
import sys
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from custom_components.rointe.rointesdk.device import RointeDevice
from scripts import validate_display_timeout as validator


@pytest.mark.parametrize("data,supported,option,matches,diagnosis", [
    ({"backlight_time": "30"}, True, "30 seconds", True, "Confirmed:"),
    ({"backlight_time": 30}, True, "30 seconds", True, "another cause"),
    ({"backlight_time": 10}, True, "10 seconds", False, "another cause"),
    ({"backlight_time": 15}, True, "15 seconds", False, "not an alias"),
    ({"backlight_time": 20}, True, None, False, "unrecognised"),
    ({"backlight_time": False}, True, None, False, "unrecognised"),
    ({"backlight_time": None}, False, None, False, "null"),
    ({}, False, None, False, "missing field"),
    ({"backlight_time": 30}, False, "30 seconds", False, "capability rules"),
])
def test_diagnosis_distinguishes_parsing_from_other_causes(data, supported, option, matches, diagnosis):
    result = validator.diagnose_timeout(data, supported, 30)
    assert result["fixed_option"] == option
    assert result["matches_expected"] is matches
    assert diagnosis in result["diagnosis"]


def test_reported_default_matches_only_fifteen_seconds():
    result = validator.diagnose_timeout({"backlight_time": 15}, True, 15)
    assert result["reported_only"] is True
    assert result["matches_expected"] is True
    assert result["original_option"] is None
    assert result["fixed_option"] == "15 seconds"
    assert not validator.diagnose_timeout({"backlight_time": 15}, True, 30)["matches_expected"]


def test_credentials_are_private_and_never_replaced(tmp_path):
    path = tmp_path / "credentials.json"
    validator.init_credentials(path)
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    credentials = {"username": "user", "password": " p@ss $() # ü "}
    path.write_text(json.dumps(credentials))
    with pytest.raises(ValueError, match="left unchanged"):
        validator.init_credentials(path)
    assert validator.read_credentials(path) == credentials


@pytest.mark.parametrize("contents", ["{SECRET", "[]", '{}', '{"username":"user","password":""}'])
def test_invalid_credentials_do_not_echo_contents(tmp_path, contents):
    path = tmp_path / "credentials.json"
    path.write_text(contents)
    with pytest.raises(ValueError) as error:
        validator.read_credentials(path)
    assert contents not in str(error.value)


def read_only_api():
    # No write methods exist on this mock: accidental writes fail the test.
    api = MagicMock(spec=["initialize_authentication", "get_installations",
                          "get_installation_devices", "get_device", "close"])
    api.initialize_authentication.return_value = SimpleNamespace(success=True)
    api.get_installations.return_value = SimpleNamespace(success=True, data={"private-installation-id": "Home"})
    api.get_installation_devices.return_value = SimpleNamespace(success=True, data=["private-device-id"])
    api.get_device.return_value = SimpleNamespace(success=True, data={"data": {
        "name": "Bedroom", "product_type": 1, "backlight_time": "30",
        "unrelated_private_data": "SECRET",
    }})
    return api


def test_read_only_report_excludes_ids_and_unrelated_data(tmp_path):
    api = read_only_api()
    report = validator.collect_report(api, RointeDevice, 30)
    assert not report["errors"]
    assert report["devices"][0]["matches_expected"]
    assert report["devices"][0]["original_option"] is None
    api.close.assert_called_once()
    path = tmp_path / "report.json"
    validator.save_report(path, report)
    assert json.loads(path.read_text()) == report
    for private in ("SECRET", "private-installation-id", "private-device-id"):
        assert private not in path.read_text()
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


@pytest.mark.parametrize("failure", ["login", "inventory", "device", "exception", "empty"])
def test_failures_are_reported_without_exposing_exception_details(failure):
    api = read_only_api()
    if failure == "login":
        api.initialize_authentication.return_value = SimpleNamespace(success=False)
    elif failure == "inventory":
        api.get_installation_devices.return_value = SimpleNamespace(success=False)
    elif failure == "device":
        api.get_device.return_value = SimpleNamespace(success=False)
    elif failure == "exception":
        api.get_installations.side_effect = RuntimeError("SECRET authenticated URL")
    else:
        api.get_installation_devices.return_value.data = []
    report = validator.collect_report(api, RointeDevice, 30)
    assert report["errors"]
    assert "SECRET" not in json.dumps(report)
    api.close.assert_called_once()


def test_interrupt_closes_connection():
    api = read_only_api()
    api.get_device.side_effect = KeyboardInterrupt
    with pytest.raises(KeyboardInterrupt):
        validator.collect_report(api, RointeDevice, 30)
    api.close.assert_called_once()


def test_cli_keeps_credentials_out_of_output(tmp_path, monkeypatch, capsys):
    credentials = tmp_path / "credentials.json"
    credentials.write_text(json.dumps({"username": "SECRET_USER", "password": "SECRET_PASSWORD"}))
    output = tmp_path / "report.json"
    api = read_only_api()
    monkeypatch.setattr(validator, "load_sdk", lambda: (lambda *args, **kwargs: api, RointeDevice))
    monkeypatch.setattr(validator.logging, "disable", lambda level: None)
    assert validator.main(["--credentials", str(credentials), "--output", str(output)]) == 0
    assert "SECRET" not in capsys.readouterr().out
    assert "SECRET" not in output.read_text()


def test_report_cannot_overwrite_credentials(tmp_path, capsys):
    path = tmp_path / "credentials.json"
    path.write_text("SECRET")
    assert validator.main(["--credentials", str(path), "--output", str(path)]) == 2
    assert path.read_text() == "SECRET"
    assert "SECRET" not in capsys.readouterr().err


def test_default_private_files_are_explicitly_gitignored():
    ignored = (Path(__file__).resolve().parents[1] / ".gitignore").read_text().splitlines()
    assert validator.DEFAULT_CREDENTIALS.name in ignored
    assert validator.DEFAULT_OUTPUT.name in ignored


def test_standalone_sdk_does_not_import_home_assistant_or_shadow_stdlib():
    result = subprocess.run([
        sys.executable, "-c",
        "import runpy, sys; "
        "helper = runpy.run_path('scripts/validate_display_timeout.py', run_name='validator'); "
        "helper['load_sdk'](); "
        "assert not any(n == 'homeassistant' or n.startswith('homeassistant.') for n in sys.modules); "
        "import select; assert hasattr(select, 'select')",
    ], cwd=validator.ROOT, capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stderr

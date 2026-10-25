"""All-field local validation: coverage, real model interpretation and privacy."""

import ast
from datetime import datetime, timezone
import json
import os
import subprocess
import sys
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from custom_components.rointe.number import NUMBER_DESCRIPTIONS
from custom_components.rointe.rointesdk.device import RointeDevice
from custom_components.rointe.rointesdk.dto import EnergyConsumptionData
from custom_components.rointe.rointesdk.controls import OPTIONAL_FIELDS
from custom_components.rointe.rointesdk.storage import STORAGE_FIELDS
from custom_components.rointe.switch import SWITCH_DESCRIPTIONS
from scripts import validate_fields as validator
from scripts.field_schema import DEVICE_FIELDS, ENERGY_FIELDS, Field


def info(**changes):
    return {"data": {"product_type": 1, "product_brand": 1, "product_model": 24,
                     "name": "Bedroom", "temp": 22, "temp_probe": 20,
                     "power": 2, "mode": 0, **changes}}


def test_catalogue_covers_all_model_field_reads_and_transport_writes():
    root = validator.common.SDK_PATH
    tree = ast.parse((root / "device.py").read_text())
    reads = set()
    for node in ast.walk(tree):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr == "get" and node.args and isinstance(node.args[0], ast.Constant)
                and ast.unparse(node.func.value) in {"data", "self._data"}):
            reads.add(node.args[0].value)
    tree = ast.parse((root / "rointe_api.py").read_text())
    method = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == "_write_via_websocket")
    writes = next(ast.literal_eval(n.value) for n in method.body if isinstance(n, ast.Assign)
                  and any(isinstance(t, ast.Name) and t.id == "supported" for t in n.targets))
    assert not (reads | writes | OPTIONAL_FIELDS | STORAGE_FIELDS) - DEVICE_FIELDS.keys()
    for description in (*NUMBER_DESCRIPTIONS, *SWITCH_DESCRIPTIONS):
        assert description.field_name in DEVICE_FIELDS


@pytest.mark.parametrize("description", [d for d in NUMBER_DESCRIPTIONS if d.field_name != "nominal_effective_power"], ids=lambda d: d.key)
def test_numeric_ranges_match_actual_entity_descriptions(description):
    spec = DEVICE_FIELDS[description.field_name]
    assert (spec.minimum, spec.maximum, spec.step or 1) == (
        description.native_min_value, description.native_max_value, description.native_step)


@pytest.mark.parametrize("kind,value,invalid", [
    ("boolean", False, False), ("boolean", "false", True), ("boolean", 0, True),
    ("number", "20.5", False), ("number", float("nan"), True),
    ("integer", 1.5, True), ("integer", True, True),
    ("wall_time", 86400, True), ("wall_time", "82800", False),
    ("power", True, False), ("power", 2, False), ("power", "2", True),
    ("mode", "auto", False), ("mode", "0", False), ("mode", False, True),
    ("timeout", "30", False), ("timeout", 15, False), ("timeout", 20, True),
    ("timezone", "Europe/Madrid", False), ("timezone", "invalid-zone", True),
    ("timestamp_ms", 1e100, True),
])
def test_type_and_value_validation(kind, value, invalid):
    spec = Field(kind, 0, 86399) if kind == "wall_time" else Field(kind)
    assert bool(validator.check_value(value, spec)) is invalid


@pytest.mark.parametrize("kind,value,invalid", [
    ("schedule", ["C" * 24] * 7, False),
    ("schedule", {"0": "E" * 24}, False),
    ("schedule", {"7": "C" * 24}, True),
    ("schedule", {"0": "C" * 23}, True),
    ("schedule", [None], True),
    ("charging_schedule", {"0": "01" * 12}, False),
    ("charging_schedule", {"0": "C" * 24}, True),
    ("holidays", [{"from": "2026-11-01 00:00:00", "to": None}], False),
    ("holidays", [{"from": "2026-11-01", "to": "2026-10-01"}], True),
    ("holidays", [{"from": "2026-11-01"}, {"from": "2026-12-01"}], True),
    ("holidays", [{"from": "2026-11-01", "to": "2026-11-10"}, {"from": "2026-11-09"}], True),
])
def test_schedules_and_holiday_shapes(kind, value, invalid):
    assert bool(validator.check_value(value, Field(kind))) is invalid


def test_model_truthiness_and_defaults_are_visible():
    result = validator.diagnose_device(info(user_mode="false", backlight_time="30"), RointeDevice)
    boolean = result["fields"]["device"]["user_mode"]
    assert boolean["status"] == "invalid"
    assert boolean["parsed_value"] is True
    assert result["fields"]["device"]["temp_surface"]["uses_model_default"]
    assert result["derived"]["display_timeout"]["original_option"] is None
    assert result["derived"]["display_timeout"]["fixed_option"] == "30 seconds"
    assert "matches_expected" not in result["derived"]["display_timeout"]


def test_bad_numeric_field_does_not_hide_other_raw_fields():
    result = validator.diagnose_device(info(nominal_power="bad", temp_probe=19.5), RointeDevice)
    assert result["errors"]
    assert result["fields"]["device"]["nominal_power"]["status"] == "invalid"
    assert result["fields"]["device"]["temp_probe"]["value"] == 19.5


def test_unused_shared_firmware_controls_are_not_failures():
    result = validator.diagnose_device(info(charge_percentage_limit=0), RointeDevice)
    assert result["fields"]["device"]["charge_percentage_limit"]["status"] == "not_applicable"
    assert all(entry["status"] == "not_applicable" for entry in result["fields"]["zone"].values())


@pytest.mark.parametrize("comfort,status", [(32, "valid"), ("32", "coerced")])
def test_user_limits_override_only_comfort_and_eco_bounds(comfort, status):
    result = validator.diagnose_device(info(user_mode=True, um_min_temp=5, um_max_temp=35,
                                            comfort=comfort, eco=5, ice=4), RointeDevice)
    fields = result["fields"]["device"]
    assert fields["comfort"]["status"] == status
    assert fields["eco"]["status"] == "valid"
    assert (fields["comfort"]["minimum"], fields["comfort"]["maximum"]) == (5, 35)
    assert fields["ice"]["status"] == "invalid"


def test_zone_fields_are_separate_from_device_readback():
    data = info(product_type=5, charge_percentage_min=5, charge_percentage_limit=90)
    data["metadata"] = {"zone_id": "PRIVATE_ZONE", "installation_id": "PRIVATE_INSTALLATION"}
    data["commanded"] = {"charge_percentage_min": 10, "charge_percentage_limit": 80}
    result = validator.diagnose_device(data, RointeDevice)
    assert result["fields"]["device"]["charge_percentage_min"]["value"] == 5
    assert result["fields"]["zone"]["charge_percentage_min"]["value"] == 10
    assert result["fields"]["zone"]["charge_percentage_min"]["setting_supported"]
    assert "PRIVATE" not in json.dumps(result)


@pytest.mark.parametrize("container", ["metadata", "firmware", "commanded"])
def test_malformed_containers_still_produce_field_diagnostics(container):
    data = info()
    data[container] = "PRIVATE_MALFORMED_CONTENTS"
    result = validator.diagnose_device(data, RointeDevice)
    assert result["errors"]
    assert "temp" in result["fields"]["device"]
    assert "PRIVATE" not in json.dumps(result)


def test_report_is_strict_json_and_redacts_network_identifiers():
    data = info(backlight_time=float("nan"), wifissid="PRIVATE_WIFI")
    data["serialnumber"] = "PRIVATE_SERIAL"
    data["firmware"] = {"firmware_version_device": "1.2.3", "hardware_version": 2}
    result = validator.diagnose_device(data, RointeDevice)
    encoded = json.dumps(result, allow_nan=False)
    assert "PRIVATE" not in encoded
    assert result["fields"]["device"]["wifissid"]["value"] == "<redacted>"
    assert result["derived"]["firmware_version"] == "1.2.3"


def read_only_api(nexa=True):
    api = MagicMock(spec=["initialize_authentication", "get_installations", "get_installation_devices",
                          "get_device", "get_latest_energy_stats", "get_latest_firmware", "use_nexa_ws", "close"])
    api.use_nexa_ws = nexa
    api.initialize_authentication.return_value = SimpleNamespace(success=True)
    api.get_installations.return_value = SimpleNamespace(success=True, data={"PRIVATE_INSTALLATION": "Home"})
    api.get_installation_devices.return_value = SimpleNamespace(success=True, data=["PRIVATE_DEVICE"])
    api.get_device.return_value = SimpleNamespace(success=True, data=info())
    api.get_latest_energy_stats.return_value = SimpleNamespace(success=True, data=EnergyConsumptionData(
        datetime.now(timezone.utc), datetime.now(timezone.utc), 1.5, 0, datetime.now(timezone.utc), cost=0, saving=0.2, daily=True))
    api.get_latest_firmware.return_value = SimpleNamespace(success=True, data={})
    return api


def test_read_only_collection_includes_energy_without_nexa_firmware_probe():
    api = read_only_api()
    report = validator.collect_report(api, RointeDevice)
    assert not report["errors"]
    assert report["devices"][0]["fields"]["energy"]["kwh"]["value"] == 1.5
    assert report["devices"][0]["fields"]["energy"]["cost"]["value"] == 0
    assert ENERGY_FIELDS.keys() == report["devices"][0]["fields"]["energy"].keys()
    api.get_latest_firmware.assert_not_called()
    api.close.assert_called_once()
    assert "PRIVATE" not in json.dumps(report)


def test_optional_endpoint_failure_keeps_device_results():
    api = read_only_api(nexa=False)
    api.get_latest_energy_stats.side_effect = RuntimeError("PRIVATE_AUTHENTICATED_URL")
    api.get_latest_firmware.side_effect = RuntimeError("PRIVATE_AUTHENTICATED_URL")
    report = validator.collect_report(api, RointeDevice)
    assert report["devices"] and report["warnings"] and not report["errors"]
    assert "PRIVATE" not in json.dumps(report)
    api.close.assert_called_once()


@pytest.mark.parametrize("failure", ["login", "inventory", "device", "exception", "empty", "interrupt"])
def test_failures_always_close_and_do_not_disclose_errors(failure):
    api = read_only_api()
    if failure == "login":
        api.initialize_authentication.return_value = SimpleNamespace(success=False)
    elif failure == "inventory":
        api.get_installation_devices.return_value = SimpleNamespace(success=False)
    elif failure == "device":
        api.get_device.return_value = SimpleNamespace(success=False)
    elif failure == "exception":
        api.get_installations.side_effect = RuntimeError("PRIVATE_AUTHENTICATED_URL")
    elif failure == "empty":
        api.get_installation_devices.return_value.data = []
    else:
        api.get_device.side_effect = KeyboardInterrupt
    if failure == "interrupt":
        with pytest.raises(KeyboardInterrupt):
            validator.collect_report(api, RointeDevice)
    else:
        report = validator.collect_report(api, RointeDevice)
        assert report["errors"]
        assert "PRIVATE" not in json.dumps(report)
    api.close.assert_called_once()


def test_cli_reuses_credentials_and_default_report_is_gitignored(tmp_path, monkeypatch, capsys):
    credentials, output = tmp_path / "creds.json", tmp_path / "report.json"
    credentials.write_text(json.dumps({"username": "PRIVATE_EMAIL", "password": "PRIVATE_PASSWORD"}))
    api = read_only_api()
    monkeypatch.setattr(validator.common, "load_sdk", lambda: (lambda *args, **kwargs: api, RointeDevice))
    monkeypatch.setattr(validator.logging, "disable", lambda _: None)
    assert validator.main(["--credentials", str(credentials), "--output", str(output), "--timezone", "Europe/Madrid"]) == 0
    assert "PRIVATE" not in capsys.readouterr().out
    report = json.loads(output.read_text())
    assert report["timezone"] == "Europe/Madrid"
    assert report["summary"]["valid"] > 0
    ignored = (validator.common.ROOT / ".gitignore").read_text().splitlines()
    assert validator.DEFAULT_OUTPUT.name in ignored


def test_cli_refuses_to_overwrite_credentials(tmp_path):
    path = tmp_path / "creds.json"
    path.write_text("PRIVATE")
    assert validator.main(["--credentials", str(path), "--output", str(path)]) == 2
    assert path.read_text() == "PRIVATE"


@pytest.mark.parametrize("malformed,expected_timeout", [(True, None), (False, "10")])
def test_cli_returns_failure_for_invalid_fields_or_requested_mismatch(tmp_path, monkeypatch, malformed, expected_timeout):
    credentials, output = tmp_path / "creds.json", tmp_path / "report.json"
    credentials.write_text(json.dumps({"username": "user", "password": "password"}))
    api = read_only_api()
    api.get_device.return_value.data = info(backlight_time=30, buzzer=11 if malformed else 3)
    monkeypatch.setattr(validator.common, "load_sdk", lambda: (lambda *args, **kwargs: api, RointeDevice))
    monkeypatch.setattr(validator.logging, "disable", lambda _: None)
    args = ["--credentials", str(credentials), "--output", str(output)]
    if expected_timeout:
        args += ["--expected-timeout", expected_timeout]
    assert validator.main(args) == 1
    assert json.loads(output.read_text())["devices"]


def test_all_fields_cli_remains_standalone():
    result = subprocess.run([sys.executable, "-c",
        "import runpy, sys; helper = runpy.run_path('scripts/validate_fields.py', run_name='validator'); "
        "helper['common'].load_sdk(); assert 'homeassistant' not in sys.modules; "
        "import select; assert hasattr(select, 'select')"],
        cwd=validator.common.ROOT, capture_output=True, text=True, timeout=15,
        env={**os.environ, "PYTHONPATH": str(validator.common.ROOT / "scripts")})
    assert result.returncode == 0, result.stderr

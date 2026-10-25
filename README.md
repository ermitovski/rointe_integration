# Rointe Heaters

[![hacs_badge](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://github.com/hacs/integration)
[![GitHub Release](https://img.shields.io/github/v/release/ermitovski/rointe_integration?style=flat-square)](https://github.com/ermitovski/rointe_integration/releases)
[![GitHub Issues](https://img.shields.io/github/issues/ermitovski/rointe_integration?style=flat-square)](https://github.com/ermitovski/rointe_integration/issues)

Home Assistant integration for Rointe electric radiators. Control your Rointe heating devices directly from Home Assistant with full climate entity support.

This project is a maintained fork of [Joshua Yewman's Rointe integration](https://github.com/JYewman/rointe_integration).

## How It Works

This integration connects to Rointe's cloud infrastructure to control your radiators. It supports two different API backends depending on your device generation.

### Architecture

```
Home Assistant  ←→  Rointe Integration  ←→  Rointe Cloud  ←→  Your Radiators
                         │
                         ├── Nexa API (WebSocket)
                         │   └── Polling over a shared Firebase connection
                         │
                         └── Legacy API (REST)
                             └── Polling-based updates
```

### Nexa Platform (Newer Devices)

The Nexa platform uses Firebase Realtime Database for device state synchronisation:

1. **Authentication** - The integration authenticates with Rointe's servers using your account credentials
2. **WebSocket Connection** - Reuses one authenticated Firebase connection for device reads and commands, with keep-alives and reconnection after a dropped or stale connection
3. **State Synchronisation** - Device states (temperature, power, schedules) are polled every 15 seconds over that connection
4. **Command Dispatch** - When you adjust settings in Home Assistant, commands are sent to Firebase which pushes them to your devices

**Schedule-Aware Temperature Display**: In AUTO mode, the integration reads the device's schedule to determine the current target temperature (Comfort/Eco/Off) based on the time of day, rather than relying on potentially stale cached values.

### Legacy Platform (Older Devices)

The Legacy platform uses traditional REST API polling:

1. **Authentication** - OAuth-style authentication with Rointe's servers
2. **Polling** - Device states are fetched periodically (configurable interval)
3. **Commands** - Settings changes are sent via REST API calls

### Data Flow

| Direction | Nexa | Legacy |
|-----------|------|--------|
| State updates | Polling (15s, shared WebSocket) | Polling (15s) |
| Commands | Instant | Instant |
| Schedule data | Synced from device | Synced from device |

### Heating State Detection

The integration determines whether a radiator is actively heating based on temperature differential rather than device-reported state (which can be stale):

- **Heating**: Target temperature > Current temperature (and power is on)
- **Idle**: Current temperature ≥ Target temperature
- **Off**: Device power is off

This approach ensures the UI accurately reflects what the radiator is doing, even when cloud data is delayed.

## Supported Devices

- Series-D Radiators
- Belize and Olympia Radiators (Nexa)
- Series-D Towel Rails
- Oval Towels (Nexa)
- Thermostats

## Features

- **Climate Control** - Set target temperatures, switch between heating modes
- **Presets** - Use Eco, Comfort, or Manual modes
- **Temperature Sensors** - Monitor current room temperature from each device
- **Power Monitoring** - Track current power consumption and energy usage
- **Firmware Updates** - Get notified when device firmware updates are available
- **Schedule Support** - Works with the device's built-in scheduling system

## Installation

Version 3.2.3 requires Home Assistant 2026.10.4 or newer.

### HACS (Recommended)

[![Open your Home Assistant instance and open a repository inside the Home Assistant Community Store.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=ermitovski&repository=rointe_integration&category=integration)

1. Click the button above, or manually:
   1. Open HACS in Home Assistant
   2. Click the three dots in the top right corner
   3. Select **Custom repositories**
   4. Add the repository URL: `https://github.com/ermitovski/rointe_integration`
   5. Select **Integration** as the category
   6. Click **Add**
2. Search for "Rointe" in HACS and install it
3. Restart Home Assistant

### Manual Installation

1. Download the [latest release](https://github.com/ermitovski/rointe_integration/releases)
2. Extract the `custom_components/rointe` folder to your Home Assistant `config/custom_components/` directory
3. Restart Home Assistant

## Configuration

### Adding the Integration

1. Go to **Settings** → **Devices & Services**
2. Click **Add Integration**
3. Search for **Rointe Heaters**
4. Follow the configuration steps:
   - Enter your Rointe account email
   - Enter your Rointe account password
   - Select your API type (Nexa or Legacy)

### API Types

| API Type | Description |
|----------|-------------|
| **Nexa** | For newer Rointe devices using the Nexa platform. Polls over a shared WebSocket connection. |
| **Legacy** | For older Rointe devices using the original REST API. |

If you're unsure which to use, try Nexa first. If your devices don't appear, reconfigure with Legacy.

## Entities

After setup, the following entities are created for each radiator:

### Climate

| Entity | Description |
|--------|-------------|
| `climate.{device_name}` | Main climate control entity |

**Supported Features:**

- Set target temperature
- Switch HVAC modes (Heat, Off)
- Select presets (Eco, Comfort, Manual)

### Sensors

| Entity | Description |
|--------|-------------|
| `sensor.{device_name}_temperature` | Current room temperature |
| `sensor.{device_name}_power` | Current power consumption (W) |
| `sensor.{device_name}_energy` | Total energy consumption (kWh) |
| `sensor.{device_name}_effective_power` | Real-time power draw |

### Binary Sensors

| Entity | Description |
|--------|-------------|
| `binary_sensor.{device_name}_heating` | Whether the radiator is actively heating |
| `binary_sensor.{device_name}_window_open` | Window open detection (if supported) |

### Switches

| Entity | Description |
|--------|-------------|
| `switch.{device_name}_power` | Turn radiator on/off |
| `switch.{device_name}_lock` | Child lock |

### Update

| Entity | Description |
|--------|-------------|
| `update.{device_name}_firmware` | Firmware update availability |

Firmware update entities are available only with the legacy API. Nexa does not
provide a firmware update catalogue through the integration; existing Nexa
firmware update entities are removed when the integration loads.

## Services

The integration provides custom services for advanced control:

### `rointe.set_preset`

Set a specific preset mode on a device.

| Parameter | Required | Description |
|-----------|----------|-------------|
| `entity_id` | Yes | Climate entity to control |
| `preset` | Yes | Preset name (eco, comfort, manual) |

### `rointe.set_temperature`

Set the target temperature for a specific mode.

| Parameter | Required | Description |
|-----------|----------|-------------|
| `entity_id` | Yes | Climate entity to control |
| `temperature` | Yes | Target temperature |
| `mode` | No | Mode to set temperature for |

## Schedule details

Each radiator's Schedule Status sensor includes today's raw schedule, grouped
Comfort/Eco/Off/Ice hours, and the next known mode change. `next_change_at` includes
the date and timezone offset, including changes after midnight. The
`schedule_active` attribute distinguishes an active schedule from one being
inspected while the radiator is off or in manual mode. Schedules use Home
Assistant's configured timezone and support both Nexa and Connect data formats.

## Nexa energy, cost and savings

Nexa statistics come from the v4 API filtered by the radiator's serial number.
The energy sensor accumulates observed daily readings into a persistent running
total in kWh; Energy Cost and Energy Saving report the API's daily euro amounts when available. Devices are not
assigned an estimated share of installation or zone totals.

Statistics are cached per radiator for five minutes, separately from the
15-second state refresh, and refreshed at the start of a new local day. Missing
or invalid statistics produce an unknown value instead of invented consumption.
Nexa v4 statistics are covered by mocked API tests; verify readings against the Rointe app on your installation.

The Nexa energy total survives Home Assistant restarts. Its attributes retain the
last daily reading, its local date, and the cumulative offset. A new calendar day
starts a new accumulation period; a lower correction within the same day does
not count as another reset. Existing energy entity IDs are preserved. The total
starts from the readings observed by this integration: consumption missed while
Home Assistant is offline is not reconstructed. Connect energy behavior and daily
cost/savings amounts remain unchanged.

## Energy Dashboard

The integration provides energy sensors compatible with Home Assistant's Energy Dashboard.

To add your radiators to the Energy Dashboard:

1. Go to **Settings** → **Dashboards** → **Energy**
2. Click **Add Consumption**
3. Select `sensor.{device_name}_energy` for each radiator

## Troubleshooting

### Devices not appearing

- Verify your Rointe account credentials are correct
- Try switching between Nexa and Legacy API types
- Check the Home Assistant logs for error messages

### Connection issues

- The integration uses cloud polling; ensure your Home Assistant instance has internet access
- Check if the Rointe servers are accessible (try logging into the Rointe app)

### Entity states show "unavailable"

- This usually indicates a temporary connection issue
- The integration will automatically reconnect when the connection is restored

### Debug Logging

To enable debug logging, add this to your `configuration.yaml`:

```yaml
logger:
  default: info
  logs:
    custom_components.rointe: debug
```

### Validate all integration fields locally

The general validator reads device fields, storage-zone commands, installation
holidays and timezone, firmware metadata, and SDK-parsed energy statistics. It
uses the same gitignored credentials file and dependencies as the timeout helper
below. Once those are set up, run:

```sh
.venv/bin/python scripts/validate_fields.py --timezone Europe/Madrid
```

Use Home Assistant's timezone for schedule interpretation and daily energy
grouping. Without `--timezone`, the helper uses this computer's current local
timezone. Add `--expected-timeout 30` to compare every device with the app's
selected display timeout; no expected timeout is assumed by default.

The gitignored `.rointe-validation.json` report includes every catalogued field's
presence, raw type, value, expected shape/range, and parsed model value where one
exists. It identifies missing/null fields, defaults, numeric text, invalid values,
and family-specific controls that do not apply. Missing optional fields are normal
and do not make the check fail. `setting_supported` describes the SDK's setting
capability check; read-only sensor visibility can have different conditions.
The field catalogue is checked against the model's reads, transport writes and
number/switch descriptions by regression tests.

The validator checks schedule formats, night times, boolean types, numeric ranges
and steps, paired charge/user limits, holiday period ordering, and derived model
state such as heating, target temperature, Boost and power reduction. It retains
raw field diagnostics if a malformed value prevents device-model construction.
Energy values are already parsed by the SDK; this report does not include raw
statistics responses. Optional statistics/catalogue failures are reported as
warnings while other device checks continue. Nexa's unavailable firmware-update
catalogue is not queried.

Credentials, tokens and unrelated response fields are omitted. Serial numbers,
SSID names and installation/zone identifiers are redacted; device names and
schedules are included. Only authentication and reads are performed. Exit codes
are `0` for completed checks with no invalid values or requested mismatches,
`1` for invalid values, mismatches or required-read/model failures, and `2` for
local setup/file errors. Physical device behavior, Home Assistant's entity
lifecycle and restored cumulative-energy history require separate checks.

### Validate only a display timeout locally

The read-only helper uses the bundled SDK without installing Home Assistant.
From this repository, with Python 3.10 or newer:

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r scripts/requirements.txt
.venv/bin/python scripts/validate_display_timeout.py --init
```

Fill in `username` (your account email) and `password` in the generated
`.rointe-credentials.json`. Keep `api_type` as `nexa` for the Nexa app; `rointe`
and `auto` are also supported. The file is gitignored and created with permissions
that restrict access to your user. Enter credentials as JSON strings, escaping
any embedded double quotes or backslashes. The helper never executes file contents.

With the Rointe app showing 30 seconds, run:

```sh
.venv/bin/python scripts/validate_display_timeout.py --expected 30
```

For each device, the report shows the raw `backlight_time`, its type, whether the
integration supports the setting, and how the original and fixed parsers interpret
it. A text value `"30"` confirms numeric-text parsing is relevant; a numeric `30`
already worked before the fix and points to a different cause. The reported
15-second Nexa default is recognised as `15 seconds`, not converted to 30. The
report marks it `reported_only`, since the app's buttons do not offer 15.
Missing, null or unrecognised values are reported without inventing a timeout.

The helper only reads settings and saves a gitignored `.rointe-display-timeout.json`
report. Reports include device names and selected timeout diagnostics, but exclude
credentials, tokens, serial numbers and unrelated device data. To check updates,
change the value in the Rointe app, wait around 30 seconds and rerun with the new
`--expected` value (`0`, `10`, `15` or `30`; 15 checks the reported default).
All discovered devices are compared against
that value; different settings on other devices can produce expected mismatches.

Exit codes: `0` means all device values match, `1` means a mismatch, unsupported
setting or cloud-read failure, and `2` means a local setup or file error. A matching
report validates the cloud value and shared parser; checking Home Assistant after
installing the change is still needed to confirm the entity updates correctly.

## Migration from Previous Versions

If you previously used an older version of this integration:

1. Remove the old integration from **Settings** → **Devices & Services**
2. Delete the `custom_components/rointe` folder
3. Delete any `custom_components/rointesdk` folder if present
4. Install the new version via HACS
5. Restart Home Assistant
6. Add the integration again

**Note:** Entity IDs may change after migration. Update any automations, scripts, or dashboards that reference the old entity IDs.

## Issues

Found a bug or have a feature request? Please open an issue on GitHub:

**[Open an Issue](https://github.com/ermitovski/rointe_integration/issues)**

When reporting a bug, please include:

- Home Assistant version
- Integration version
- Debug logs (see [Debug Logging](#debug-logging))
- Steps to reproduce the issue

## Contributing

Contributions are welcome via pull requests.

1. Fork the repository
2. Create a feature branch (`git checkout -b feature/my-feature`)
3. Commit your changes (`git commit -am 'Add new feature'`)
4. Push to the branch (`git push origin feature/my-feature`)
5. Open a Pull Request

**[View Open Pull Requests](https://github.com/ermitovski/rointe_integration/pulls)**

### Development Setup

```bash
# Clone your fork
git clone https://github.com/ermitovski/rointe_integration.git

# Copy to HA custom_components for testing
cp -r custom_components/rointe /path/to/ha/config/custom_components/

# Restart Home Assistant to test changes
```

## License

This project is licensed under the [MIT License](LICENSE), retaining Joshua Yewman's copyright notice.
The bundled SDK includes code from [tggm/rointe-sdk](https://github.com/tggm/rointe-sdk),
also under the [MIT License](custom_components/rointe/rointesdk/LICENSE), copyright 2022 Tiago Matias.
Both license notices are included in the integration folder so they accompany installations and release ZIPs.

## Acknowledgements

- Joshua Yewman ([@JYewman](https://github.com/JYewman)) for the upstream integration
- Tiago Matias ([@tggm](https://github.com/tggm)) for the [original HACS integration](https://github.com/tggm/rointe-radiators) and [Rointe SDK](https://github.com/tggm/rointe-sdk)
- Thanks to all contributors who have helped improve this integration
- Rointe for manufacturing quality electric radiators

## Device model identification and optional controls

Nexa identifies a device with `product_type`, `product_brand`, `product_model`
and `product_version`. The type codes are 1 radiator, 2 towel rail, 3 water
heater, 4 thermostat and 5 storage heater. Home Assistant shows the family and
brand/model codes when available. These codes are not guaranteed retail names;
unknown models are not labelled as Belize, Olympia or Onyx by guesswork.
Connect retains its existing type/version names and entity IDs.

Optional controls require a non-null field in the device response. For Nexa's
known numeric families, the integration also checks the restrictions used by
the web app, so unused fields in shared firmware do not automatically enable
controls. Unknown numeric families expose only basic preset settings.

Optional status sensors also require their reported fields. Missing timer,
presence, pilot-wire, lock and connectivity fields no longer create default
off/unlocked/connected statuses. Presence and timer sensors follow the same
model restrictions as their controls. A reported local-lock status remains
readable even on models where changing that lock is unsupported. Surface
temperature and Wi-Fi signal sensors are discovered when their readings are
reported, and Legionella Protection Running recognizes the `acs` water-heater
family. Supported sensors can be discovered on later refreshes; previously saved
optional sensors become unavailable while their required fields are absent.

Protocol reference: the public [Rointe Nexa web app](https://rointenexa.com/),
inspected 2026-10-25 (build `8b8da08c-7177-4d97-884d-4a80b88951d4`). Its
`DeviceConfig` selects settings using the three numeric product codes. There
is no assumed mapping from these codes to every retail model.

### Nexa Boost timer

`rointe.activate_boost` and `rointe.deactivate_boost` use the current Nexa timer
protocol. Duration is 5–240 minutes; temperature is optional and otherwise uses
the device's saved Boost temperature. The end time is an absolute UTC timestamp,
so the remaining-time sensors no longer expose an epoch as a duration. Boost
sets manual mode, matching the Nexa app; ending Boost does not automatically
restore a previous weekly programme. Select Auto afterwards when required.
Only devices reporting supported timer fields offer this operation. Protocol
writes are covered by mocked tests and still need live hardware validation.

### Optional comfort and display controls

Supported devices expose Early Start, Night Temperature Reduction, Night Start
and Night End, Display Brightness (1–10), Standby Brightness (0–10), Sound Volume
(0–10), and Display Timeout (always on, 10 or 30 seconds). Zero volume mutes the
buzzer. The existing Do Not Disturb switch enables the night period; temperature
reduction is a separate setting. Start/end times use the device's local clock,
and an end earlier than the start represents a period crossing midnight.

Only reported, compatible settings are created. Changes preserve the device's
other reported night/display settings in the same command. Early start lets the
device preheat for its own schedule; it does not create a Home Assistant timer.

Nexa devices can also report a 15-second display timeout. The public Nexa web app
defines 15 as its default while offering only 0, 10 and 30 as button choices.
Home Assistant shows `15 seconds` when that value is reported, and keeps it intact
when changing brightness or sound. This extra choice disappears after a device
reports a standard app choice. Selecting the already reported `15 seconds` does
not send a command; changing a different timeout to 15 is not offered. There is
no verified conversion from a stored 15 to the app's 30-second button, so the
integration reports the cloud value directly.

A live app round trip confirmed that selecting 10 and then 30 updates the device
record to literal 10 and 30, while its room record can remain at 15. The timeout
entity therefore continues to use the individual device record. An app button's
highlight is not sufficient evidence of the persisted device value when these
default settings disagree.

### Nexa holiday schedules

`rointe.set_holiday_schedule` replaces the selected heater's **entire installation**
holiday schedule and enables it. Every heater in that Rointe installation is
affected. Pass `periods` as an ordered, non-overlapping list; each period has
`from` and `to` ISO datetimes with timezone offsets. Only the last `to` can be
null for an indefinite absence. For example:

```yaml
action: rointe.set_holiday_schedule
target:
  entity_id: climate.living_room
data:
  periods:
    - from: "2026-12-20T08:00:00+01:00"
      to: "2026-12-28T18:00:00+01:00"
```

`rointe.clear_holiday_schedule` removes all periods and disables the installation
schedule. Selecting several heaters in one installation applies the operation
once. The Holiday Schedule sensor reports the master flag and periods, which
are returned by Rointe in UTC. Enabled means that scheduling is enabled; it does
not necessarily mean the current time falls in an absence period.

The integration converts offsets to UTC and checks the server's master flag
before toggling it. An uncertain toggle is not replayed automatically. On an
unknown outcome, refresh the integration and inspect the Rointe app before
retrying. Rointe's cloud holds these periods and executes them independently
of Home Assistant. Live holiday transitions remain to be verified.

### Nexa storage-heater charging

Storage heaters are discovered using `product_type: 5` or the `storageheater`
type, without assuming every device is an Onyx. Charge Level and Charging remain
physical readings from the device. Optional configuration is read from, and
written to, the owning **Rointe zone**; every storage heater sharing that zone
can be affected. Charging controls are named Zone Minimum/Maximum Charge, Zone
Smart Charging, Zone Additional Charge, and Zone Extra Element in each mode.
Storage-heater Silence Mode also applies to the zone.

Charge limits follow the app's ranges: minimum 0–30%, maximum 60–100%, in 5%
steps. Both values are written together using fresh zone state. They bound the
Smart Charging target, and are not a way to force charging off. Additional
Charge is an allowance of 0–7 hours outside the scheduled window. Extra Element
switches control permission to use direct heating in Auto, Manual, Boost and
Holiday modes. Turning off direct heating permits stored-heat-only operation
in that mode; it does not claim to measure power.

`rointe.set_charging_schedule` accepts partial Monday–Sunday days numbered 0–6,
with 24 characters per day: `1` allows charging, `0` disallows it. The action
selects custom programming and preserves omitted days. For example:

```yaml
action: rointe.set_charging_schedule
target:
  entity_id: climate.storage_heater
data:
  schedule:
    "0": "111111100000000000000000"
```

Smart Charging decides how much to charge within the permitted hours; Additional
Charge allows spill outside them. Use Rointe's daily consumption statistics for
energy. The integration leaves instantaneous storage-heater power unknown when
there is no measured value, rather than estimating it from charging state or
nameplate wattage. Command acknowledgement confirms a cloud write, not that the
heater has already applied it. Storage writes need verification on live hardware.

The zone-command approach is also described in the community's
[Onyx field observations](https://github.com/Atmaybury-25/rointe-nexa-hacs/blob/main/docs/onyx-field-map.md).
It is limited to these explicit storage configuration controls; individual
radiator commands keep their existing device destination.

### Nexa installed-power reduction

Compatible radiators expose a **Power Reduction** number from 0–30%, in 5% steps,
and a diagnostic **Configured Power** sensor in watts. A 20% reduction changes
an original 1,500 W rating to 1,200 W; changing to 10% then sets 1,350 W, using
the original rating rather than reducing the previous result again. Set 0% to
restore the original rating. Configured Power is a setting, not a measurement
of instantaneous consumption, and keeps fractional watts when necessary.

The integration follows the current Nexa app's capability list: `product_type`
1, `product_brand` 1, and `product_model` 20–23 or 25–32. Other models, including
model 24 and storage heaters, do not get this control just because shared
firmware contains the same field. Both `nominal_power` and
`nominal_effective_power` must report finite, positive values.

To apply the same reduction to every eligible radiator discovered in an
installation, select any climate entity belonging to that installation:

```yaml
action: rointe.set_installation_power_reduction
target:
  entity_id: climate.living_room
data:
  reduction: 20
```

This action also affects eligible radiators that were not individually selected.
Unsupported models are skipped; an installation with no eligible radiators
returns an error. Selecting multiple heaters in one installation applies the
operation once. New devices become eligible after normal discovery refreshes.

The integration reads each eligible device's current model and nominal rating
before any writes for that installation. A failed preflight sends no changes.
Writes are acknowledged separately for each radiator, so an installation update
is not atomic. A later failure reports the number of earlier acknowledged
writes, refreshes the displayed state, and stops; it does not silently roll back
or continue. Multiple selected installations are processed separately.

Protocol reference: Nexa's public
[installed-power adjustment code](https://rointenexa.com/_nuxt/ufbF_FP9.js) and
[model definitions](https://rointenexa.com/_nuxt/B-QLPcdT.js), inspected
2026-10-25. The app calculates `nominal_effective_power` from `nominal_power`
and writes it to `/devices/{serial}/data`. These controls reproduce that cloud
configuration; actual electrical power limiting still needs hardware validation.

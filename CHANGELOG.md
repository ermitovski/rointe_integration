# Changelog

## Unreleased

### Fixed
- Recognise the 15-second display timeout reported by Nexa devices instead of showing unknown. Preserve this existing default when changing brightness or sound; new timeout commands remain limited to the app's always-on, 10-second and 30-second choices. Numeric text values are also recognised.

## v3.2.2 (2026-10-25)

### Fixed
- Remove unsupported firmware update entities for Nexa accounts instead of showing Unknown; legacy firmware update checks and installed firmware version reporting are retained (#21).
- Require reported fields for optional lock, connectivity, pilot-wire, window, surface-temperature and Wi-Fi statuses, and apply model restrictions to presence and timer sensors.
- Preserve reported false values, read-only local-lock status and discovery of newly reported sensors without duplicate entities.
- Recognize Nexa's `acs` water-heater family for Legionella Protection Running.

### Installation
- Update through HACS and restart Home Assistant. Requires Home Assistant 2026.10.4 or newer.
- Existing Nexa firmware update entries are removed when the integration loads. Previously saved optional sensors become unavailable while their required data is absent.

### Validation
- 297 automated regression tests pass against Home Assistant 2026.10.4 / Python 3.14, with mocked cloud responses.

## v3.2.1 (2026-10-25)

### Fixed
- Restore the Configured Power and Power Reduction icons by replacing invalid Material Design Icon names with supported icons.

### Installation
- Update through HACS and restart Home Assistant. Requires Home Assistant 2026.10.4 or newer.

## v3.2.0 (2026-10-25)

### Added
- Identify Nexa devices using product type, brand and model codes, and expose optional controls only when the device reports compatible capabilities (#14).
- Early Start, night start/end times and temperature reduction, display brightness/timeout and sound-volume controls (#16).
- Installation-wide Nexa holiday scheduling and status, with timezone-aware periods and verified master-flag toggles (#17).
- Storage-heater charge limits, Smart Charging, additional charging hours, direct-element permissions and partial charging schedules (#18).
- Per-radiator installed-power reduction from 0–30% in 5% steps, configured-wattage reporting, and an action to apply the same percentage across a Nexa installation (#19).

### Fixed
- Use Nexa's timer protocol for Boost commands, target temperatures and remaining-time sensors. Boost enters manual mode, matching the Nexa app; ending Boost does not automatically restore Auto (#15).
- Verify Nexa WebSocket certificates and hostnames, renew expired discovery credentials, and bound legacy HTTP waits (#9–11).
- Handle missing firmware metadata and respect each temperature control's declared or active user limits (#12–13).
- Preserve fractional configured wattages and leave storage-heater instantaneous power unknown when no measured value is available.

### Scope and compatibility
- Requires Home Assistant 2026.10.4 or newer. Update through HACS and restart Home Assistant.
- Holiday actions affect the entire Rointe installation. Storage configuration controls affect the owning Rointe zone.
- Installed-power reduction is available for the Nexa app's compatible radiator models. Installation-wide adjustments overwrite individual reductions on eligible discovered radiators; unsupported models are skipped. Set 0% to restore original ratings.
- Existing entity IDs are retained. Optional controls without a reported compatible capability may become unavailable.

### Validation
- 285 automated regression tests pass against Home Assistant 2026.10.4 / Python 3.14; HACS and Hassfest validation pass.
- Cloud responses are mocked. Live heater behavior, holiday transitions, storage charging and electrical power reduction have not yet been independently verified on hardware.
- A cloud write acknowledgement confirms receipt, not physical application. Multi-radiator power adjustments are not atomic and report partial failures.

## v3.1.1 (2026-10-25)

### Improved
- Reuse one authenticated Nexa WebSocket for polling and atomic commands, with keep-alives and bounded reconnection (#8). Adapted from alex1075's proposal in [JYewman/rointe_integration#9](https://github.com/JYewman/rointe_integration/pull/9).
- Close the connection permanently on unload, failed setup, and Home Assistant shutdown; unfinished requests cannot reopen it.
- Preserve schedule, boost, lock, temperature-limit validation and explicit write acknowledgements. State updates still poll every 15 seconds.

### Packaging
- Preserve upstream integration and SDK attribution, and include both MIT license notices in the installation ZIP (#7).

### Validation notes
- 95 regression tests pass against Home Assistant 2026.10.4; HACS and Hassfest validation pass for the connection changes.
- Cloud connections are mocked; live radiator behavior remains to be verified.
- Requires Home Assistant 2026.10.4 or newer. Update through HACS and restart Home Assistant.

## v3.1.0 (2026-10-25)

### Added
- Per-radiator Nexa v4 consumption, daily cost, and savings statistics, with independent caching and token renewal (#4).
- Today's schedule periods and the next change across midnight and daylight-saving transitions (#5).
- Persistent Nexa energy totals across daily resets and Home Assistant restarts (#6).
- A regression suite with 71 passing tests against Home Assistant 2026.10.4.

### Fixed
- Deliver schedule, boost, lock, and temperature-limit commands; require an explicit Firebase acknowledgement and surface failures (#1).
- Respect disabled frost protection in scheduled off periods and keep climate, power, and heating status consistent (#2).
- Retain radiators across refreshes and discover newly relevant entities without reloading (#3).

### Installation
- Requires Home Assistant 2026.10.4 or newer, matching the tested baseline.
- Add `https://github.com/ermitovski/rointe_integration` to HACS as an Integration, install version 3.1.0, then restart Home Assistant.
- Existing energy entity unique IDs are preserved.

### Validation notes
- Automated checks use mocked cloud responses; live Nexa v4 statistics and radiator command behavior have not been verified on hardware.
- Persistent totals accumulate observed daily readings and cannot reconstruct consumption missed while Home Assistant was offline.

## v3.0.5 (2026-09-17)

### Fixed
- **Deprecated `device_registry.async_get_device` call** — HA now warns that `async_get_device` is deprecated because device identifiers are no longer unique across config entries, and will stop working in HA Core 2027.8.0. `device_update_info` now calls `async_get_device_by_identifier` with the coordinator's `config_entry_id`, which requires the coordinator to be constructed with the config entry (passed through as `config_entry` to `DataUpdateCoordinator`, matching current HA conventions).

## v3.0.4 (2026-05-06)

### Fixed
- **Climate entity stuck at OFF for Connect (legacy) radiators** — `RointeDevice.update_data` was decoding the `power` field as a Nexa integer code (1=standby, 2=heating). Because `bool` is a subclass of `int` in Python, `int(True)` yields `1`, so a Connect radiator reporting `power=True` was being misread as Nexa standby and `self.power` was set to `False` on every refresh. The climate entity therefore reported `HVACMode.OFF` and `target_temperature=None`, even though sensors and the preset switch (which don't read `power`) updated correctly. Now we check `isinstance(power_val, bool)` before the integer branch so Connect's boolean is trusted as-is and Nexa's integer codes still decode correctly.

## v3.0.3 (2026-05-03)

### Fixed
- **Nexa energy sensor froze after the first refresh** — `_nexa_installation_energy` was cached on first fetch but never invalidated, so every subsequent refresh cycle returned the same kWh value forever. The device manager now invalidates the cache at the start of each cycle, and the per-device fetch holds a lock while repopulating it so all devices in the same cycle share a single HTTP call instead of stampeding the Nexa statistics endpoint.

## v3.0.2 (2026-05-03)

### Fixed
- **Schedule status sensor crash on legacy (list-format) schedules** — `_get_schedule_status` assumed the schedule was always a dict (Nexa format) and called `.get()` on it, raising `AttributeError: 'list' object has no attribute 'get'` for legacy accounts. Now delegates to `RointeDevice.get_current_schedule_mode()`, which already handles both list and dict formats.

## v3.0.1 (2026-03-17)

### Fixed
- **Nexa WebSocket commands now include `last_sync_datetime_app` timestamp** — radiators were ignoring commands sent via WebSocket because the sync timestamp was missing, causing them to treat updates as stale data rather than fresh commands from the app.
- **WebSocket auth response verification** — both read and write operations now properly wait for and verify the Firebase auth response before sending commands, instead of blindly proceeding after a 300ms delay.
- **Automatic re-authentication on token expiry** — when the Firebase refresh token expires, the integration now performs a full re-login using stored credentials instead of silently failing with "Permission denied" errors.
- **Permission denied retry logic** — if a WebSocket read or write gets a "Permission denied" response, the integration forces a token refresh and retries once before giving up.

## v3.0.0 (2026-02-17)

### Added
- Full Nexa API support (Rointe's new cloud platform)
- Auto-detection of legacy vs Nexa accounts
- WebSocket-based real-time communication for Nexa devices
- Binary sensors (window open, boost active, timer active, heating, connected, etc.)
- Number entities for comfort/eco/frost protection temperatures
- Switch entities for window detection, silence mode, lock controls, etc.
- Schedule-aware target temperature in AUTO mode
- Energy consumption tracking via Nexa statistics API
- Firmware update detection

### Changed
- Derive HVAC action from temperature differential instead of stale Firebase status
- Improved device discovery and zone mapping
- Better error handling throughout

## v2.2.2

- Bug fixes and stability improvements

## v2.2.1-1

- Minor fixes

## v2.1.0

- Added additional sensor entities
- Improved error handling

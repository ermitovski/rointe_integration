"""Services for Rointe integration."""

from __future__ import annotations

import logging

import voluptuous as vol

from homeassistant.const import ATTR_ENTITY_ID
from homeassistant.exceptions import HomeAssistantError
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.helpers import config_validation as cv, entity_registry as er

from .const import DOMAIN
from .rointesdk.power import validate_reduction

_LOGGER = logging.getLogger(__name__)

ATTR_DURATION = "duration"
ATTR_TEMPERATURE = "temperature"
ATTR_SCHEDULE = "schedule"

SERVICE_ACTIVATE_BOOST = "activate_boost"
SERVICE_DEACTIVATE_BOOST = "deactivate_boost"
SERVICE_SET_SCHEDULE = "set_schedule"

SERVICE_ACTIVATE_BOOST_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_ENTITY_ID): cv.entity_ids,
        vol.Required(ATTR_DURATION, default=60): vol.All(
            vol.Coerce(int), vol.Range(min=5, max=240)
        ),
        vol.Optional(ATTR_TEMPERATURE): vol.All(
            vol.Coerce(float), vol.Range(min=15, max=30)
        ),
    }
)

SERVICE_DEACTIVATE_BOOST_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_ENTITY_ID): cv.entity_ids,
    }
)

SERVICE_SET_SCHEDULE_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_ENTITY_ID): cv.entity_ids,
        vol.Required(ATTR_SCHEDULE): dict,
    }
)


async def async_setup_services(hass: HomeAssistant) -> None:
    """Register advanced controls and surface rejected device commands."""

    def resolve_device(entity_id):
        registry = er.async_get(hass)
        entity = registry.async_get(entity_id)
        if entity is None:
            raise HomeAssistantError(f"Unknown entity: {entity_id}")
        coordinator = hass.data.get(DOMAIN, {}).get(entity.config_entry_id)
        if coordinator is None:
            raise HomeAssistantError(
                f"Rointe integration is unavailable: {entity_id}"
            )
        device = next(
            (
                device
                for did, device in coordinator.devices.items()
                if entity.unique_id
                in {did, f"rointe-{did}", f"rointe-{did}-climate"}
            ),
            None,
        )
        if device is None:
            raise HomeAssistantError(f"Rointe device is unavailable: {entity_id}")
        return coordinator, device

    async def apply(call: ServiceCall, action, installation_scope=False) -> None:
        seen_installations = set()
        for entity_id in call.data[ATTR_ENTITY_ID]:
            coordinator, device = resolve_device(entity_id)
            if installation_scope:
                installation_id = getattr(device, "installation_id", None)
                if not installation_id:
                    raise HomeAssistantError("This entity has no Nexa installation")
                if installation_id in seen_installations:
                    continue
                seen_installations.add(installation_id)
            result = await hass.async_add_executor_job(
                action, coordinator.rointe_api, device
            )
            if not result.success:
                raise HomeAssistantError(
                    result.error_message or "Device command failed"
                )
            await coordinator.async_request_refresh()

    async def activate_boost(call: ServiceCall) -> None:
        await apply(
            call,
            lambda api, device: _activate_boost(
                api,
                device,
                call.data[ATTR_DURATION] * 60,
                call.data.get(ATTR_TEMPERATURE),
            ),
        )

    async def deactivate_boost(call: ServiceCall) -> None:
        await apply(call, _deactivate_boost)

    async def set_schedule(call: ServiceCall) -> None:
        await apply(
            call,
            lambda api, device: _set_schedule(api, device, call.data[ATTR_SCHEDULE]),
        )

    async def set_holiday_schedule(call: ServiceCall) -> None:
        await apply(call, lambda api, device: api.set_holiday_schedule(
            device.installation_id, call.data["periods"]), installation_scope=True)

    async def clear_holiday_schedule(call: ServiceCall) -> None:
        await apply(call, lambda api, device: api.set_holiday_schedule(
            device.installation_id, []), installation_scope=True)

    async def set_charging_schedule(call: ServiceCall) -> None:
        await apply(call, lambda api, device: api.set_storage_settings(
            device, {"schedule_charging": call.data[ATTR_SCHEDULE]}))

    async def set_installation_power_reduction(call: ServiceCall) -> None:
        plans = {}
        for entity_id in call.data[ATTR_ENTITY_ID]:
            coordinator, selected = resolve_device(entity_id)
            installation_id = getattr(selected, "installation_id", None)
            if not installation_id or not coordinator.rointe_api.use_nexa_ws:
                raise HomeAssistantError("Power adjustment requires a Nexa installation")
            devices = tuple(coordinator.devices.values())
            if not any(device.installation_id == installation_id and
                       device.supports_setting("nominal_effective_power") for device in devices):
                raise HomeAssistantError("No supported radiators were found in this installation")
            plans.setdefault(installation_id, (coordinator, devices))
        for installation_id, (coordinator, devices) in plans.items():
            result = await hass.async_add_executor_job(
                coordinator.rointe_api.set_installation_power_reduction,
                installation_id, devices, call.data["reduction"],
            )
            # A failed multi-device action can still have acknowledged earlier writes.
            await coordinator.async_request_refresh()
            if not result.success:
                raise HomeAssistantError(result.error_message or "Unable to adjust installation power")

    for service, handler, schema in (
        ("set_installation_power_reduction", set_installation_power_reduction, vol.Schema({
            vol.Required(ATTR_ENTITY_ID): cv.entity_ids,
            vol.Required("reduction"): validate_reduction,
        })),
        ("set_charging_schedule", set_charging_schedule, SERVICE_SET_SCHEDULE_SCHEMA),
        ("set_holiday_schedule", set_holiday_schedule, vol.Schema({
            vol.Required(ATTR_ENTITY_ID): cv.entity_ids, vol.Required("periods"): [dict],
        })),
        ("clear_holiday_schedule", clear_holiday_schedule, SERVICE_DEACTIVATE_BOOST_SCHEMA),
        (SERVICE_ACTIVATE_BOOST, activate_boost, SERVICE_ACTIVATE_BOOST_SCHEMA),
        (SERVICE_DEACTIVATE_BOOST, deactivate_boost, SERVICE_DEACTIVATE_BOOST_SCHEMA),
        (SERVICE_SET_SCHEDULE, set_schedule, SERVICE_SET_SCHEDULE_SCHEMA),
    ):
        hass.services.async_register(DOMAIN, service, handler, schema=schema)


def _activate_boost(api, device, duration_seconds: int, temperature: float | None):
    """Activate boost through the configured backend."""
    return api.set_device_boost(device, duration_seconds, temperature)


def _deactivate_boost(api, device):
    """Deactivate boost through the configured backend."""
    return api.set_device_boost(device, 0, None)


def _set_schedule(api, device, schedule: dict):
    """Set a partial weekly schedule through the configured backend."""
    return api.set_device_settings(device, {"schedule": schedule})

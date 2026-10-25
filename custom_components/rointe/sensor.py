"""Sensor platform for Rointe devices."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.typing import StateType

from .discovery import setup_description_entities
from .const import DOMAIN
from .energy import DailyEnergyTotal
from .coordinator import RointeDataUpdateCoordinator
from .rointe_entity import RointeRadiatorEntity
from .rointesdk.device import RointeDevice
from .sensor_descriptions import SENSOR_DESCRIPTIONS, RointeSensorEntityDescription


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Rointe sensors from a config entry."""
    coordinator: RointeDataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id]

    setup_description_entities(
        entry, coordinator, async_add_entities, SENSOR_DESCRIPTIONS, _create_sensor
    )


class RointeSensor(RointeRadiatorEntity, SensorEntity):
    """Representation of a Rointe sensor."""

    entity_description: RointeSensorEntityDescription

    def __init__(
        self,
        device: RointeDevice,
        coordinator: RointeDataUpdateCoordinator,
        description: RointeSensorEntityDescription,
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator, device, unique_id=device.id)
        self.entity_description = description
        self._attr_unique_id = f"{device.id}_{description.key}"

    @property
    def native_value(self) -> StateType:
        """Return the sensor value."""
        if self._radiator is None:
            return None
        return self.entity_description.value_fn(self._radiator)

    @property
    def last_reset(self) -> datetime | None:
        """Return the last time the sensor was reset, if relevant."""
        if self._radiator is None:
            return None
        return self.entity_description.last_reset_fn(self._radiator)

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Return extra state attributes."""
        if self._radiator is None or self.entity_description.attributes_fn is None:
            return None
        return self.entity_description.attributes_fn(self._radiator)

    @property
    def available(self) -> bool:
        """Return True if entity is available."""
        if self._radiator is None:
            return False
        return (
            super().available
            and self._radiator.hass_available
            and self.entity_description.available_fn(self._radiator)
        )


def _create_sensor(device, coordinator, description):
    """Use persistent accumulation for daily Nexa energy without changing IDs."""
    if coordinator.rointe_api.use_nexa_ws and description.key == "energy":
        return RointeCumulativeEnergySensor(device, coordinator, description)
    return RointeSensor(device, coordinator, description)


class RointeCumulativeEnergySensor(RointeSensor, RestoreEntity):
    """Persist daily-energy offsets across Home Assistant restarts."""

    def __init__(self, device, coordinator, description) -> None:
        super().__init__(device, coordinator, description)
        self._total = DailyEnergyTotal()
        self._update_total()

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        if (state := await self.async_get_last_state()) is not None:
            self._total = DailyEnergyTotal.restore(state.attributes)
        self._update_total()

    def _update_total(self) -> None:
        data = self._radiator.energy_data if self._radiator else None
        if data is not None and data.daily:
            try:
                self._total.update(data.start.date(), data.kwh)
            except (TypeError, ValueError, OverflowError):
                pass

    @callback
    def _handle_coordinator_update(self) -> None:
        self._update_total()
        super()._handle_coordinator_update()

    @property
    def native_value(self) -> StateType:
        if self._radiator is None or self._radiator.energy_data is None:
            return None
        return self._total.value

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return self._total.attributes()

"""Sensor platform for Rointe devices."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.typing import StateType

from .discovery import setup_description_entities
from .const import DOMAIN
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
        entry, coordinator, async_add_entities, SENSOR_DESCRIPTIONS, RointeSensor
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

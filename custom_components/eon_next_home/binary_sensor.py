"""Binary sensor entities for E.ON Next EV Smart Charging."""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import ATTRIBUTION, DOMAIN
from .coordinator import EonNextEVCoordinator


@dataclass(frozen=True, kw_only=True)
class EonNextEVBinarySensorDescription(BinarySensorEntityDescription):
    """Binary sensor description with a value extractor callable."""

    value_fn: Callable[[dict[str, Any]], bool | None] = lambda _: None


BINARY_SENSOR_DESCRIPTIONS: tuple[EonNextEVBinarySensorDescription, ...] = (
    EonNextEVBinarySensorDescription(
        key="charger_connected",
        name="Charger Connected",
        device_class=BinarySensorDeviceClass.PLUG,
        # True when the Ohme charger has an active OCPP connection
        value_fn=lambda d: (d.get("ocppConnection") or {}).get("isConnected"),
    ),
    EonNextEVBinarySensorDescription(
        key="smart_charging_scheduled",
        name="Smart Charging Scheduled",
        icon="mdi:calendar-clock",
        # True when there are upcoming planned dispatch windows
        value_fn=lambda d: bool(d.get("plannedDispatches")),
    ),
    EonNextEVBinarySensorDescription(
        key="device_live",
        name="Smart Charge Device Active",
        device_class=BinarySensorDeviceClass.CONNECTIVITY,
        # True when Kraken reports the device as Live
        value_fn=lambda d: (
            (d.get("registeredKrakenflexDevice") or {}).get("status", "").upper() == "LIVE"
        ),
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up E.ON Next EV binary sensor entities."""
    coordinator: EonNextEVCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        EonNextEVBinarySensor(coordinator, description)
        for description in BINARY_SENSOR_DESCRIPTIONS
    )


class EonNextEVBinarySensor(CoordinatorEntity[EonNextEVCoordinator], BinarySensorEntity):
    """A binary sensor backed by coordinator data."""

    _attr_attribution = ATTRIBUTION
    _attr_has_entity_name = True
    entity_description: EonNextEVBinarySensorDescription

    def __init__(
        self,
        coordinator: EonNextEVCoordinator,
        description: EonNextEVBinarySensorDescription,
    ) -> None:
        """Initialise the binary sensor."""
        super().__init__(coordinator)
        self.entity_description = description
        self._attr_unique_id = f"{coordinator.account_number}_{description.key}"
        self._attr_device_info = coordinator.device_info

    @property
    def is_on(self) -> bool | None:
        """Return True if the condition is active."""
        if not self.coordinator.data:
            return None
        try:
            return self.entity_description.value_fn(self.coordinator.data)
        except (KeyError, TypeError):
            return None

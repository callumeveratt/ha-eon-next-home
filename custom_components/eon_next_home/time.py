"""Time entities for E.ON Next EV Smart Charging (ready-by time picker)."""
from __future__ import annotations

import logging
from datetime import time
from typing import Any

from homeassistant.components.time import TimeEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import ATTRIBUTION, DOMAIN
from .coordinator import EonNextEVCoordinator

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up E.ON Next EV time entities."""
    coordinator: EonNextEVCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([EonNextEVReadyByTime(coordinator)])


def _parse_time(value: str | None) -> time | None:
    """Parse an "HH:MM" or "HH:MM:SS" string from the API into a time object."""
    if not value:
        return None
    try:
        parts = value.split(":")
        return time(int(parts[0]), int(parts[1]))
    except (AttributeError, IndexError, ValueError):
        return None


def _current_target_soc(coordinator: EonNextEVCoordinator) -> int:
    """Return the current target SoC as an int, falling back to 80."""
    prefs: dict[str, Any] = (
        (coordinator.data or {}).get("vehicleChargingPreferences") or {}
    )
    val = prefs.get("weekdayTargetSoc")
    try:
        return int(val) if val is not None else 80
    except (TypeError, ValueError):
        return 80


class EonNextEVReadyByTime(CoordinatorEntity[EonNextEVCoordinator], TimeEntity):
    """Time picker for the EV autopilot ready-by time."""

    _attr_attribution = ATTRIBUTION
    _attr_has_entity_name = True
    _attr_name = "Ready By"
    _attr_entity_category = EntityCategory.CONFIG
    _attr_icon = "mdi:clock-outline"

    def __init__(self, coordinator: EonNextEVCoordinator) -> None:
        """Initialise the time entity."""
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.account_number}_ready_by"
        self._attr_device_info = coordinator.device_info

    @property
    def native_value(self) -> time | None:
        """Return the current ready-by time."""
        prefs: dict[str, Any] = (
            (self.coordinator.data or {}).get("vehicleChargingPreferences") or {}
        )
        return _parse_time(prefs.get("weekdayTargetTime"))

    async def async_set_value(self, value: time) -> None:
        """Write the new ready-by time via the E.ON Next REST API."""
        new_time_str = value.strftime("%H:%M")
        target_soc = _current_target_soc(self.coordinator)

        _LOGGER.debug("Setting ready-by time to %s", new_time_str)

        await self.coordinator.async_set_preferences(
            departure_time=new_time_str,
            target_soc=target_soc,
        )
        await self.coordinator.async_request_refresh()

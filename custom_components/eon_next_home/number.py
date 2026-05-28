"""Number entities for E.ON Next EV Smart Charging (target SoC slider)."""
from __future__ import annotations

import logging
from datetime import time
from typing import Any

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import PERCENTAGE
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
    """Set up E.ON Next EV number entities."""
    coordinator: EonNextEVCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([EonNextEVSocNumber(coordinator)])


def _current_ready_by_time(coordinator: EonNextEVCoordinator) -> str:
    """Return the current ready-by time as 'HH:MM', falling back to '07:00'."""
    prefs: dict[str, Any] = (
        (coordinator.data or {}).get("vehicleChargingPreferences") or {}
    )
    raw: str | None = prefs.get("weekdayTargetTime")
    if raw:
        return raw[:5]
    return "07:00"


class EonNextEVSocNumber(CoordinatorEntity[EonNextEVCoordinator], NumberEntity):
    """Slider for the EV autopilot target state-of-charge."""

    _attr_attribution = ATTRIBUTION
    _attr_has_entity_name = True
    _attr_name = "Target Charge"
    _attr_entity_category = EntityCategory.CONFIG
    _attr_native_min_value = 10
    _attr_native_max_value = 100
    _attr_native_step = 5
    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_mode = NumberMode.SLIDER
    _attr_icon = "mdi:battery-charging-80"

    def __init__(self, coordinator: EonNextEVCoordinator) -> None:
        """Initialise the number entity."""
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.account_number}_target_soc"
        self._attr_device_info = coordinator.device_info

    @property
    def native_value(self) -> float | None:
        """Return the current target SoC."""
        prefs: dict[str, Any] = (
            (self.coordinator.data or {}).get("vehicleChargingPreferences") or {}
        )
        val = prefs.get("weekdayTargetSoc")
        try:
            return float(val) if val is not None else None
        except (TypeError, ValueError):
            return None

    async def async_set_native_value(self, value: float) -> None:
        """Write the new target SoC via the E.ON Next REST API."""
        departure_time = _current_ready_by_time(self.coordinator)
        target_soc = int(value)

        _LOGGER.debug("Setting target SoC to %s%%", target_soc)

        await self.coordinator.async_set_preferences(
            departure_time=departure_time,
            target_soc=target_soc,
        )
        await self.coordinator.async_request_refresh()

"""Time entities for E.ON Next EV Smart Charging (ready-by time pickers)."""
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
    async_add_entities(
        [
            EonNextEVReadyByTime(coordinator, is_weekday=True),
            EonNextEVReadyByTime(coordinator, is_weekday=False),
        ]
    )


def _parse_time(value: str | None) -> time | None:
    """Parse an "HH:MM" or "HH:MM:SS" string from the API into a time object."""
    if not value:
        return None
    try:
        parts = value.split(":")
        return time(int(parts[0]), int(parts[1]))
    except (AttributeError, IndexError, ValueError):
        return None


class EonNextEVReadyByTime(CoordinatorEntity[EonNextEVCoordinator], TimeEntity):
    """Time picker for weekday or weekend ready-by time."""

    _attr_attribution = ATTRIBUTION
    _attr_has_entity_name = True
    _attr_entity_category = EntityCategory.CONFIG
    _attr_icon = "mdi:clock-outline"

    def __init__(self, coordinator: EonNextEVCoordinator, is_weekday: bool) -> None:
        """Initialise the time entity."""
        super().__init__(coordinator)
        self._is_weekday = is_weekday
        day_label = "Weekday" if is_weekday else "Weekend"
        pref_key = "weekday" if is_weekday else "weekend"
        self._pref_key = f"{pref_key}TargetTime"
        self._attr_name = f"{day_label} Ready By"
        self._attr_unique_id = f"{coordinator.account_number}_{pref_key}_ready_by"
        self._attr_device_info = coordinator.device_info

    @property
    def native_value(self) -> time | None:
        """Return the current ready-by time."""
        prefs: dict[str, Any] = (
            (self.coordinator.data or {}).get("vehicleChargingPreferences") or {}
        )
        return _parse_time(prefs.get(self._pref_key))

    async def async_set_value(self, value: time) -> None:
        """Write the new ready-by time to Kraken, keeping the other values intact."""
        prefs: dict[str, Any] = (
            (self.coordinator.data or {}).get("vehicleChargingPreferences") or {}
        )

        new_time_str = value.strftime("%H:%M")
        weekday_time = new_time_str if self._is_weekday else (prefs.get("weekdayTargetTime") or "07:00")
        weekend_time = new_time_str if not self._is_weekday else (prefs.get("weekendTargetTime") or "09:00")
        weekday_soc = int(prefs.get("weekdayTargetSoc") or 80)
        weekend_soc = int(prefs.get("weekendTargetSoc") or 80)

        _LOGGER.debug(
            "Setting %s ready-by time to %s",
            "weekday" if self._is_weekday else "weekend",
            new_time_str,
        )

        await self.coordinator.async_set_preferences(
            weekday_time=weekday_time,
            weekend_time=weekend_time,
            weekday_soc=weekday_soc,
            weekend_soc=weekend_soc,
        )
        await self.coordinator.async_request_refresh()

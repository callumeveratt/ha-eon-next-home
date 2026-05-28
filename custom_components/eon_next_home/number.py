"""Number entities for E.ON Next EV Smart Charging (target SoC sliders)."""
from __future__ import annotations

import logging
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
    async_add_entities(
        [
            EonNextEVSocNumber(coordinator, is_weekday=True),
            EonNextEVSocNumber(coordinator, is_weekday=False),
        ]
    )


class EonNextEVSocNumber(CoordinatorEntity[EonNextEVCoordinator], NumberEntity):
    """Slider for weekday or weekend target state-of-charge."""

    _attr_attribution = ATTRIBUTION
    _attr_has_entity_name = True
    _attr_entity_category = EntityCategory.CONFIG
    _attr_native_min_value = 10
    _attr_native_max_value = 100
    _attr_native_step = 5
    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_mode = NumberMode.SLIDER
    _attr_icon = "mdi:battery-charging-80"

    def __init__(self, coordinator: EonNextEVCoordinator, is_weekday: bool) -> None:
        """Initialise the number entity."""
        super().__init__(coordinator)
        self._is_weekday = is_weekday
        day_label = "Weekday" if is_weekday else "Weekend"
        pref_key = "weekday" if is_weekday else "weekend"
        self._pref_key = f"{pref_key}TargetSoc"
        self._attr_name = f"{day_label} Target Charge"
        self._attr_unique_id = f"{coordinator.account_number}_{pref_key}_target_soc"
        self._attr_device_info = coordinator.device_info

    @property
    def native_value(self) -> float | None:
        """Return the current target SoC."""
        prefs: dict[str, Any] = (
            (self.coordinator.data or {}).get("vehicleChargingPreferences") or {}
        )
        val = prefs.get(self._pref_key)
        try:
            return float(val) if val is not None else None
        except (TypeError, ValueError):
            return None

    async def async_set_native_value(self, value: float) -> None:
        """Write the new target SoC to Kraken, keeping the other values intact."""
        prefs: dict[str, Any] = (
            (self.coordinator.data or {}).get("vehicleChargingPreferences") or {}
        )

        # Read current values (fall back to the new value if not yet loaded)
        weekday_soc = int(value if self._is_weekday else (prefs.get("weekdayTargetSoc") or value))
        weekend_soc = int(value if not self._is_weekday else (prefs.get("weekendTargetSoc") or value))
        weekday_time: str = prefs.get("weekdayTargetTime") or "07:00"
        weekend_time: str = prefs.get("weekendTargetTime") or "09:00"

        _LOGGER.debug(
            "Setting %s target SoC to %s%% (weekday=%s%%, weekend=%s%%)",
            "weekday" if self._is_weekday else "weekend",
            int(value),
            weekday_soc,
            weekend_soc,
        )

        await self.coordinator.async_set_preferences(
            weekday_time=weekday_time,
            weekend_time=weekend_time,
            weekday_soc=weekday_soc,
            weekend_soc=weekend_soc,
        )
        await self.coordinator.async_request_refresh()

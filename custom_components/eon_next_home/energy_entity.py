"""Shared base for entities backed by E.ON Next tariff and meter data."""
from __future__ import annotations

from datetime import datetime

from homeassistant.core import CALLBACK_TYPE, callback
from homeassistant.helpers.event import async_track_point_in_utc_time
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import dt as dt_util

from .const import ATTRIBUTION
from .energy import EonNextTariffCoordinator, FuelTariff


class EonNextTariffEntity(CoordinatorEntity[EonNextTariffCoordinator]):
    """An entity for one fuel's tariff that updates when the rate window changes."""

    _attr_attribution = ATTRIBUTION
    _attr_has_entity_name = True

    def __init__(self, coordinator: EonNextTariffCoordinator, fuel: str, key: str) -> None:
        """Initialise the entity."""
        super().__init__(coordinator)
        self.fuel = fuel
        self._attr_unique_id = f"{coordinator.account_number}_{fuel}_{key}"
        self._attr_device_info = coordinator.device_info
        self._unsub_boundary: CALLBACK_TYPE | None = None

    @property
    def tariff(self) -> FuelTariff | None:
        """The fuel's active tariff."""
        return (self.coordinator.data or {}).get(self.fuel)

    @property
    def available(self) -> bool:
        """Unavailable if the fuel's tariff has gone (e.g. supply ended)."""
        return super().available and self.tariff is not None

    async def async_added_to_hass(self) -> None:
        """Start tracking rate window boundaries."""
        await super().async_added_to_hass()
        self._schedule_boundary()

    async def async_will_remove_from_hass(self) -> None:
        """Stop tracking rate window boundaries."""
        self._cancel_boundary()
        await super().async_will_remove_from_hass()

    @callback
    def _handle_coordinator_update(self) -> None:
        """Re-plan the next boundary whenever the rates are refreshed."""
        self._schedule_boundary()
        super()._handle_coordinator_update()

    @callback
    def _cancel_boundary(self) -> None:
        if self._unsub_boundary:
            self._unsub_boundary()
            self._unsub_boundary = None

    @callback
    def _schedule_boundary(self) -> None:
        """Wake up when the current rate window ends."""
        self._cancel_boundary()
        tariff = self.tariff
        if not tariff or not tariff.windows:
            return
        now = dt_util.utcnow()
        next_change = min(
            (t for w in tariff.windows for t in (w.start, w.end) if t > now),
            default=None,
        )
        if next_change:
            self._unsub_boundary = async_track_point_in_utc_time(
                self.hass, self._at_boundary, next_change
            )

    @callback
    def _at_boundary(self, _now: datetime) -> None:
        self._unsub_boundary = None
        self._schedule_boundary()
        self.async_write_ha_state()

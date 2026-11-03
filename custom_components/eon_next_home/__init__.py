"""The E.ON Next Home integration."""
from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, callback

from .const import DOMAIN
from .coordinator import EonNextEVCoordinator
from .energy import EonNextConsumptionCoordinator, EonNextData, EonNextTariffCoordinator

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [
    Platform.BINARY_SENSOR,
    Platform.NUMBER,
    Platform.SENSOR,
    Platform.SWITCH,
    Platform.TIME,
]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up E.ON Next Home from a config entry."""
    coordinator = EonNextEVCoordinator(hass, entry)

    # Perform the first refresh. If this fails it raises and the entry is retried
    # later rather than silently adding entities with no data.
    await coordinator.async_config_entry_first_refresh()

    # Tariffs decide which energy entities exist (e.g. gas), so load them first too
    tariffs = EonNextTariffCoordinator(hass, coordinator)
    await tariffs.async_config_entry_first_refresh()

    consumption = EonNextConsumptionCoordinator(hass, entry, coordinator, tariffs)
    # Keep the statistics import running even if the "data up to" sensors are
    # disabled; a coordinator with no listeners stops polling.
    entry.async_on_unload(consumption.async_add_listener(_noop))
    # The first run may backfill months of readings, so don't hold up setup for it
    entry.async_create_background_task(
        hass, consumption.async_refresh(), f"{DOMAIN}_consumption_import"
    )

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = EonNextData(
        ev=coordinator, tariffs=tariffs, consumption=consumption
    )

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # Reload the entry whenever the user saves new options (e.g. scan interval).
    # Update listeners also fire when entry data changes, such as every token
    # refresh, so compare the options rather than reloading on every change.
    options = dict(entry.options)

    async def _async_reload_on_options_change(hass: HomeAssistant, entry: ConfigEntry) -> None:
        if dict(entry.options) != options:
            await hass.config_entries.async_reload(entry.entry_id)

    entry.async_on_unload(entry.add_update_listener(_async_reload_on_options_change))

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id)
    return unload_ok


@callback
def _noop() -> None:
    """Coordinator listener that does nothing."""

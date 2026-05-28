"""Switch entities for E.ON Next EV Smart Charging."""
from __future__ import annotations

import logging

from homeassistant.components.switch import SwitchDeviceClass, SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import ATTRIBUTION, DOMAIN
from .coordinator import EonNextEVCoordinator

_LOGGER = logging.getLogger(__name__)

# ── Verified mutations ─────────────────────────────────────────────────────────
# Input type: SmartControlInput { deviceId: ID!, action: SmartControlAction! }
# SmartControlAction enum values: SUSPEND | UNSUSPEND
MUTATION_SMART_CONTROL = """
mutation SmartControl($deviceId: ID!, $action: SmartControlAction!) {
  updateDeviceSmartControl(input: { deviceId: $deviceId, action: $action }) {
    __typename
  }
}
"""

# Input type: UpdateBoostChargeInput { deviceId: String!, action: UpdateBoostChargeAction! }
# UpdateBoostChargeAction enum values: BOOST | CANCEL
MUTATION_BOOST_CHARGE = """
mutation BoostCharge($deviceId: String!, $action: UpdateBoostChargeAction!) {
  updateBoostCharge(input: { deviceId: $deviceId, action: $action }) {
    __typename
  }
}
"""


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up E.ON Next EV switch entities."""
    coordinator: EonNextEVCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([
        EonNextEVSmartChargeSwitch(coordinator),
        EonNextEVBoostChargeSwitch(coordinator),
    ])


class EonNextEVSmartChargeSwitch(CoordinatorEntity[EonNextEVCoordinator], SwitchEntity):
    """Switch that enables or suspends Kraken smart charging.

    ON  = Smart charging active (Kraken controls the schedule).
    OFF = Suspended (charges at full rate immediately; use Boost instead).
    """

    _attr_attribution = ATTRIBUTION
    _attr_has_entity_name = True
    _attr_name = "Smart Charging"
    _attr_icon = "mdi:ev-plug-type2"
    _attr_device_class = SwitchDeviceClass.SWITCH

    def __init__(self, coordinator: EonNextEVCoordinator) -> None:
        """Initialise the switch."""
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.account_number}_smart_charging"
        self._attr_device_info = coordinator.device_info

    @property
    def is_on(self) -> bool | None:
        """Return True when smart charging is active (not suspended)."""
        if not self.coordinator.data:
            return None
        flex = self.coordinator.data.get("registeredKrakenflexDevice") or {}
        return flex.get("status", "").upper() == "LIVE"

    async def async_turn_on(self, **kwargs) -> None:
        """Re-enable smart charging."""
        await self._send("UNSUSPEND")

    async def async_turn_off(self, **kwargs) -> None:
        """Suspend smart charging."""
        await self._send("SUSPEND")

    async def _send(self, action: str) -> None:
        device_id = self.coordinator.device_id
        if not device_id:
            _LOGGER.error("Cannot update smart charging: device ID not yet available")
            return
        try:
            await self.coordinator.async_graphql_mutation(
                MUTATION_SMART_CONTROL,
                {"deviceId": device_id, "action": action},
            )
        except Exception as err:  # pylint: disable=broad-except
            _LOGGER.error("Failed to set smart charging to %s: %s", action, err)
            return
        await self.coordinator.async_request_refresh()


class EonNextEVBoostChargeSwitch(CoordinatorEntity[EonNextEVCoordinator], SwitchEntity):
    """Switch that triggers (or cancels) an immediate boost charge.

    ON  = Start a boost charge right now at full charger power.
    OFF = Cancel an active boost and return to smart schedule.
    """

    _attr_attribution = ATTRIBUTION
    _attr_has_entity_name = True
    _attr_name = "Boost Charge"
    _attr_icon = "mdi:battery-charging-100"
    _attr_device_class = SwitchDeviceClass.SWITCH

    # Sources that indicate a boost/manual dispatch is active.
    # "bump-charge" confirmed from live API response.
    BOOST_SOURCES = {"bump-charge", "boost"}

    def __init__(self, coordinator: EonNextEVCoordinator) -> None:
        """Initialise the switch."""
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.account_number}_boost_charge"
        self._attr_device_info = coordinator.device_info
        # Optimistic state: held between mutation and next coordinator refresh
        self._optimistic_on: bool | None = None

    @property
    def is_on(self) -> bool | None:
        """Return True when a boost charge is active.

        Uses optimistic state immediately after a mutation, then falls back to
        API data once the coordinator refreshes.
        """
        if self._optimistic_on is not None:
            return self._optimistic_on

        if not self.coordinator.data:
            return None

        dispatches = self.coordinator.data.get("plannedDispatches") or []
        return any(
            (d.get("meta") or {}).get("source", "").lower() in self.BOOST_SOURCES
            for d in dispatches
        )

    @property
    def assumed_state(self) -> bool:
        """Tell HA we use optimistic state so it renders correctly."""
        return self._optimistic_on is not None

    def _handle_coordinator_update(self) -> None:
        """Clear optimistic state when fresh data arrives."""
        self._optimistic_on = None
        super()._handle_coordinator_update()

    async def async_turn_on(self, **kwargs) -> None:
        """Start a boost charge."""
        self._optimistic_on = True
        self.async_write_ha_state()
        await self._send("BOOST")

    async def async_turn_off(self, **kwargs) -> None:
        """Cancel an active boost charge."""
        self._optimistic_on = False
        self.async_write_ha_state()
        await self._send("CANCEL")

    async def _send(self, action: str) -> None:
        device_id = self.coordinator.device_id
        if not device_id:
            _LOGGER.error("Cannot update boost charge: device ID not yet available")
            self._optimistic_on = None
            self.async_write_ha_state()
            return
        try:
            await self.coordinator.async_graphql_mutation(
                MUTATION_BOOST_CHARGE,
                {"deviceId": device_id, "action": action},
            )
        except Exception as err:  # pylint: disable=broad-except
            _LOGGER.error("Failed to set boost charge to %s: %s", action, err)
            # Roll back optimistic state on failure
            self._optimistic_on = None
            self.async_write_ha_state()
            return
        await self.coordinator.async_request_refresh()

"""Sensor entities for E.ON Next EV Smart Charging."""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfEnergy, UnitOfPower
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import ATTRIBUTION, DOMAIN
from .coordinator import EonNextEVCoordinator


@dataclass(frozen=True, kw_only=True)
class EonNextEVSensorDescription(SensorEntityDescription):
    """Sensor description with a value extractor callable."""

    value_fn: Callable[[dict[str, Any]], Any] = lambda _: None


def _first_ev_device(data: dict) -> dict:
    return next(
        (d for d in (data.get("devices") or []) if d.get("deviceType") == "ELECTRIC_VEHICLES"),
        {},
    )


def _dispatches(data: dict) -> list[dict]:
    return data.get("plannedDispatches") or []


def _flex(data: dict) -> dict:
    return data.get("registeredKrakenflexDevice") or {}


SENSOR_DESCRIPTIONS: tuple[EonNextEVSensorDescription, ...] = (
    # ── Live status ──────────────────────────────────────────────────────────
    EonNextEVSensorDescription(
        key="smart_charge_status",
        name="Smart Charge Status",
        icon="mdi:ev-station",
        value_fn=lambda d: _flex(d).get("status"),
    ),
    EonNextEVSensorDescription(
        key="vehicle_name",
        name="Vehicle",
        icon="mdi:car-electric",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda d: _first_ev_device(d).get("name"),
    ),
    # ── Next planned dispatch ─────────────────────────────────────────────────
    EonNextEVSensorDescription(
        key="next_dispatch_start",
        name="Smart Charge Window Start",
        icon="mdi:clock-start",
        device_class=SensorDeviceClass.TIMESTAMP,
        value_fn=lambda d: (
            datetime.fromisoformat(disp[0]["start"])
            if (disp := _dispatches(d))
            else None
        ),
    ),
    EonNextEVSensorDescription(
        key="next_dispatch_end",
        name="Smart Charge Window End",
        icon="mdi:clock-end",
        device_class=SensorDeviceClass.TIMESTAMP,
        value_fn=lambda d: (
            datetime.fromisoformat(disp[-1]["end"])
            if (disp := _dispatches(d))
            else None
        ),
    ),
    EonNextEVSensorDescription(
        key="next_dispatch_kwh",
        name="Smart Charge Energy",
        icon="mdi:battery-charging",
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        value_fn=lambda d: (
            round(sum(abs(float(x["delta"])) for x in disp if x.get("delta")), 2)
            if (disp := _dispatches(d))
            else None
        ),
    ),
    # ── Hardware info (diagnostic) ────────────────────────────────────────────
    EonNextEVSensorDescription(
        key="battery_capacity",
        name="Battery Capacity",
        icon="mdi:battery",
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        device_class=SensorDeviceClass.ENERGY_STORAGE,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda d: (
            float(v) if (v := _flex(d).get("vehicleBatterySizeInKwh")) else None
        ),
    ),
    EonNextEVSensorDescription(
        key="charger_power",
        name="Charger Max Power",
        icon="mdi:lightning-bolt",
        native_unit_of_measurement=UnitOfPower.KILO_WATT,
        device_class=SensorDeviceClass.POWER,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda d: (
            float(v) if (v := _flex(d).get("chargePointPowerInKw")) else None
        ),
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up E.ON Next EV sensor entities."""
    coordinator: EonNextEVCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        EonNextEVSensor(coordinator, description)
        for description in SENSOR_DESCRIPTIONS
    )


class EonNextEVSensor(CoordinatorEntity[EonNextEVCoordinator], SensorEntity):
    """A sensor that reads its value from the coordinator data."""

    _attr_attribution = ATTRIBUTION
    _attr_has_entity_name = True
    entity_description: EonNextEVSensorDescription

    def __init__(
        self,
        coordinator: EonNextEVCoordinator,
        description: EonNextEVSensorDescription,
    ) -> None:
        """Initialise the sensor."""
        super().__init__(coordinator)
        self.entity_description = description
        self._attr_unique_id = f"{coordinator.account_number}_{description.key}"
        self._attr_device_info = coordinator.device_info

    @property
    def native_value(self) -> Any:
        """Return the current sensor value."""
        if not self.coordinator.data:
            return None
        try:
            return self.entity_description.value_fn(self.coordinator.data)
        except (KeyError, TypeError, ValueError):
            return None

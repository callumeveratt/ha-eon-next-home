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
from homeassistant.util import dt as dt_util

from .const import ATTRIBUTION, DOMAIN
from .coordinator import EonNextEVCoordinator
from .energy import (
    CURRENCY,
    EonNextConsumptionCoordinator,
    EonNextData,
    EonNextTariffCoordinator,
)
from .energy_entity import EonNextTariffEntity


@dataclass(frozen=True, kw_only=True)
class EonNextEVSensorDescription(SensorEntityDescription):
    """Sensor description with a value extractor callable."""

    value_fn: Callable[[dict[str, Any]], Any] = lambda _: None
    attrs_fn: Callable[[dict[str, Any]], dict[str, Any] | None] | None = None


def _first_ev_device(data: dict) -> dict:
    return next(
        (d for d in (data.get("devices") or []) if d.get("deviceType") == "ELECTRIC_VEHICLES"),
        {},
    )


def _dispatches(data: dict) -> list[dict]:
    return data.get("plannedDispatches") or []


def _local_iso(value: str) -> str:
    return dt_util.as_local(datetime.fromisoformat(value)).isoformat()


def _kwh(delta: Any) -> float | None:
    return round(abs(float(delta)), 2) if delta not in (None, "") else None


def _slot_attrs(data: dict) -> dict[str, Any]:
    """Every planned dispatch, sorted by start, with local ISO times."""
    slots = sorted(_dispatches(data), key=lambda x: datetime.fromisoformat(x["start"]))
    return {
        "dispatches": [
            {
                "start": _local_iso(x["start"]),
                "end": _local_iso(x["end"]),
                "kwh": _kwh(x.get("delta")),
                "source": (x.get("meta") or {}).get("source"),
                "location": (x.get("meta") or {}).get("location"),
            }
            for x in slots
        ],
        "slot_count": len(slots),
    }


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
        attrs_fn=_slot_attrs,
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
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        value_fn=lambda d: (
            round(sum(abs(float(x["delta"])) for x in disp if x.get("delta")), 2)
            if (disp := _dispatches(d))
            else None
        ),
    ),
    EonNextEVSensorDescription(
        key="smart_charge_slot_count",
        name="Smart Charge Slot Count",
        icon="mdi:calendar-clock",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda d: len(_dispatches(d)),
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
    """Set up E.ON Next EV and energy sensor entities."""
    data: EonNextData = hass.data[DOMAIN][entry.entry_id]
    entities: list[SensorEntity] = [
        EonNextEVSensor(data.ev, description) for description in SENSOR_DESCRIPTIONS
    ]
    for fuel in data.consumption.fuels:
        entities += [
            EonNextRateSensor(data.tariffs, fuel),
            EonNextStandingChargeSensor(data.tariffs, fuel),
            EonNextDataUpToSensor(data.consumption, fuel),
        ]
    async_add_entities(entities)


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

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Return extra attributes, if the description defines any."""
        attrs_fn = self.entity_description.attrs_fn
        if attrs_fn is None or not self.coordinator.data:
            return None
        try:
            return attrs_fn(self.coordinator.data)
        except (KeyError, TypeError, ValueError):
            return None


# ── Energy ─────────────────────────────────────────────────────────────────────


class EonNextRateSensor(EonNextTariffEntity, SensorEntity):
    """The unit rate that applies right now, in GBP/kWh."""

    _attr_icon = "mdi:currency-gbp"
    _attr_native_unit_of_measurement = f"{CURRENCY}/{UnitOfEnergy.KILO_WATT_HOUR}"
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_suggested_display_precision = 4

    def __init__(self, coordinator: EonNextTariffCoordinator, fuel: str) -> None:
        """Initialise the sensor."""
        super().__init__(coordinator, fuel, "current_rate")
        self._attr_name = f"{fuel.capitalize()} Current Rate"

    @property
    def native_value(self) -> float | None:
        """Return the rate for the current time."""
        return self.tariff.rate_at(dt_util.utcnow()) if self.tariff else None

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Tariff details and the upcoming time-of-use windows."""
        tariff = self.tariff
        if not tariff:
            return None
        now = dt_util.utcnow()
        attrs: dict[str, Any] = {
            "tariff_name": tariff.display_name,
            "tariff_code": tariff.tariff_code,
            "product_code": tariff.product_code,
        }
        if tariff.windows:
            current = tariff.window_at(now)
            later = [w for w in tariff.upcoming(now) if w.start >= (current.end if current else now)]
            attrs.update(
                {
                    "current_rate_ends": current.end.isoformat() if current else None,
                    "next_rate": later[0].rate if later else None,
                    "off_peak_rate": tariff.off_peak_rate,
                    "rates": [
                        {"start": w.start.isoformat(), "end": w.end.isoformat(), "rate": w.rate}
                        for w in tariff.upcoming(now)
                    ],
                }
            )
        return attrs


class EonNextStandingChargeSensor(EonNextTariffEntity, SensorEntity):
    """The daily standing charge, in GBP/day."""

    _attr_icon = "mdi:cash-clock"
    _attr_native_unit_of_measurement = f"{CURRENCY}/day"
    _attr_suggested_display_precision = 4

    def __init__(self, coordinator: EonNextTariffCoordinator, fuel: str) -> None:
        """Initialise the sensor."""
        super().__init__(coordinator, fuel, "standing_charge")
        self._attr_name = f"{fuel.capitalize()} Standing Charge"

    @property
    def native_value(self) -> float | None:
        """Return the standing charge."""
        return self.tariff.standing_charge if self.tariff else None


class EonNextDataUpToSensor(CoordinatorEntity[EonNextConsumptionCoordinator], SensorEntity):
    """The end of the latest half-hour E.ON Next has supplied a reading for."""

    _attr_attribution = ATTRIBUTION
    _attr_has_entity_name = True
    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_icon = "mdi:meter-electric"

    def __init__(self, coordinator: EonNextConsumptionCoordinator, fuel: str) -> None:
        """Initialise the sensor."""
        super().__init__(coordinator)
        self.fuel = fuel
        self._attr_name = f"{fuel.capitalize()} Readings Up To"
        self._attr_unique_id = f"{coordinator.account_number}_{fuel}_readings_up_to"
        self._attr_device_info = coordinator.device_info
        if fuel == "gas":
            self._attr_icon = "mdi:meter-gas"

    @property
    def native_value(self) -> datetime | None:
        """Return when the latest reading ends."""
        return (self.coordinator.data or {}).get(self.fuel)

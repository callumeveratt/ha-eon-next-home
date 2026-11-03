"""Tariff and meter-reading coordinators for E.ON Next Home.

Meter readings reach the Kraken API as a daily batch, 1–2 days after the fact,
so they are imported into Home Assistant's long-term statistics against the
half-hour they belong to rather than exposed as a live sensor.
"""
from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from homeassistant.components.recorder import get_instance
from homeassistant.components.recorder.models import (
    StatisticData,
    StatisticMeanType,
    StatisticMetaData,
)
from homeassistant.components.recorder.statistics import (
    async_add_external_statistics,
    get_last_statistics,
    statistics_during_period,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfEnergy
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from homeassistant.util import dt as dt_util

from .const import (
    CONSUMPTION_BACKFILL_DAYS,
    CONSUMPTION_OVERLAP_DAYS,
    CONSUMPTION_SCAN_INTERVAL,
    DOMAIN,
    MEASUREMENT_FILTERS,
    MEASUREMENTS_CHUNK_DAYS,
    MEASUREMENTS_PAGE_SIZE,
    QUERY_MEASUREMENTS,
    QUERY_TARIFFS,
    TARIFF_SCAN_INTERVAL,
)
from .coordinator import EonNextEVCoordinator

_LOGGER = logging.getLogger(__name__)

FUELS = ("electricity", "gas")
CURRENCY = "GBP"


@dataclass
class EonNextData:
    """Everything a config entry owns, stored in hass.data."""

    ev: EonNextEVCoordinator
    tariffs: EonNextTariffCoordinator
    consumption: EonNextConsumptionCoordinator


# ── Tariffs ────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class RateWindow:
    """A unit rate that applies between two instants."""

    start: datetime
    end: datetime
    rate: float  # GBP/kWh, VAT included


@dataclass
class FuelTariff:
    """The active import tariff for one fuel."""

    fuel: str
    tariff_type: str
    display_name: str | None
    product_code: str | None
    tariff_code: str | None
    standing_charge: float | None  # GBP/day, VAT included
    unit_rate: float | None = None  # GBP/kWh, for single-rate tariffs
    windows: list[RateWindow] = field(default_factory=list)  # time-of-use tariffs

    @property
    def is_time_of_use(self) -> bool:
        """Whether the rate depends on the time of day."""
        return bool(self.windows)

    def window_at(self, when: datetime) -> RateWindow | None:
        """Return the time-of-use window covering `when`, if any."""
        return next((w for w in self.windows if w.start <= when < w.end), None)

    def rate_at(self, when: datetime) -> float | None:
        """Return the unit rate in GBP/kWh at `when`."""
        if self.windows:
            window = self.window_at(when)
            return window.rate if window else None
        return self.unit_rate

    def upcoming(self, when: datetime) -> list[RateWindow]:
        """Return the current and future windows."""
        return [w for w in self.windows if w.end > when]

    @property
    def off_peak_rate(self) -> float | None:
        """The cheapest known rate, or None if the tariff has a single rate."""
        rates = {w.rate for w in self.windows}
        return min(rates) if len(rates) > 1 else None


def _pence_to_pounds(value: Any) -> float | None:
    # Rates have up to 5 decimal places in pence (e.g. 6.28005), so keep 7 in pounds
    return None if value is None else round(float(value) / 100, 7)


def _parse_tariff(fuel: str, agreements: list[dict[str, Any]]) -> FuelTariff | None:
    """Pick the active import agreement and convert it to a FuelTariff."""
    tariff = next(
        (
            a["tariff"]
            for a in agreements
            if a.get("tariff") and not a["tariff"].get("isExport")
        ),
        None,
    )
    if tariff is None:
        return None

    tariff_type = tariff.get("__typename", "GasTariffType" if fuel == "gas" else "")
    parsed = FuelTariff(
        fuel=fuel,
        tariff_type=tariff_type,
        display_name=tariff.get("displayName"),
        product_code=tariff.get("productCode"),
        tariff_code=tariff.get("tariffCode"),
        standing_charge=_pence_to_pounds(tariff.get("standingCharge")),
        unit_rate=_pence_to_pounds(tariff.get("unitRate")),
    )
    for rate in tariff.get("unitRates") or []:
        start = dt_util.parse_datetime(rate.get("validFrom") or "")
        end = dt_util.parse_datetime(rate.get("validTo") or "")
        if start and end and rate.get("value") is not None:
            parsed.windows.append(RateWindow(start, end, _pence_to_pounds(rate["value"])))
    parsed.windows.sort(key=lambda w: w.start)

    if parsed.unit_rate is None and not parsed.windows:
        # Day/night and other multi-register tariffs don't say when each rate applies
        _LOGGER.warning(
            "E.ON Next %s tariff %s (%s) isn't supported for rate sensors yet",
            fuel,
            parsed.tariff_code,
            tariff_type,
        )
    return parsed


class EonNextTariffCoordinator(DataUpdateCoordinator[dict[str, FuelTariff | None]]):
    """Fetches the active electricity and gas tariffs."""

    def __init__(self, hass: HomeAssistant, ev: EonNextEVCoordinator) -> None:
        """Initialise the coordinator."""
        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN}_tariffs",
            update_interval=timedelta(minutes=TARIFF_SCAN_INTERVAL),
        )
        self._ev = ev
        self.account_number = ev.account_number
        self.property_ids: list[str] = []

    @property
    def device_info(self) -> DeviceInfo:
        """Return DeviceInfo for the energy supply (separate from the EV device)."""
        return DeviceInfo(
            identifiers={(DOMAIN, f"{self.account_number}_energy")},
            name="E.ON Next Energy",
            manufacturer="E.ON Next",
            model="Smart meter",
            entry_type=DeviceEntryType.SERVICE,
            configuration_url="https://home.eonnext.com",
        )

    async def _async_update_data(self) -> dict[str, FuelTariff | None]:
        """Fetch the active agreements."""
        data = await self._ev.async_graphql_query(
            QUERY_TARIFFS, {"accountNumber": self.account_number}
        )
        account = data.get("account") or {}
        self.property_ids = [
            p["id"] for p in account.get("properties") or [] if p.get("id")
        ]
        return {
            "electricity": _parse_tariff(
                "electricity", account.get("electricityAgreements") or []
            ),
            "gas": _parse_tariff("gas", account.get("gasAgreements") or []),
        }


# ── Meter readings → long-term statistics ──────────────────────────────────────


def statistic_id(account_number: str, fuel: str, kind: str) -> str:
    """Return the external statistic ID, e.g. eon_next_home:a_1234abcd_electricity_consumption."""
    slug = account_number.lower().replace("-", "_")
    return f"{DOMAIN}:{slug}_{fuel}_{kind}"


def _metadata(stat_id: str, name: str, unit: str, unit_class: str | None) -> StatisticMetaData:
    return StatisticMetaData(
        mean_type=StatisticMeanType.NONE,
        has_sum=True,
        name=name,
        source=DOMAIN,
        statistic_id=stat_id,
        unit_class=unit_class,
        unit_of_measurement=unit,
    )


@dataclass(frozen=True)
class Reading:
    """One half-hour of consumption."""

    start: datetime
    end: datetime
    kwh: float
    cost: float | None  # GBP, VAT included, excluding standing charge


def _parse_reading(node: dict[str, Any]) -> Reading | None:
    start = dt_util.parse_datetime(node.get("startAt") or "")
    end = dt_util.parse_datetime(node.get("endAt") or "")
    if not start or not end or node.get("value") is None:
        return None
    cost = next(
        (
            float(s["costInclTax"]["estimatedAmount"]) / 100
            for s in (node.get("metaData") or {}).get("statistics") or []
            if s.get("type") == "CONSUMPTION_COST" and s.get("costInclTax")
        ),
        None,
    )
    return Reading(dt_util.as_utc(start), dt_util.as_utc(end), float(node["value"]), cost)


class EonNextConsumptionCoordinator(DataUpdateCoordinator[dict[str, datetime | None]]):
    """Imports half-hourly meter readings as external statistics.

    The data is the end of the latest imported reading per fuel, which the
    "data up to" sensors show so users can see how far behind E.ON Next is.
    """

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        ev: EonNextEVCoordinator,
        tariffs: EonNextTariffCoordinator,
    ) -> None:
        """Initialise the coordinator."""
        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN}_consumption",
            update_interval=timedelta(minutes=CONSUMPTION_SCAN_INTERVAL),
        )
        self._entry = entry
        self._ev = ev
        self._tariffs = tariffs
        self.account_number = ev.account_number
        self._import_lock = asyncio.Lock()

    @property
    def device_info(self) -> DeviceInfo:
        """Readings belong to the same energy device as the tariffs."""
        return self._tariffs.device_info

    @property
    def fuels(self) -> list[str]:
        """Fuels the account is supplied with."""
        return [f for f in FUELS if (self._tariffs.data or {}).get(f)]

    async def _async_update_data(self) -> dict[str, datetime | None]:
        """Import any new readings for each fuel."""
        async with self._import_lock:
            latest: dict[str, datetime | None] = {}
            for fuel in self.fuels:
                latest[fuel] = await self._async_import_fuel(fuel)
            return latest

    async def _async_import_fuel(self, fuel: str) -> datetime | None:
        """Fetch readings since the last import (with overlap) and write statistics."""
        consumption_id = statistic_id(self.account_number, fuel, "consumption")
        cost_id = statistic_id(self.account_number, fuel, "cost")
        recorder = get_instance(self.hass)

        last = await recorder.async_add_executor_job(
            get_last_statistics, self.hass, 1, consumption_id, True, {"sum"}
        )
        now = dt_util.utcnow()
        if last.get(consumption_id):
            last_start = dt_util.utc_from_timestamp(last[consumption_id][0]["start"])
            fetch_from = last_start - timedelta(days=CONSUMPTION_OVERLAP_DAYS)
            bases = await self._async_sums_before(fetch_from, {consumption_id, cost_id})
        else:
            fetch_from = (now - timedelta(days=CONSUMPTION_BACKFILL_DAYS)).replace(
                hour=0, minute=0, second=0, microsecond=0
            )
            bases = {}
            _LOGGER.info("Backfilling E.ON Next %s readings; this can take a minute", fuel)

        readings = await self._async_fetch_readings(fuel, fetch_from, now)
        if uncosted := sum(r.cost is None for r in readings):
            _LOGGER.warning(
                "E.ON Next returned %d of %d %s half-hours without a cost; "
                "they are imported with no cost",
                uncosted,
                len(readings),
                fuel,
            )
        if not readings:
            return self.data.get(fuel) if self.data else None

        # Long-term statistics are hourly, so add each pair of half-hours together
        hourly_kwh: dict[datetime, float] = defaultdict(float)
        hourly_cost: dict[datetime, float] = defaultdict(float)
        for reading in readings:
            hour = reading.start.replace(minute=0, second=0, microsecond=0)
            hourly_kwh[hour] += reading.kwh
            if reading.cost is not None:
                hourly_cost[hour] += reading.cost

        kwh_sum = bases.get(consumption_id, 0.0)
        cost_sum = bases.get(cost_id, 0.0)
        kwh_rows: list[StatisticData] = []
        cost_rows: list[StatisticData] = []
        for hour in sorted(hourly_kwh):
            kwh_sum += hourly_kwh[hour]
            cost_sum += hourly_cost.get(hour, 0.0)
            kwh_rows.append(StatisticData(start=hour, state=round(hourly_kwh[hour], 3), sum=round(kwh_sum, 3)))
            cost_rows.append(StatisticData(start=hour, state=round(hourly_cost.get(hour, 0.0), 4), sum=round(cost_sum, 4)))

        label = fuel.capitalize()
        async_add_external_statistics(
            self.hass,
            _metadata(consumption_id, f"E.ON Next {label} Consumption", UnitOfEnergy.KILO_WATT_HOUR, "energy"),
            kwh_rows,
        )
        async_add_external_statistics(
            self.hass,
            _metadata(cost_id, f"E.ON Next {label} Cost", CURRENCY, None),
            cost_rows,
        )
        _LOGGER.debug(
            "Imported %d hours of E.ON Next %s readings from %s", len(kwh_rows), fuel, kwh_rows[0]["start"]
        )
        return max(r.end for r in readings)

    async def _async_sums_before(self, when: datetime, stat_ids: set[str]) -> dict[str, float]:
        """Return each statistic's running sum just before `when`."""
        rows = await get_instance(self.hass).async_add_executor_job(
            statistics_during_period,
            self.hass,
            when - timedelta(days=30),
            when,
            stat_ids,
            "hour",
            None,
            {"sum"},
        )
        return {
            stat: float(values[-1]["sum"] or 0.0)
            for stat, values in rows.items()
            if values
        }

    async def _async_fetch_readings(
        self, fuel: str, start: datetime, end: datetime
    ) -> list[Reading]:
        """Fetch half-hourly measurements for every property, a week at a time."""
        readings: list[Reading] = []
        for property_id in self._tariffs.property_ids:
            # Skip straight to the first reading so a backfill doesn't walk
            # through years of empty weeks before supply started
            first = await self._async_query_measurements(property_id, fuel, start, end, 1)
            if not first:
                continue
            chunk_start = max(start, first[0].start)
            while chunk_start < end:
                chunk_end = min(chunk_start + timedelta(days=MEASUREMENTS_CHUNK_DAYS), end)
                readings += await self._async_query_measurements(
                    property_id, fuel, chunk_start, chunk_end, MEASUREMENTS_PAGE_SIZE
                )
                chunk_start = chunk_end
                if chunk_start < end:
                    await asyncio.sleep(1)  # be gentle during a backfill
        return readings

    async def _async_query_measurements(
        self, property_id: str, fuel: str, start: datetime, end: datetime, limit: int
    ) -> list[Reading]:
        """Return up to `limit` readings per page, following pages until done."""
        readings: list[Reading] = []
        after: str | None = None
        while True:
            data = await self._ev.async_graphql_query(
                QUERY_MEASUREMENTS,
                {
                    "propertyId": property_id,
                    "startAt": start.isoformat(),
                    "endAt": end.isoformat(),
                    "filters": [MEASUREMENT_FILTERS[fuel]],
                    "first": limit,
                    "after": after,
                },
            )
            measurements = (data.get("property") or {}).get("measurements") or {}
            for edge in measurements.get("edges") or []:
                if reading := _parse_reading(edge.get("node") or {}):
                    readings.append(reading)
            page = measurements.get("pageInfo") or {}
            if limit == 1 or not page.get("hasNextPage") or not page.get("endCursor"):
                return readings
            after = page["endCursor"]

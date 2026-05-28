"""DataUpdateCoordinator for E.ON Next EV Smart Charging."""
from __future__ import annotations

import logging
import time
from datetime import timedelta
from typing import Any

import aiohttp

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import (
    API_KEY,
    AUTH_URL,
    CONF_ACCOUNT_NUMBER,
    CONF_DEVICE_ID,
    CONF_REFRESH_TOKEN,
    CONF_SCAN_INTERVAL,
    CONF_TOKEN,
    CONF_TOKEN_EXPIRY,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    GRAPHQL_URL,
    MUTATION_SET_PREFERENCES,
    QUERY_ALL_DATA,
    REFRESH_URL,
)

_LOGGER = logging.getLogger(__name__)


class EonNextEVCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Coordinator that owns the API session and keeps tokens fresh."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        """Initialise the coordinator."""
        scan_minutes = entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=timedelta(minutes=scan_minutes),
        )
        self._entry = entry
        self._session = async_get_clientsession(hass)

        self.account_number: str = entry.data[CONF_ACCOUNT_NUMBER]
        self.device_id: str | None = entry.data.get(CONF_DEVICE_ID)

        self._token: str = entry.data[CONF_TOKEN]
        self._refresh_token: str = entry.data[CONF_REFRESH_TOKEN]
        self._token_expiry: int = entry.data[CONF_TOKEN_EXPIRY]  # Unix timestamp

    # ── Public helpers ─────────────────────────────────────────────────────────

    @property
    def device_info(self) -> DeviceInfo:
        """Return DeviceInfo for the EV device (used by all entity platforms)."""
        data = self.data or {}
        flex = data.get("registeredKrakenflexDevice") or {}
        ev_device = next(
            (
                d
                for d in (data.get("devices") or [])
                if d.get("deviceType") == "ELECTRIC_VEHICLES"
            ),
            {},
        )
        return DeviceInfo(
            identifiers={(DOMAIN, self.account_number)},
            name=ev_device.get("name", "E.ON Next EV"),
            manufacturer=flex.get("chargePointMake", "E.ON Next"),
            model=flex.get("chargePointModel"),
            configuration_url="https://home.eonnext.com",
        )

    async def async_set_preferences(
        self,
        weekday_time: str,
        weekend_time: str,
        weekday_soc: int,
        weekend_soc: int,
    ) -> None:
        """Write weekday/weekend ready-by times and target SoC to Kraken.

        Times are expected in "HH:MM" format; SoC values are whole percentages.
        The API requires a full 7-day schedule so we expand Mon–Fri / Sat–Sun.
        """
        weekday_days = ["MONDAY", "TUESDAY", "WEDNESDAY", "THURSDAY", "FRIDAY"]
        weekend_days = ["SATURDAY", "SUNDAY"]

        # API expects Time scalar in HH:MM:SS format
        wday_time_api = f"{weekday_time}:00" if len(weekday_time) == 5 else weekday_time
        wend_time_api = f"{weekend_time}:00" if len(weekend_time) == 5 else weekend_time

        schedules = [
            {"dayOfWeek": day, "time": wday_time_api, "max": str(weekday_soc)}
            for day in weekday_days
        ] + [
            {"dayOfWeek": day, "time": wend_time_api, "max": str(weekend_soc)}
            for day in weekend_days
        ]

        await self.async_graphql_mutation(
            MUTATION_SET_PREFERENCES,
            {
                "input": {
                    "deviceId": self.device_id,
                    "mode": "CHARGE",
                    "unit": "PERCENTAGE",
                    "schedules": schedules,
                }
            },
        )

    async def async_graphql_mutation(
        self, mutation: str, variables: dict[str, Any]
    ) -> dict[str, Any]:
        """Execute a GraphQL mutation and return the response data."""
        await self._ensure_valid_token()

        async with self._session.post(
            GRAPHQL_URL,
            json={"query": mutation, "variables": variables},
            headers={
                "Authorization": f"Bearer {self._token}",
                "Content-Type": "application/json",
            },
        ) as response:
            response.raise_for_status()
            result = await response.json()

        if "errors" in result:
            msgs = [e.get("message", "unknown") for e in result["errors"]]
            raise UpdateFailed(f"GraphQL mutation failed: {'; '.join(msgs)}")

        return result.get("data", {})

    # ── DataUpdateCoordinator hook ─────────────────────────────────────────────

    async def _async_update_data(self) -> dict[str, Any]:
        """Fetch a fresh snapshot from the Kraken GraphQL API."""
        try:
            await self._ensure_valid_token()
            return await self._graphql_query()
        except ConfigEntryAuthFailed:
            raise
        except aiohttp.ClientConnectionError as err:
            raise UpdateFailed(f"Cannot connect to E.ON Next: {err}") from err
        except aiohttp.ClientResponseError as err:
            raise UpdateFailed(f"E.ON Next API error {err.status}: {err.message}") from err

    # ── Token management ───────────────────────────────────────────────────────

    async def _ensure_valid_token(self) -> None:
        """Proactively refresh the access token if it expires within 5 minutes."""
        if time.time() >= self._token_expiry - 300:
            await self._do_token_refresh()

    async def _do_token_refresh(self) -> None:
        """Exchange the refresh token for a new access token."""
        try:
            async with self._session.post(
                REFRESH_URL,
                json={"refreshToken": self._refresh_token},
                headers={"X-API-KEY": API_KEY, "Content-Type": "application/json"},
            ) as response:
                if response.status in (401, 403):
                    # Refresh token has itself expired — need the user to log in again
                    raise ConfigEntryAuthFailed(
                        "E.ON Next refresh token has expired. "
                        "Please re-enter your credentials in the integration settings."
                    )
                response.raise_for_status()
                body = await response.json()

        except aiohttp.ClientConnectionError as err:
            raise UpdateFailed(f"Token refresh failed — connection error: {err}") from err

        self._token = body["token"]
        self._refresh_token = body["refreshToken"]
        self._token_expiry = body["tokenExpiresIn"]

        # Persist the new tokens so they survive an HA restart
        self.hass.config_entries.async_update_entry(
            self._entry,
            data={
                **self._entry.data,
                CONF_TOKEN: self._token,
                CONF_REFRESH_TOKEN: self._refresh_token,
                CONF_TOKEN_EXPIRY: self._token_expiry,
            },
        )
        _LOGGER.debug("E.ON Next access token refreshed successfully")

    # ── GraphQL ────────────────────────────────────────────────────────────────

    async def _graphql_query(self) -> dict[str, Any]:
        """Run QUERY_ALL_DATA and return the data dict."""
        payload = {
            "query": QUERY_ALL_DATA,
            "variables": {"accountNumber": self.account_number},
        }
        headers = {
            "Authorization": f"Bearer {self._token}",
            "Content-Type": "application/json",
        }

        async with self._session.post(
            GRAPHQL_URL, json=payload, headers=headers
        ) as response:
            if response.status == 401:
                # Token rejected mid-session — refresh once and retry
                _LOGGER.debug("Got 401 from GraphQL; refreshing token and retrying")
                await self._do_token_refresh()
                headers["Authorization"] = f"Bearer {self._token}"
                async with self._session.post(
                    GRAPHQL_URL, json=payload, headers=headers
                ) as retry:
                    retry.raise_for_status()
                    result = await retry.json()
            else:
                response.raise_for_status()
                result = await response.json()

        if "errors" in result:
            # Check specifically for JWT expiry errors and retry once
            for error in result["errors"]:
                if "expired" in error.get("message", "").lower():
                    _LOGGER.debug("JWT expired in GraphQL error; refreshing and retrying")
                    await self._do_token_refresh()
                    return await self._graphql_query()
            msgs = [e.get("message", "unknown") for e in result["errors"]]
            raise UpdateFailed(f"GraphQL errors: {'; '.join(msgs)}")

        data: dict[str, Any] = result.get("data", {})

        # Cache the device ID in the config entry on first successful fetch
        flex_device = data.get("registeredKrakenflexDevice")
        if flex_device and not self.device_id:
            self.device_id = flex_device.get("krakenflexDeviceId")
            self.hass.config_entries.async_update_entry(
                self._entry,
                data={**self._entry.data, CONF_DEVICE_ID: self.device_id},
            )

        return data

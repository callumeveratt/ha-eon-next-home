"""DataUpdateCoordinator for E.ON Next EV Smart Charging."""
from __future__ import annotations

import asyncio
import logging
import time
from datetime import timedelta
from typing import Any

import aiohttp

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_API_KEY
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .auth import InvalidAuthError, async_obtain_token
from .const import (
    CONF_ACCOUNT_NUMBER,
    CONF_DEVICE_ID,
    CONF_SCAN_INTERVAL,
    CONF_TOKEN,
    CONF_TOKEN_EXPIRY,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    GRAPHQL_URL,
    MUTATION_SET_VEHICLE_PREFS,
    QUERY_ALL_DATA,
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

        # Entries created before the API-key switch have no key; the first
        # token fetch raises ConfigEntryAuthFailed so HA prompts for one.
        self._api_key: str | None = entry.data.get(CONF_API_KEY)
        self._token: str = entry.data.get(CONF_TOKEN, "")
        self._token_expiry: int = entry.data.get(CONF_TOKEN_EXPIRY, 0)  # Unix timestamp
        # The energy coordinators share this token, so refreshes must not race
        self._token_lock = asyncio.Lock()

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

    @property
    def ev_device_id(self) -> str | None:
        """Return the REST API device ID for the EV (from the devices query)."""
        data = self.data or {}
        ev_device = next(
            (
                d
                for d in (data.get("devices") or [])
                if d.get("deviceType") == "ELECTRIC_VEHICLES"
            ),
            None,
        )
        return ev_device.get("id") if ev_device else None

    async def async_set_preferences(
        self,
        departure_time: str,
        target_soc: int,
    ) -> None:
        """Write autopilot departure time and target SoC via the Kraken GraphQL API.

        Uses the setVehicleChargePreferences mutation.
        departure_time must be in "HH:MM" format; target_soc is a whole percentage.
        Weekend values are read from the current coordinator state and passed through
        unchanged (the E.ON Next app has no UI for them).
        """
        # Preserve current weekend values — the mutation requires them
        prefs: dict[str, Any] = (self.data or {}).get("vehicleChargingPreferences") or {}
        wknd_time = (prefs.get("weekendTargetTime") or departure_time)[:5]
        wknd_soc = int(prefs.get("weekendTargetSoc") or target_soc)

        await self.async_graphql_mutation(
            MUTATION_SET_VEHICLE_PREFS,
            {
                "input": {
                    "accountNumber": self.account_number,
                    "weekdayTargetTime": departure_time[:5],
                    "weekdayTargetSoc": target_soc,
                    "weekendTargetTime": wknd_time,
                    "weekendTargetSoc": wknd_soc,
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
        data = await self.async_graphql_query(
            QUERY_ALL_DATA, {"accountNumber": self.account_number}
        )

        # Cache the device ID in the config entry on first successful fetch
        flex_device = data.get("registeredKrakenflexDevice")
        if flex_device and not self.device_id:
            self.device_id = flex_device.get("krakenflexDeviceId")
            self.hass.config_entries.async_update_entry(
                self._entry,
                data={**self._entry.data, CONF_DEVICE_ID: self.device_id},
            )

        return data

    # ── Token management ───────────────────────────────────────────────────────

    async def _ensure_valid_token(self) -> None:
        """Proactively fetch a new access token if it expires within 5 minutes."""
        async with self._token_lock:
            if time.time() >= self._token_expiry - 300:
                await self._do_token_refresh()

    async def _do_token_refresh(self) -> None:
        """Exchange the stored API key for a new Kraken token."""
        if not self._api_key:
            raise ConfigEntryAuthFailed(
                "E.ON Next no longer supports email/password sign-in. "
                "Re-authenticate with your account API key."
            )

        try:
            token, expiry = await async_obtain_token(self._session, self._api_key)
        except InvalidAuthError as err:
            raise ConfigEntryAuthFailed(
                f"E.ON Next rejected the API key: {err}"
            ) from err
        except aiohttp.ClientConnectionError as err:
            raise UpdateFailed(f"Token refresh failed — connection error: {err}") from err

        self._token = token
        self._token_expiry = expiry

        # Persist the new token so it survives an HA restart
        self.hass.config_entries.async_update_entry(
            self._entry,
            data={
                **self._entry.data,
                CONF_TOKEN: self._token,
                CONF_TOKEN_EXPIRY: self._token_expiry,
            },
        )
        _LOGGER.debug("E.ON Next access token refreshed successfully")

    # ── GraphQL ────────────────────────────────────────────────────────────────

    async def async_graphql_query(
        self, query: str, variables: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """Run a GraphQL query with a valid token and return the data dict.

        Shared by the EV and energy coordinators. Raises UpdateFailed on API or
        connection errors and ConfigEntryAuthFailed if the API key is rejected.
        """
        try:
            await self._ensure_valid_token()
            return await self._graphql_query(query, variables or {}, retried=False)
        except ConfigEntryAuthFailed:
            raise
        except aiohttp.ClientConnectionError as err:
            raise UpdateFailed(f"Cannot connect to E.ON Next: {err}") from err
        except aiohttp.ClientResponseError as err:
            raise UpdateFailed(f"E.ON Next API error {err.status}: {err.message}") from err

    async def _graphql_query(
        self, query: str, variables: dict[str, Any], retried: bool
    ) -> dict[str, Any]:
        """POST a query, refreshing the token once if Kraken rejects it."""
        payload = {"query": query, "variables": variables}
        headers = {
            "Authorization": f"Bearer {self._token}",
            "Content-Type": "application/json",
        }

        async with self._session.post(
            GRAPHQL_URL, json=payload, headers=headers
        ) as response:
            if response.status == 401 and not retried:
                # Token rejected mid-session — refresh once and retry
                _LOGGER.debug("Got 401 from GraphQL; refreshing token and retrying")
                async with self._token_lock:
                    await self._do_token_refresh()
                return await self._graphql_query(query, variables, retried=True)
            response.raise_for_status()
            result = await response.json()

        if "errors" in result:
            # Check specifically for JWT expiry errors and retry once
            if not retried and any(
                "expired" in error.get("message", "").lower() for error in result["errors"]
            ):
                _LOGGER.debug("JWT expired in GraphQL error; refreshing and retrying")
                async with self._token_lock:
                    await self._do_token_refresh()
                return await self._graphql_query(query, variables, retried=True)
            msgs = [e.get("message", "unknown") for e in result["errors"]]
            raise UpdateFailed(f"GraphQL errors: {'; '.join(msgs)}")

        return result.get("data", {})

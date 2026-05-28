"""Config flow for E.ON Next Home."""
from __future__ import annotations

import logging
from typing import Any

import aiohttp
import voluptuous as vol

from homeassistant import config_entries
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD
from homeassistant.core import callback
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import (
    API_KEY,
    AUTH_URL,
    CONF_ACCOUNT_NUMBER,
    CONF_REFRESH_TOKEN,
    CONF_SCAN_INTERVAL,
    CONF_TOKEN,
    CONF_TOKEN_EXPIRY,
    DEFAULT_SCAN_INTERVAL,
    MIN_SCAN_INTERVAL,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)

STEP_USER_DATA_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_EMAIL): str,
        vol.Required(CONF_PASSWORD): str,
    }
)


class EonNextHomeConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for E.ON Next Home."""

    VERSION = 1

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: config_entries.ConfigEntry) -> EonNextHomeOptionsFlow:
        """Return the options flow handler."""
        return EonNextHomeOptionsFlow()

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle the initial step — ask for email and password."""
        errors: dict[str, str] = {}

        if user_input is not None:
            try:
                auth_data = await self._authenticate(
                    user_input[CONF_EMAIL],
                    user_input[CONF_PASSWORD],
                )
            except aiohttp.ClientConnectionError:
                errors["base"] = "cannot_connect"
            except InvalidAuthError:
                errors["base"] = "invalid_auth"
            except Exception:  # pylint: disable=broad-except
                _LOGGER.exception("Unexpected exception during E.ON Next authentication")
                errors["base"] = "unknown"
            else:
                # Prevent the same account being added twice
                await self.async_set_unique_id(auth_data[CONF_ACCOUNT_NUMBER])
                self._abort_if_unique_id_configured()

                return self.async_create_entry(
                    title=f"E.ON Next Home ({auth_data[CONF_ACCOUNT_NUMBER]})",
                    data={
                        CONF_EMAIL: user_input[CONF_EMAIL],
                        CONF_PASSWORD: user_input[CONF_PASSWORD],
                        CONF_TOKEN: auth_data[CONF_TOKEN],
                        CONF_REFRESH_TOKEN: auth_data[CONF_REFRESH_TOKEN],
                        CONF_TOKEN_EXPIRY: auth_data[CONF_TOKEN_EXPIRY],
                        CONF_ACCOUNT_NUMBER: auth_data[CONF_ACCOUNT_NUMBER],
                    },
                )

        return self.async_show_form(
            step_id="user",
            data_schema=STEP_USER_DATA_SCHEMA,
            errors=errors,
        )

    async def _authenticate(self, email: str, password: str) -> dict[str, Any]:
        """Call the E.ON Next login endpoint and return token data."""
        session = async_get_clientsession(self.hass)

        async with session.post(
            AUTH_URL,
            json={"email": email, "password": password},
            headers={"X-API-KEY": API_KEY, "Content-Type": "application/json"},
        ) as response:
            if response.status in (401, 403):
                raise InvalidAuthError
            response.raise_for_status()
            body = await response.json()

        return {
            CONF_TOKEN: body["auth"]["token"],
            CONF_REFRESH_TOKEN: body["auth"]["refreshToken"],
            CONF_TOKEN_EXPIRY: body["auth"]["tokenExpiresIn"],  # Unix timestamp
            CONF_ACCOUNT_NUMBER: body["accountNumber"],
        }


class EonNextHomeOptionsFlow(config_entries.OptionsFlow):
    """Handle options for E.ON Next Home — shown when the user clicks Configure."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Show the options form."""
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)

        current_interval = self.config_entry.options.get(
            CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL
        )

        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_SCAN_INTERVAL,
                        default=current_interval,
                    ): vol.All(vol.Coerce(int), vol.Range(min=MIN_SCAN_INTERVAL)),
                }
            ),
        )


class InvalidAuthError(Exception):
    """Raised when the email/password combination is rejected."""

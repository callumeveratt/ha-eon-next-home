"""Config flow for E.ON Next Home."""
from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any

import aiohttp
import voluptuous as vol

from homeassistant import config_entries
from homeassistant.const import CONF_API_KEY
from homeassistant.core import callback
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .auth import InvalidAuthError, async_get_account_number, async_obtain_token
from .const import (
    CONF_ACCOUNT_NUMBER,
    CONF_DEVICE_ID,
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
        vol.Required(CONF_API_KEY): str,
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
        """Handle the initial step — ask for the account API key."""
        errors: dict[str, str] = {}

        if user_input is not None:
            auth_data = await self._try_authenticate(user_input[CONF_API_KEY], errors)
            if auth_data is not None:
                # Prevent the same account being added twice
                await self.async_set_unique_id(auth_data[CONF_ACCOUNT_NUMBER])
                self._abort_if_unique_id_configured()

                return self.async_create_entry(
                    title=f"E.ON Next Home ({auth_data[CONF_ACCOUNT_NUMBER]})",
                    data=auth_data,
                )

        return self.async_show_form(
            step_id="user",
            data_schema=STEP_USER_DATA_SCHEMA,
            errors=errors,
        )

    async def async_step_reauth(
        self, entry_data: Mapping[str, Any]
    ) -> FlowResult:
        """Start reauth when the coordinator raises ConfigEntryAuthFailed."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Ask the user for a (new) account API key."""
        errors: dict[str, str] = {}
        reauth_entry = self._get_reauth_entry()

        if user_input is not None:
            auth_data = await self._try_authenticate(user_input[CONF_API_KEY], errors)
            if auth_data is not None:
                # Don't let the user swap the entry over to a different account
                await self.async_set_unique_id(auth_data[CONF_ACCOUNT_NUMBER])
                self._abort_if_unique_id_mismatch(reason="wrong_account")

                # Replace (not merge) the data so pre-API-key credentials are dropped
                data = dict(auth_data)
                if device_id := reauth_entry.data.get(CONF_DEVICE_ID):
                    data[CONF_DEVICE_ID] = device_id

                return self.async_update_reload_and_abort(reauth_entry, data=data)

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=STEP_USER_DATA_SCHEMA,
            errors=errors,
        )

    async def _try_authenticate(
        self, api_key: str, errors: dict[str, str]
    ) -> dict[str, Any] | None:
        """Validate the API key; return entry data, or None with errors filled in."""
        api_key = api_key.strip()
        session = async_get_clientsession(self.hass)
        try:
            token, expiry = await async_obtain_token(session, api_key)
            account_number = await async_get_account_number(session, token)
        except aiohttp.ClientConnectionError:
            errors["base"] = "cannot_connect"
        except InvalidAuthError as err:
            _LOGGER.debug("E.ON Next rejected the API key: %s", err)
            errors["base"] = "invalid_auth"
        except Exception:  # pylint: disable=broad-except
            _LOGGER.exception("Unexpected exception during E.ON Next authentication")
            errors["base"] = "unknown"
        else:
            return {
                CONF_API_KEY: api_key,
                CONF_TOKEN: token,
                CONF_TOKEN_EXPIRY: expiry,
                CONF_ACCOUNT_NUMBER: account_number,
            }
        return None


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

"""Kraken API-key authentication for E.ON Next Home."""
from __future__ import annotations

import logging
from typing import Any

import aiohttp

from .const import GRAPHQL_URL, MUTATION_OBTAIN_TOKEN, QUERY_ACCOUNTS

_LOGGER = logging.getLogger(__name__)

# Enough of an error page to tell a WAF block from a rate limit or a Kraken
# message, without dumping a whole HTML page into the log
_BODY_SNIPPET_LEN = 500

# Response headers worth logging when a request is refused
_DIAGNOSTIC_HEADERS = (
    "X-Kraken-Correlation-ID",
    "Retry-After",
    "Server",
    "Content-Type",
    "X-Amzn-ErrorType",
    "X-Amzn-RequestId",
)


class InvalidAuthError(Exception):
    """Raised when E.ON Next rejects the API key."""


async def async_check_response(response: aiohttp.ClientResponse, request: str) -> None:
    """Raise for a non-2xx response, logging what E.ON Next sent back.

    `request` names the call (e.g. "token exchange") so the log says which
    one failed. The raised error's message carries the start of the body, so
    it also shows up in coordinator errors.
    """
    if response.ok:
        return

    try:
        body = await response.text()
    except (aiohttp.ClientError, UnicodeDecodeError) as err:
        body = f"<unreadable body: {err}>"
    snippet = " ".join(body.split())[:_BODY_SNIPPET_LEN]
    headers = {
        name: response.headers[name]
        for name in _DIAGNOSTIC_HEADERS
        if name in response.headers
    }

    _LOGGER.warning(
        "E.ON Next %s returned HTTP %s %s; headers %s; body: %s",
        request,
        response.status,
        response.reason,
        headers,
        snippet or "<empty>",
    )

    raise aiohttp.ClientResponseError(
        response.request_info,
        response.history,
        status=response.status,
        message=f"{response.reason} during {request}: {snippet or '<empty body>'}",
        headers=response.headers,
    )


async def async_obtain_token(
    session: aiohttp.ClientSession, api_key: str
) -> tuple[str, int]:
    """Exchange the account API key for a Kraken token.

    Returns (token, expiry) where expiry is a Unix timestamp.
    """
    async with session.post(
        GRAPHQL_URL,
        json={
            "query": MUTATION_OBTAIN_TOKEN,
            "variables": {"input": {"APIKey": api_key}},
        },
        headers={"Content-Type": "application/json"},
    ) as response:
        await async_check_response(response, "token exchange")
        result = await response.json()

    data = (result.get("data") or {}).get("obtainKrakenToken")
    if result.get("errors") or not data:
        # Any error minting a token from a key means the key is wrong or revoked
        # (or API access is disabled on the account, KT-CT-11334)
        raise InvalidAuthError(
            "; ".join(e.get("message", "unknown") for e in result.get("errors", []))
        )

    return data["token"], int(data["payload"]["exp"])


async def async_get_account_number(
    session: aiohttp.ClientSession, token: str
) -> str:
    """Return the first account number visible to this token."""
    async with session.post(
        GRAPHQL_URL,
        json={"query": QUERY_ACCOUNTS},
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
    ) as response:
        await async_check_response(response, "account lookup")
        result: dict[str, Any] = await response.json()

    accounts = ((result.get("data") or {}).get("viewer") or {}).get("accounts") or []
    if not accounts:
        raise InvalidAuthError("No E.ON Next accounts found for this API key")

    return accounts[0]["number"]

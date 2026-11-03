"""Kraken API-key authentication for E.ON Next Home."""
from __future__ import annotations

from typing import Any

import aiohttp

from .const import GRAPHQL_URL, MUTATION_OBTAIN_TOKEN, QUERY_ACCOUNTS


class InvalidAuthError(Exception):
    """Raised when E.ON Next rejects the API key."""


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
        response.raise_for_status()
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
        response.raise_for_status()
        result: dict[str, Any] = await response.json()

    accounts = ((result.get("data") or {}).get("viewer") or {}).get("accounts") or []
    if not accounts:
        raise InvalidAuthError("No E.ON Next accounts found for this API key")

    return accounts[0]["number"]

"""Shared auth, request and redaction helpers for the read-only E.ON Next probes.

Reads the account API key from EON_API_KEY. The key and the token are kept in
memory only and are never printed or written to disk.
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

GRAPHQL_URL = "https://api.eonnext-kraken.energy/v1/graphql/"
OUTPUT_DIR = Path(__file__).parent / "output"
PAUSE_SECONDS = 1.5

# The only mutation the probes may send: minting a token from the API key.
_TOKEN_MUTATION = """
mutation ObtainKrakenToken($input: ObtainJSONWebTokenInput!) {
  obtainKrakenToken(input: $input) { token payload }
}
"""

_token: str | None = None
_secrets: set[str] = set()
_sensitive: dict[str, str] = {}  # value -> placeholder

# Response keys whose values are personal data, mapped to their placeholder.
_SENSITIVE_KEYS = {
    "number": "<ACCOUNT_NUMBER>",  # account.number; redacted only when it looks like A-XXXX
    "accountNumber": "<ACCOUNT_NUMBER>",
    "mpan": "<MPAN>",
    "mprn": "<MPRN>",
    "serialNumber": "<METER_SERIAL>",
    "meterSerialNumber": "<METER_SERIAL>",
    "deviceId": "<DEVICE_ID>",
    "esmeDeviceId": "<DEVICE_ID>",
    "ihdDeviceId": "<DEVICE_ID>",
    "gsmeDeviceId": "<DEVICE_ID>",
    "address": "<ADDRESS>",
    "richAddress": "<ADDRESS>",
    "addressLine1": "<ADDRESS>",
    "addressLine2": "<ADDRESS>",
    "addressLine3": "<ADDRESS>",
    "addressLine4": "<ADDRESS>",
    "addressLine5": "<ADDRESS>",
    "line1": "<ADDRESS>",
    "line2": "<ADDRESS>",
    "line3": "<ADDRESS>",
    "postcode": "<POSTCODE>",
    "postalCode": "<POSTCODE>",
    "postTown": "<ADDRESS>",
    "town": "<ADDRESS>",
    "locality": "<ADDRESS>",
    "givenName": "<NAME>",
    "familyName": "<NAME>",
    "fullName": "<NAME>",
    "preferredName": "<NAME>",
    "firstName": "<NAME>",
    "lastName": "<NAME>",
    "billingName": "<NAME>",
    "email": "<EMAIL>",
    "mobile": "<PHONE>",
    "landline": "<PHONE>",
    "uprn": "<UPRN>",
    "krakenflexDeviceId": "<DEVICE_ID>",
    "marketSupplyPointId": "<MPAN>",
    "externalIdentifier": "<MPAN>",
    "installCode": "<INSTALL_CODE>",
    "billingEmail": "<EMAIL>",
    "billingAddress": "<ADDRESS>",
    "billingAddressPostcode": "<POSTCODE>",
    "splitAddress": "<ADDRESS>",
    "latitude": "<COORD>",
    "longitude": "<COORD>",
    "liveSecretKey": "<SECRET>",
    "token": "<SECRET>",
    "refreshToken": "<SECRET>",
}


def _api_key() -> str:
    key = os.environ.get("EON_API_KEY", "").strip()
    if not key:
        sys.exit("EON_API_KEY is not set. Set it in the environment and try again.")
    _secrets.add(key)
    return key


def _post(payload: dict, token: str | None) -> tuple[int, dict]:
    """POST a GraphQL payload, backing off on rate limits. Returns (status, body)."""
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    delay = 10.0
    for attempt in range(4):
        time.sleep(PAUSE_SECONDS)
        request = urllib.request.Request(
            GRAPHQL_URL, data=json.dumps(payload).encode(), headers=headers
        )
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                status, body = response.status, json.load(response)
        except urllib.error.HTTPError as err:
            status = err.code
            text = err.read().decode(errors="replace")
            try:
                body = json.loads(text)
            except ValueError:
                body = {"http_error_body": text[:2000]}
        if status == 429 or _is_rate_limited(body):
            print(f"  rate limited, waiting {delay:.0f}s (attempt {attempt + 1})")
            time.sleep(delay)
            delay *= 2
            continue
        return status, body
    return status, body


def _is_rate_limited(body: dict) -> bool:
    text = json.dumps(body.get("errors") or []).lower()
    return "rate limit" in text or "too many requests" in text or "kt-ct-1199" in text


def token() -> str:
    """Mint (once per run) a Kraken token from the API key."""
    global _token
    if _token is None:
        status, body = _post(
            {"query": _TOKEN_MUTATION, "variables": {"input": {"APIKey": _api_key()}}}, None
        )
        data = (body.get("data") or {}).get("obtainKrakenToken")
        if not data:
            sys.exit(f"Could not obtain a token (HTTP {status}): {errors_summary(body)}")
        _token = data["token"]
        _secrets.add(_token)
    return _token


def gql(query: str, variables: dict | None = None) -> dict:
    """Run a read-only GraphQL query. Refuses anything that isn't a query."""
    stripped = re.sub(r"#.*", "", query).strip()
    if re.match(r"(mutation|subscription)\b", stripped, re.IGNORECASE):
        raise ValueError("Probes are read-only: mutations and subscriptions are not allowed")
    if "regenerateSecretKey" in query:
        raise ValueError("regenerateSecretKey must never be called")
    payload: dict = {"query": query}
    if variables:
        payload["variables"] = variables
    status, body = _post(payload, token())
    body["_http_status"] = status
    return body


def errors_summary(body: dict) -> str:
    """One line per error: code and message."""
    out = []
    for err in body.get("errors") or []:
        ext = err.get("extensions") or {}
        code = ext.get("errorCode") or ext.get("code") or ""
        desc = ext.get("errorDescription") or ""
        line = f"{code} {err.get('message', '')}".strip()
        if desc and desc not in line:
            line += f" ({desc})"
        out.append(line)
    if "http_error_body" in body:
        out.append(f"HTTP body: {body['http_error_body'][:300]}")
    return " | ".join(out) or "(no errors)"


def account_number() -> str:
    """The first account number visible to the token (registered as sensitive)."""
    body = gql("query { viewer { accounts { number } } }")
    accounts = ((body.get("data") or {}).get("viewer") or {}).get("accounts") or []
    if not accounts:
        sys.exit(f"No accounts visible: {errors_summary(body)}")
    number = accounts[0]["number"]
    mark_sensitive(number, "<ACCOUNT_NUMBER>")
    return number


def mark_sensitive(value: str, placeholder: str) -> None:
    if value:
        _sensitive[str(value)] = placeholder


def _collect(obj, parent_key: str = "") -> None:
    """Learn sensitive values from known keys so they can be scrubbed everywhere."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k in _SENSITIVE_KEYS and isinstance(v, (str, int)) and str(v).strip():
                if k == "number" and not re.match(r"^A-[0-9A-F]+$", str(v)):
                    continue
                mark_sensitive(str(v), _SENSITIVE_KEYS[k])
            elif k in _SENSITIVE_KEYS and isinstance(v, list):
                for item in v:
                    if isinstance(item, str) and item.strip():
                        mark_sensitive(item, _SENSITIVE_KEYS[k])
            _collect(v, k)
    elif isinstance(obj, list):
        for item in obj:
            _collect(item, parent_key)


def _scrub(obj, key: str = ""):
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            if k in _SENSITIVE_KEYS and v not in (None, "", [], {}):
                if k == "number" and not (isinstance(v, str) and re.match(r"^A-[0-9A-F]+$", v)):
                    out[k] = _scrub(v, k)
                    continue
                if isinstance(v, (str, int, float)):
                    out[k] = _SENSITIVE_KEYS[k]
                    continue
                if isinstance(v, list) and all(isinstance(i, (str, int, float)) for i in v):
                    out[k] = [_SENSITIVE_KEYS[k]] * len(v)
                    continue
            out[k] = _scrub(v, k)
        return out
    if isinstance(obj, list):
        return [_scrub(i, key) for i in obj]
    if isinstance(obj, str):
        return scrub_text(obj)
    return obj


def scrub_text(text: str) -> str:
    """Replace every known secret and personal value inside free text."""
    for secret in _secrets:
        text = text.replace(secret, "<SECRET>")
    for value in sorted(_sensitive, key=len, reverse=True):
        if len(value) >= 4:
            text = text.replace(value, _sensitive[value])
    # Belt and braces: account numbers and 13-digit MPANs in free text.
    text = re.sub(r"\bA-[0-9A-F]{6,10}\b", "<ACCOUNT_NUMBER>", text)
    text = re.sub(r"(?<![\d.])\d{13}(?![\d.])", "<MPAN>", text)
    return text


def redact(obj):
    """Return a redacted deep copy of a response."""
    _collect(obj)
    return _scrub(obj)


def save(name: str, obj) -> Path:
    """Write a redacted response to scripts/probe/output/<name>.json."""
    OUTPUT_DIR.mkdir(exist_ok=True)
    path = OUTPUT_DIR / f"{name}.json"
    path.write_text(json.dumps(redact(obj), indent=2, default=str), encoding="utf-8")
    return path


def report(name: str, body: dict) -> bool:
    """Save a response and print a one-line verdict. Returns True if it had data and no errors."""
    path = save(name, body)
    ok = bool(body.get("data")) and not body.get("errors") and any(
        v is not None for v in (body.get("data") or {}).values()
    )
    verdict = "OK  " if ok else ("PART" if body.get("data") and any(
        v is not None for v in body["data"].values()) else "FAIL")
    print(f"[{verdict}] {name}: {scrub_text(errors_summary(body)) if body.get('errors') else 'data returned'}"
          f"  -> {path.relative_to(Path.cwd()) if path.is_relative_to(Path.cwd()) else path}")
    return ok

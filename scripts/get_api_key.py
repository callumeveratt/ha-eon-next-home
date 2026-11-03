"""Get your E.ON Next account API key for the E.ON Next Home integration.

E.ON Next's website doesn't show the key, so this script uses your signed-in
browser session to read it (or create one if the account has none yet).

1. Sign in at https://www.eonnext.com and open your browser's developer tools
   (F12) on the Network tab.
2. Click around your account until a request to
   api.eonnext-kraken.energy/v1/graphql/ appears, and copy the value of its
   "Authorization" request header. It's only valid for about an hour.
3. Run:  python scripts/get_api_key.py   and paste the header when asked.

Only standard-library Python 3 is needed. Nothing is sent anywhere except
E.ON Next's own API.
"""
from __future__ import annotations

import getpass
import json
import sys
import urllib.error
import urllib.request

GRAPHQL_URL = "https://api.eonnext-kraken.energy/v1/graphql/"


def graphql(auth_header: str, query: str) -> dict:
    """Run a GraphQL query with the browser's Authorization header."""
    request = urllib.request.Request(
        GRAPHQL_URL,
        data=json.dumps({"query": query}).encode(),
        headers={"Authorization": auth_header, "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            result = json.load(response)
    except urllib.error.HTTPError as err:
        sys.exit(f"E.ON Next returned HTTP {err.code}: {err.read().decode(errors='replace')}")

    if result.get("errors"):
        sys.exit("E.ON Next returned an error: "
                 + "; ".join(e.get("message", "unknown") for e in result["errors"]))
    return result["data"]


def main() -> None:
    auth_header = getpass.getpass("Paste the Authorization header (input hidden): ").strip()
    if auth_header.lower().startswith("authorization:"):
        auth_header = auth_header.split(":", 1)[1].strip()
    if not auth_header:
        sys.exit("No header entered.")

    viewer = graphql(auth_header, "{ viewer { liveSecretKey accounts { number } } }")["viewer"]
    accounts = ", ".join(a["number"] for a in viewer.get("accounts") or []) or "none"
    print(f"Signed in. Account(s): {accounts}")

    if viewer.get("liveSecretKey"):
        print("\nThis account already has an API key:\n")
        print(f"    {viewer['liveSecretKey']}\n")
        print("Enter it in Home Assistant. If it looks shortened or masked, you'll need")
        print("to create a new one, which stops the old key working everywhere it's used")
        print("(e.g. Predbat or EnergyDock). Run with --regenerate to do that.")
        if "--regenerate" not in sys.argv:
            return
    else:
        print("\nThis account has no API key yet.")

    answer = input("Create a new API key now? Any existing key will stop working. [y/N] ")
    if answer.strip().lower() != "y":
        sys.exit("No key created.")

    key = graphql(auth_header, "mutation { regenerateSecretKey { key } }")["regenerateSecretKey"]["key"]
    print("\nYour new API key (shown once, keep it safe):\n")
    print(f"    {key}\n")
    print("Enter it in Home Assistant when the E.ON Next Home integration asks for it.")


if __name__ == "__main__":
    main()

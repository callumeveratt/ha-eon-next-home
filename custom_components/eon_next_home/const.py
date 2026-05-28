"""Constants for the E.ON Next Home integration."""
from datetime import timedelta

DOMAIN = "eon_next_home"
ATTRIBUTION = "Data provided by E.ON Next"

# REST auth endpoints (E.ON Next wrapper)
AUTH_URL = "https://api.public.eonnext.com/apps/eonnext-home/v1/user/login"
REFRESH_URL = "https://api.public.eonnext.com/apps/eonnext-home/v1/user/login/refresh"
API_KEY = "f8897b3c25c1bbd655962233ebcab48f"

# Kraken GraphQL endpoint
GRAPHQL_URL = "https://api.eonnext-kraken.energy/v1/graphql/"

# Config entry data keys (in addition to CONF_EMAIL / CONF_PASSWORD from HA)
CONF_REFRESH_TOKEN = "refresh_token"
CONF_TOKEN = "token"
CONF_TOKEN_EXPIRY = "token_expiry"  # Unix timestamp
CONF_ACCOUNT_NUMBER = "account_number"
CONF_DEVICE_ID = "device_id"

# How often to poll the API
UPDATE_INTERVAL = timedelta(minutes=5)

# ── GraphQL ────────────────────────────────────────────────────────────────────
# Single request that fetches all data we need.
QUERY_ALL_DATA = """
query GetEVData($accountNumber: String!) {
  registeredKrakenflexDevice(accountNumber: $accountNumber) {
    krakenflexDeviceId
    vehicleBatterySizeInKwh
    chargePointPowerInKw
    chargePointMake
    chargePointModel
    status
  }
  vehicleChargingPreferences(accountNumber: $accountNumber) {
    weekdayTargetTime
    weekdayTargetSoc
    weekendTargetTime
    weekendTargetSoc
    minimumSocPercentage
    maximumSocPercentage
  }
  devices(accountNumber: $accountNumber) {
    id
    status { current }
    name
    deviceType
  }
  ocppConnection(accountNumber: $accountNumber) {
    isConnected
  }
  plannedDispatches(accountNumber: $accountNumber) {
    start
    end
    delta
    meta { source location }
  }
}
"""

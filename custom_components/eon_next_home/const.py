"""Constants for the E.ON Next Home integration."""

DOMAIN = "eon_next_home"
ATTRIBUTION = "Data provided by E.ON Next"

# Kraken GraphQL endpoint (used for auth and data)
GRAPHQL_URL = "https://api.eonnext-kraken.energy/v1/graphql/"

# Config entry data keys (in addition to CONF_API_KEY from HA)
CONF_TOKEN = "token"
CONF_TOKEN_EXPIRY = "token_expiry"  # Unix timestamp
CONF_ACCOUNT_NUMBER = "account_number"
CONF_DEVICE_ID = "device_id"

# Polling interval — configurable via the integration's options flow
CONF_SCAN_INTERVAL = "scan_interval"
DEFAULT_SCAN_INTERVAL = 5   # minutes
MIN_SCAN_INTERVAL = 2       # hard floor — below this risks rate-limiting

# ── GraphQL ────────────────────────────────────────────────────────────────────
# E.ON Next moved customer sign-in to Auth0 in Oct 2026 and removed email/password
# from obtainKrakenToken, so we authenticate with the account's live API key.
MUTATION_OBTAIN_TOKEN = """
mutation ObtainKrakenToken($input: ObtainJSONWebTokenInput!) {
  obtainKrakenToken(input: $input) {
    token
    payload
  }
}
"""

QUERY_ACCOUNTS = """
query {
  viewer {
    accounts {
      number
    }
  }
}
"""

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

# GraphQL mutation for writing EV charging preferences.
# Requires all five fields; accountNumber is part of the input object.
# Weekend values must be supplied but are not exposed in the E.ON Next app UI —
# we mirror them from the current coordinator state to avoid unintended changes.
MUTATION_SET_VEHICLE_PREFS = """
mutation SetVehicleChargingPreferences($input: VehicleChargingPreferencesInput!) {
  setVehicleChargePreferences(input: $input) {
    krakenflexDevice {
      krakenflexDeviceId
    }
  }
}
"""

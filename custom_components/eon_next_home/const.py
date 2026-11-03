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

# ── Energy (meter readings and tariffs) ────────────────────────────────────────
# Tariffs change rarely; unitRates on a half-hourly tariff covers today and tomorrow.
TARIFF_SCAN_INTERVAL = 60  # minutes
# Meter readings arrive as a daily batch 1–2 days late, so polling often is pointless.
CONSUMPTION_SCAN_INTERVAL = 120  # minutes
# Each run re-imports this many days so late or corrected half-hours are picked up.
CONSUMPTION_OVERLAP_DAYS = 3
# How far back the first import looks. The API simply returns nothing before supply start.
CONSUMPTION_BACKFILL_DAYS = 730
# Kraken rejects pages over its limit with KT-CT-1202; 1000 is accepted.
MEASUREMENTS_PAGE_SIZE = 1000
# Long ranges that start on a single-rate tariff and run into a time-of-use one
# come back without cost data after the switch. Week-long requests are always costed.
MEASUREMENTS_CHUNK_DAYS = 7

# All rates and standing charges are in pence, VAT included.
_TARIFF_FIELDS = "displayName productCode tariffCode standingCharge isExport"

QUERY_TARIFFS = f"""
query GetTariffs($accountNumber: String!) {{
  account(accountNumber: $accountNumber) {{
    properties {{
      id
    }}
    electricityAgreements(active: true) {{
      validFrom
      validTo
      tariff {{
        __typename
        ... on StandardTariff {{ {_TARIFF_FIELDS} unitRate }}
        ... on HalfHourlyTariff {{ {_TARIFF_FIELDS} unitRates {{ validFrom validTo value }} }}
        ... on DayNightTariff {{ {_TARIFF_FIELDS} }}
        ... on ThreeRateTariff {{ {_TARIFF_FIELDS} }}
        ... on FourRateEvTariff {{ {_TARIFF_FIELDS} }}
        ... on PrepayTariff {{ {_TARIFF_FIELDS} }}
      }}
    }}
    gasAgreements(active: true) {{
      validFrom
      validTo
      tariff {{ {_TARIFF_FIELDS} unitRate }}
    }}
  }}
}}
"""

# Half-hourly readings with E.ON Next's own costing. Values are kWh (gas is
# converted from m³ by Kraken); costInclTax.estimatedAmount is in pence despite
# being labelled GBP.
QUERY_MEASUREMENTS = """
query GetMeasurements(
  $propertyId: ID!, $startAt: DateTime!, $endAt: DateTime!,
  $filters: [UtilityFiltersInput], $first: Int!, $after: String
) {
  property(id: $propertyId) {
    measurements(
      startAt: $startAt, endAt: $endAt, timezone: "Europe/London",
      utilityFilters: $filters, first: $first, after: $after
    ) {
      pageInfo {
        hasNextPage
        endCursor
      }
      edges {
        node {
          value
          ... on IntervalMeasurementType {
            startAt
            endAt
          }
          metaData {
            statistics {
              type
              costInclTax {
                estimatedAmount
              }
            }
          }
        }
      }
    }
  }
}
"""

MEASUREMENT_FILTERS = {
    "electricity": {
        "electricityFilters": {
            "readingFrequencyType": "THIRTY_MIN_INTERVAL",
            "readingDirection": "CONSUMPTION",
        }
    },
    "gas": {"gasFilters": {"readingFrequencyType": "THIRTY_MIN_INTERVAL"}},
}

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

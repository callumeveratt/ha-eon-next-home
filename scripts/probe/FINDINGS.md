# E.ON Next meter and tariff probe: findings

Probed 2026-11-03, around 21:40 UTC, using a token minted from the account API key
(`obtainKrakenToken(input: {APIKey})`). The account has one property with a SMETS2
electricity meter (ESME, half-hourly enabled, MHHS enabled) and a SMETS2 gas meter (GSME). The
tariff is Next Drive Smart V6.5 for both fuels.

**Summary:** the API-key token can read half-hourly electricity and gas consumption, including
per-half-hour cost, plus the full tariff with time-of-use unit rates. The earlier "didn't work"
conclusion doesn't hold for this token. Only near-live telemetry is unavailable. The data arrives
roughly 1–2 days late, so it belongs in long-term statistics, not in a live sensor.

## Scripts

All scripts are standard-library Python 3, read-only, and read the key from `EON_API_KEY`. Each
one saves redacted responses to `scripts/probe/output/`, which is gitignored.

| Script | What it checks |
|---|---|
| `probe_common.py` | Shared helper for auth, the read-only guard (refuses mutations and `regenerateSecretKey`), 1.5 s pacing, rate-limit backoff and redaction |
| `probe_schema.py` | Introspection (`__schema`, `__type`) and the meter/tariff-related root fields |
| `probe_type.py` | Prints the fields and arguments of any type, e.g. `python scripts/probe/probe_type.py PropertyType Query.smartMeterTelemetry` |
| `probe_meter_points.py` | Meter points, meters, smart devices and registers |
| `probe_consumption.py` | Every consumption route: `measurements`, `meter.consumption`, telemetry, readings and REST |
| `probe_tariffs.py` | Agreements (current and history), unit rates, standing charges, `applicableRates`, `agreementRates` and REST product rates |

## Everything tried

| # | Query / endpoint | Result | Error code / message | Notes |
|---|---|---|---|---|
| 1 | `__schema { types }` | Works | – | Introspection is enabled for API-key tokens |
| 2 | `__type(name: ...)` | Works | – | Full field and argument info |
| 3 | `account.properties { electricityMeterPoints / gasMeterPoints / meters / smartDevices }` | Works | – | MPAN, MPRN, serials, device IDs, meter type `S2A`, `hasAndAllowsHhReadings: true` |
| 4 | `property.measurements`, elec `THIRTY_MIN_INTERVAL` + `CONSUMPTION` | Works | – | Half-hourly kWh with per-slot cost and standing charge |
| 5 | as #4 plus `marketSupplyPointId: <MPAN>` | Works | – | Same rows; the filter is optional with one meter |
| 6 | as #4 with `RAW_INTERVAL` | Works | – | Identical to 30-minute (raw data is already half-hourly) |
| 7 | as #4 with `DAY_INTERVAL` | Works | – | Daily totals (partial first day if `startAt` isn't midnight) |
| 8 | `measurements`, gas `THIRTY_MIN_INTERVAL` | Works | – | Half-hourly, **already converted to kWh** |
| 9 | `measurements`, gas `DAY_INTERVAL` | Works | – | Daily totals in kWh |
| 10 | `measurements` one-day windows in 2025-06 and 2024-01 | Works, empty | – | No data before E.ON Next supply started |
| 11 | `meter.consumption(grouping: HALF_HOUR, first: 200)` | Fails | `KT-CT-1202` Invalid pagination parameters (more records requested through `first` than the maximum limit) | Use `first` ≤ 100 |
| 12 | `meter.consumption(grouping: HALF_HOUR / DAY, first: 100)` | Works | – | Older API; values only, no cost or units |
| 13 | `smartMeterTelemetry(deviceId: <ESME>)` (latest) | Fails | `KT-GB-4039` "Unable to query smart meter telemetry data." (`errorType: SERVICE_AVAILABILITY`) | Not a permission error: the field resolves, but the backend has no telemetry |
| 14 | `smartMeterTelemetry(..., grouping: HALF_HOURLY, last 2h)` | Fails | `KT-GB-4039` as above | Same for the GSME. Device list has a CAD with status `NOT_APPLICABLE`: no Home Mini style HAN feed |
| 15 | `electricityMeterReadings(accountNumber, meterId, last: 5)` | Works | – | Register reads (cumulative kWh); latest is 2026-04-21, so not useful for live data |
| 16 | first try at #15 with `value`, `registerId` | Fails | Cannot query field 'value' on type 'ElectricityMeterReadingType'. / Cannot query field 'registerId' ... Did you mean 'registers'? | Schema error, fixed by `registers { identifier value }` |
| 17 | `meterPoint.unbilledReadings` | Works, empty | – | – |
| 18 | `consumptionDataByMpxn(mpxn:)` | Fails | Cannot query field 'consumptionDataByMpxn' on type 'Query'. Did you mean 'consumptionEstimates'? | Doesn't exist |
| 19 | REST `GET /v1/electricity-meter-points/<MPAN>/meters/<SERIAL>/consumption/` | Works | – | Octopus-style; accepts **Basic auth with the API key as username** *or* `Bearer <token>` |
| 20 | REST same, `?period_from=2019-01-01T00:00Z&order_by=period` | Works | – | `count: 10275` half-hours of history |
| 21 | REST `GET /v1/gas-meter-points/<MPRN>/meters/<SERIAL>/consumption/` | Works | – | `count: 3163` half-hours; no unit field (see note on gas units) |
| 22 | REST `GET /v1/electricity-meter-points/<MPAN>/` | Works | – | `gsp`, `profile_class` |
| 23 | `account.electricityAgreements(active: true) { tariff { ... on HalfHourlyTariff { unitRates } } }` | Works | – | Current tariff plus a rolling list of time-of-use rates |
| 24 | `account.gasAgreements(active: true)` | Works | – | Single unit rate and standing charge |
| 25 | `meterPoint.agreements(includeInactive: true)` | Works | – | Full tariff history since supply start |
| 26 | `electricityAgreement.timeOfUseScheme` | Works, `null` | – | Time-of-use is carried as `unitRates` windows, not as a scheme |
| 27 | `applicableRates(accountNumber, mpxn, startAt, endAt)` | Works | – | Merged rate windows for any time range, elec and gas |
| 28 | `agreementRates(agreementId:)` | Fails | `KT-CT-1111` Unauthorized (the viewer is not authorized to execute the query/mutation; check authentication or roles/permissions) | **A genuine permission limit** for the API-key token. Not needed, because #23 and #27 cover it |
| 29 | REST `GET /v1/products/<code>/electricity-tariffs/<tariff>/standard-unit-rates/` | Works (no auth) | – | Public time-of-use rate history (396 rows) |
| 30 | REST `.../standing-charges/` (elec and gas) | Works (no auth) | – | – |
| 31 | REST `.../day-unit-rates/`, `.../night-unit-rates/` | Fails | HTTP 400 "This tariff has standard rates, not day and night." | Expected for this tariff |
| 32 | REST `GET /v1/products/` | Works | – | 235 products |

Not tried: `supplyPointTimeOfUseReadings`, which needs a `timeOfUseSchemeName`, and this account has
no scheme (#26).

## Working routes in detail

### Meter points (#3)

```graphql
query MeterPoints($accountNumber: String!) {
  account(accountNumber: $accountNumber) {
    properties {
      electricityMeterPoints {
        mpan status direction measurementClass smartStartDate isMhhsEnabled
        meters(includeInactive: false) {
          id serialNumber meterType consumptionUnits hasAndAllowsHhReadings
          smartDevices { deviceId type status manufacturer model paymentMode }
        }
      }
      gasMeterPoints {
        mprn status
        meters(includeInactive: false) {
          id serialNumber meterType consumptionUnits hasAndAllowsHhReadings
          smartDevices { deviceId type status }
        }
      }
    }
  }
}
```

```json
{"mpan": "<MPAN>", "status": "ON_SUPPLY", "direction": "IMPORT", "measurementClass": "A",
 "smartStartDate": "2020-09-23", "isMhhsEnabled": true,
 "meters": [{"serialNumber": "<METER_SERIAL>", "meterType": "S2A", "consumptionUnits": "kWh",
   "hasAndAllowsHhReadings": true,
   "smartDevices": [{"deviceId": "<DEVICE_ID>", "type": "ESME", "status": "COMMISSIONED",
                     "manufacturer": "1063 - Landis and Gyr", "paymentMode": "CREDIT"}]}]}
```

The gas meter point is the same shape (`consumptionUnits: "m³"`, device type `GSME`).
`smartDeviceNetworks` also lists the comms hub (CHF), GPF, CAD and PPMID.

### Half-hourly consumption with cost: `property.measurements` (#4, #8). Recommended route.

```graphql
query M($accountNumber: String!, $startAt: DateTime, $endAt: DateTime,
        $filters: [UtilityFiltersInput], $first: Int) {
  account(accountNumber: $accountNumber) {
    properties {
      measurements(startAt: $startAt, endAt: $endAt, timezone: "Europe/London",
                   utilityFilters: $filters, first: $first) {
        pageInfo { hasNextPage endCursor }
        edges { node {
          value unit readAt
          ... on IntervalMeasurementType { startAt endAt durationInSeconds }
          metaData { statistics { type value costInclTax { estimatedAmount costCurrency } } }
        } }
      }
    }
  }
}
```

Electricity filter: `[{"electricityFilters": {"readingFrequencyType": "THIRTY_MIN_INTERVAL", "readingDirection": "CONSUMPTION"}}]`.
Gas filter: `[{"gasFilters": {"readingFrequencyType": "THIRTY_MIN_INTERVAL"}}]`.

```json
{"source": "Amphio", "value": "0.149000000000000000", "unit": "kwh",
 "readAt": "2026-11-02T00:00:00+00:00", "startAt": "2026-11-01T23:30:00+00:00",
 "endAt": "2026-11-02T00:00:00+00:00", "durationInSeconds": 1800,
 "metaData": {"statistics": [
   {"type": "CONSUMPTION_COST", "value": "0.149", "costInclTax": {"estimatedAmount": "3.6289", "costCurrency": "GBP"}},
   {"type": "STANDING_CHARGE_COST", "value": null, "costInclTax": {"estimatedAmount": "1.19048", "costCurrency": "GBP"}}]}}
```

**Long ranges can lose their cost data.** If a `measurements` range starts during a single-rate
tariff and runs well past a switch to a time-of-use tariff, every half-hour after the switch comes
back with `statistics: []`. On this account, a range from March to November was costed for only
1,056 of 10,274 half-hours, with nothing after 21 Apr 2026, when Next Drive began. A 5-day range
across the same switch, and every 7-day chunk across the whole history, were 100% costed for both
fuels. The integration therefore fetches in 7-day chunks.

**Watch the cost units.** `estimatedAmount` is labelled `GBP`, but it is actually **pence**:
0.149 kWh × 24.355 p/kWh = 3.6289. Standing charge is 57.143 p/day ÷ 48 = 1.19048 per half-hour.
I checked this against every half-hour on 1 Nov: 00:00–06:00 comes out at exactly 7.619 p/kWh and
the rest at 24.355 p/kWh, which matches the tariff.

### Older route: `meter.consumption` (#12)

```graphql
meters(includeInactive: false) {
  consumption(startAt: $startAt, grouping: HALF_HOUR, timezone: "Europe/London", first: 100) {
    edges { node { value startAt endAt } }
  }
}
```
→ `{"value": "0.190000000000000000", "startAt": "2026-11-01T23:00:00+00:00", "endAt": "2026-11-01T23:30:00+00:00"}`.
This route has no units or cost, and pages are capped at 100.

### REST consumption (#19–21)

`GET https://api.eonnext-kraken.energy/v1/electricity-meter-points/<MPAN>/meters/<SERIAL>/consumption/?period_from=...&page_size=...`
with `Authorization: Basic base64("<API key>:")`, or with the Bearer token.

```json
{"count": 288, "next": ".../consumption/?page=2&page_size=5", "previous": null,
 "results": [{"consumption": 0.149, "interval_start": "2026-11-01T23:30:00Z", "interval_end": "2026-11-02T00:00:00Z"}]}
```

This route needs the MPAN and the meter serial, and returns no cost. **Gas units:** REST rows
carry no unit. On Octopus's Kraken, SMETS2 gas REST data is in m³, whereas `measurements` returns
gas explicitly in kWh. I couldn't confirm which one E.ON's REST uses, because the probed
half-hours had 0.0 gas usage, so prefer `measurements` for gas.

### Tariffs (#23, #24, #25, #27)

```graphql
query A($accountNumber: String!) {
  account(accountNumber: $accountNumber) {
    electricityAgreements(active: true) {
      validFrom validTo
      tariff {
        __typename
        ... on StandardTariff { productCode tariffCode standingCharge unitRate }
        ... on HalfHourlyTariff { productCode tariffCode standingCharge
                                  unitRates { validFrom validTo value preVatValue rateType } }
        ... on DayNightTariff { productCode tariffCode standingCharge dayRate nightRate }
      }
    }
    gasAgreements(active: true) {
      validFrom validTo
      tariff { productCode tariffCode standingCharge preVatStandingCharge unitRate preVatUnitRate }
    }
  }
}
```

```json
{"validFrom": "2026-04-29T23:00:00+00:00", "validTo": "2027-04-29T23:00:00+00:00",
 "tariff": {"__typename": "HalfHourlyTariff", "productCode": "NEXT_DRIVE_SMART_V6_5",
   "tariffCode": "E-TOU-NEXT_DRIVE_SMART_V6_5-E", "standingCharge": 57.143,
   "unitRates": [
     {"validFrom": "2026-11-03T00:00:00+00:00", "validTo": "2026-11-03T06:00:00+00:00", "value": 7.619, "rateType": "STANDARD"},
     {"validFrom": "2026-11-03T06:00:00+00:00", "validTo": "2026-11-04T00:00:00+00:00", "value": 24.355, "rateType": "STANDARD"},
     {"validFrom": "2026-11-04T00:00:00+00:00", "validTo": "2026-11-04T06:00:00+00:00", "value": 7.619, "rateType": "STANDARD"},
     {"validFrom": "2026-11-04T06:00:00+00:00", "validTo": "2026-11-05T00:00:00+00:00", "value": 24.355, "rateType": "STANDARD"}]}}
```
Gas: `{"tariffCode": "G-1R-NEXT_DRIVE_SMART_GAS_V6_5-E", "standingCharge": 29.1123, "preVatStandingCharge": 27.726, "unitRate": 6.28005, "preVatUnitRate": 5.981}`.

`unitRates` covers today and tomorrow (4 windows). All values are in **pence** and include VAT.
Electricity is currently zero-rated, so `value == preVatValue`. See "VAT on electricity" under
Open questions.

`applicableRates(accountNumber, mpxn, startAt, endAt, first: 100) { edges { node { value validFrom validTo } } }`
returns the same windows merged and clipped to the requested range. It works for any past or future
range and for gas too, so it is the simplest way to get the price at time T:
`{"value": "7.61900", "validFrom": "2026-11-04T00:00:00+00:00", "validTo": "2026-11-04T06:00:00+00:00"}`.

Agreement history (#25) goes back to supply start: NEXT_FLEX (1 day), then
NEXT_PLEDGE_TRACKER_12M_V10, then NEXT_DRIVE_SMART_V6_4, then V6_5. Gas follows the same sequence.

## Freshness and history

| | Electricity | Gas |
|---|---|---|
| Granularity | 30 min (also daily via `DAY_INTERVAL` / `grouping: DAY`) | 30 min, daily |
| Units | kWh | kWh (`measurements`); m³ likely on REST, unverified |
| Latest interval at probe time | ended 2026-11-02 00:00 UTC, **~46 h behind** | ended 2026-11-03 00:00 UTC, **~22 h behind** |
| Earliest data | 2026-03-30 00:00 BST, matching supply start (10,275 half-hours via REST, a few gaps) | 2026-08-29 (3,163 half-hours), although gas supply also started in March |
| Includes cost | Yes, per half-hour (`measurements` only) | Yes |
| Near-live | No: `smartMeterTelemetry` returns `KT-GB-4039` | No |

Data is delivered as a daily batch covering whole days up to midnight. This is one snapshot. The
electricity feed was a day further behind than gas, so expect a lag of 1–2 days that varies from
day to day. Re-run `probe_consumption.py` at different times of day to see when the batch lands.
The gas history gap (March to August) is unexplained; it may come from when half-hourly gas
consent or collection started.

## Recommendation: Energy dashboard

**Yes, the integration can fill the Energy dashboard for electricity and gas, consumption and
cost, without extra hardware, but only as long-term statistics.**

- **Use `async_add_external_statistics`, not a `total_increasing` sensor.** With a 1–2 day lag, a
  live sensor would report two days of usage in the hour the batch arrives, which puts every bar in
  the wrong place and makes the dashboard useless. External statistics let the integration write
  each half-hour against its real time.
  - Add hourly statistics (HA long-term statistics are hourly, so add each pair of half-hours):
    `eon_next_home:electricity_consumption` (kWh), `eon_next_home:gas_consumption` (kWh), and
    optionally `eon_next_home:electricity_cost` / `eon_next_home:gas_cost` in GBP. For cost, divide
    `CONSUMPTION_COST` by 100, and choose whether to add `STANDING_CHARGE_COST`.
  - Use `async_add_external_statistics` (source `eon_next_home`) rather than
    `async_import_statistics`. The latter attaches statistics to an entity's ID, and you don't want
    an entity whose state never matches its history.
  - Work out the running `sum` from the last stored statistic (`get_last_statistics`) and re-import
    a 2–3 day overlap on each run, because late or corrected half-hours do arrive. Backfill from
    supply start on first setup by paging `measurements` (`first` ≤ 100; use the cursor).
  - Polling a few times a day is plenty; this isn't something for the existing 5-minute coordinator.
  - In the Energy dashboard the user picks these statistics under Grid consumption and Gas, and
    chooses "use an entity tracking the total costs" with the cost statistic.
- **Route:** use GraphQL `property.measurements`. It has the same token and endpoint as today,
  needs no serial number, gives gas in kWh, and includes cost. REST works too but needs the serial
  and has no cost. `meter.consumption` is a fallback.

## Recommendation: current price sensor

**A current-price sensor is possible.** For example, `sensor.eon_next_electricity_current_rate`
with `device_class: monetary`, unit `GBP/kWh`, state = `value / 100`.

- Fetch `electricityAgreements(active: true).tariff.unitRates` (or `applicableRates` for now to
  +24 h) once or twice a day and cache the windows. Recalculate the state at each `validTo`
  (`async_track_point_in_time`) rather than polling.
- Time-of-use windows: Next Drive's off-peak is **00:00–06:00 Europe/London local time** at
  7.619 p/kWh, with 24.355 p/kWh otherwise. The API gives the windows as explicit UTC timestamps
  (in summer 23:00Z–05:00Z, in winter 00:00Z–06:00Z, and a 7-hour window on 25 Oct when the clocks
  went back). Use the timestamps as given rather than hard-coding hours, so tariff changes and DST
  are handled automatically. Expose upcoming windows as an attribute and add a
  `binary_sensor.eon_next_off_peak`.
- Add a standing-charge sensor (GBP/day) and a gas unit-rate sensor (fixed, `unitRate / 100`).
- Because consumption is imported as statistics, the price sensor **doesn't drive Energy dashboard
  cost**. Cost comes from the imported cost statistic. The price sensor is for automations and
  display, e.g. running appliances off-peak.

## Open questions

- **EV smart-charge dispatches outside 00:00–06:00:** the account holder believes these are billed
  at peak. No half-hour after 06:00 since Next Drive began used more than 1 kWh, so the data can't
  confirm it either way. Either way the imported cost is E.ON Next's own figure. The tariff shows only `STANDARD` windows
  (no `EV_DEVICE*` rate types), and the probe window had no dispatches. Check whether a dispatched
  half-hour outside the window is costed at 7.619 in `measurements` cost statistics. If it is,
  imported cost is still correct, but a price sensor built from `unitRates` would show peak during
  a dispatch. The integration already has `plannedDispatches` and could override the price then.
- **VAT on electricity (explained):** July's public electricity rates (25.57275 / 7.99995) are
  exactly today's 24.355 / 7.619 plus 5% VAT. The difference is the government's zero-rating of
  domestic electricity from 1 Oct 2026 (originally to 31 Mar 2027; some sources say it has been
  extended). Gas still carries 5% VAT (`preVatUnitRate` 5.981 → `unitRate` 6.28005), which
  confirms that only electricity was zero-rated. The API's inc-VAT `value` fields are therefore
  correct as they stand. Use `value` (not `preVatValue`) for both fuels, and don't hard-code a VAT
  rate, so the sensor stays right when the zero rate ends.
- **Gas REST units and history gap:** see above.

## Security notes

- Only queries were sent, apart from `obtainKrakenToken`, which is needed for auth.
  `probe_common.gql` rejects any `mutation`/`subscription` and any text containing
  `regenerateSecretKey`.
- Saved output is redacted by key (MPAN/MPRN, serials, device IDs, names, addresses, postcode,
  email, account number, tokens) and by learned value, so identifiers inside REST `next` URLs are
  also scrubbed.
- During development, one run printed the meter serial and MPRN inside a REST `next` URL to the
  console before value-learning redaction was added. That output was deleted and regenerated, and
  `output/` now contains no raw identifiers. The values appeared only in this session's terminal
  output, never in the repo.

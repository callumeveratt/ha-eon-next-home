"""Try every consumption route (measurements, meter consumption, telemetry, readings, REST) and record what works."""
from __future__ import annotations

import base64
import json
import os
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from probe_common import PAUSE_SECONDS, errors_summary, gql, report, save, scrub_text, token  # noqa: E402
from probe_meter_points import discover  # noqa: E402

NOW = datetime.now(timezone.utc).replace(microsecond=0)
RECENT = (NOW - timedelta(days=3)).isoformat()
EARLY = "2020-01-01T00:00:00+00:00"
TZ = "Europe/London"

MEASUREMENT_FIELDS = """
  pageInfo { hasNextPage hasPreviousPage }
  edgeCount
  edges { node {
    source value unit readAt
    ... on IntervalMeasurementType { startAt endAt durationInSeconds }
    metaData { statistics { type label value costInclTax { estimatedAmount costCurrency } } }
  } }
"""

MEASUREMENTS = """
query M($accountNumber: String!, $startAt: DateTime, $endAt: DateTime, $filters: [UtilityFiltersInput], $first: Int, $last: Int) {
  account(accountNumber: $accountNumber) {
    properties {
      measurements(startAt: $startAt, endAt: $endAt, timezone: "Europe/London",
                   utilityFilters: $filters, first: $first, last: $last) { %s }
    }
  }
}
""" % MEASUREMENT_FIELDS

METER_CONSUMPTION = """
query C($accountNumber: String!, $startAt: DateTime!, $grouping: ConsumptionGroupings!, $first: Int, $last: Int) {
  account(accountNumber: $accountNumber) {
    properties {
      electricityMeterPoints { meters(includeInactive: false) {
        consumption(startAt: $startAt, grouping: $grouping, timezone: "Europe/London", first: $first, last: $last) {
          edgeCount edges { node { value startAt endAt } }
        } } }
      gasMeterPoints { meters(includeInactive: false) {
        consumption(startAt: $startAt, grouping: $grouping, timezone: "Europe/London", first: $first, last: $last) {
          edgeCount edges { node { value startAt endAt } }
        } } }
    }
  }
}
"""

TELEMETRY = """
query T($deviceId: String!, $start: DateTime, $end: DateTime, $grouping: TelemetryGrouping) {
  smartMeterTelemetry(deviceId: $deviceId, start: $start, end: $end, grouping: $grouping) {
    readAt consumption export demand consumptionDelta costDelta costDeltaWithTax
  }
}
"""

READINGS = """
query R($accountNumber: String!, $meterId: String!) {
  electricityMeterReadings(accountNumber: $accountNumber, meterId: $meterId, last: 5) {
    edges { node { readAt source readingSource readingType registers { identifier name value } } }
  }
}
"""

UNBILLED = """
query U($accountNumber: String!) {
  account(accountNumber: $accountNumber) {
    properties {
      electricityMeterPoints { unbilledReadings { readAt source registers { identifier value } } }
      gasMeterPoints { unbilledReadings { readAt source registers { identifier value } } }
    }
  }
}
"""

RESULTS: list[dict] = []


def record(route: str, body: dict, note: str = "") -> dict:
    ok = report(route, body)
    RESULTS.append({"route": route, "ok": ok, "error": errors_summary(body) if body.get("errors") else "",
                    "note": note})
    return body


def nodes_from_measurements(body: dict) -> list[dict]:
    out = []
    for prop in (((body.get("data") or {}).get("account") or {}).get("properties") or []):
        for edge in ((prop.get("measurements") or {}).get("edges") or []):
            out.append(edge["node"])
    return out


def span(nodes: list[dict], start_key: str, end_key: str) -> str:
    if not nodes:
        return "no rows"
    starts = sorted(n.get(start_key) for n in nodes if n.get(start_key))
    ends = sorted(n.get(end_key) for n in nodes if n.get(end_key))
    lag = ""
    if ends:
        latest = datetime.fromisoformat(ends[-1].replace("Z", "+00:00"))
        lag = f", latest end {ends[-1]} ({(NOW - latest).total_seconds() / 3600:.1f}h behind now)"
    durations = {n.get("durationInSeconds") for n in nodes if n.get("durationInSeconds")}
    units = {n.get("unit") for n in nodes if n.get("unit")}
    return (f"{len(nodes)} rows, first start {starts[0] if starts else '?'}{lag}"
            + (f", durations {sorted(durations)}" if durations else "")
            + (f", units {sorted(units)}" if units else ""))


def probe_measurements(number: str, ids: dict) -> None:
    variants = {
        "measurements_elec_30min": {"electricityFilters": {"readingFrequencyType": "THIRTY_MIN_INTERVAL",
                                                           "readingDirection": "CONSUMPTION"}},
        "measurements_elec_30min_mpan": {"electricityFilters": {
            "readingFrequencyType": "THIRTY_MIN_INTERVAL", "readingDirection": "CONSUMPTION",
            "marketSupplyPointId": ids["electricity"][0]["mpan"] if ids["electricity"] else None}},
        "measurements_elec_raw": {"electricityFilters": {"readingFrequencyType": "RAW_INTERVAL",
                                                         "readingDirection": "CONSUMPTION"}},
        "measurements_elec_day": {"electricityFilters": {"readingFrequencyType": "DAY_INTERVAL",
                                                         "readingDirection": "CONSUMPTION"}},
        "measurements_gas_30min": {"gasFilters": {"readingFrequencyType": "THIRTY_MIN_INTERVAL"}},
        "measurements_gas_day": {"gasFilters": {"readingFrequencyType": "DAY_INTERVAL"}},
    }
    for name, filt in variants.items():
        body = gql(MEASUREMENTS, {"accountNumber": number, "startAt": RECENT, "endAt": NOW.isoformat(),
                                  "filters": [filt], "first": 200})
        nodes = nodes_from_measurements(body)
        record(name, body, f"last 3 days: {span(nodes, 'startAt', 'endAt')}")
        print(f"    {RESULTS[-1]['note']}")

    # History depth: earliest half-hourly rows available.
    for fuel, filt in (("elec", variants["measurements_elec_30min"]), ("gas", variants["measurements_gas_30min"])):
        body = gql(MEASUREMENTS, {"accountNumber": number, "startAt": EARLY, "endAt": NOW.isoformat(),
                                  "filters": [filt], "first": 3})
        nodes = nodes_from_measurements(body)
        record(f"measurements_{fuel}_30min_earliest", body, f"from 2020: {span(nodes, 'startAt', 'endAt')}")
        print(f"    {RESULTS[-1]['note']}")
        # Spot-check an older window directly in case the open-ended query is capped.
        for label, start in (("2025-06", "2025-06-01T00:00:00+00:00"), ("2024-01", "2024-01-10T00:00:00+00:00")):
            end = (datetime.fromisoformat(start) + timedelta(days=1)).isoformat()
            body = gql(MEASUREMENTS, {"accountNumber": number, "startAt": start, "endAt": end,
                                      "filters": [filt], "first": 100})
            nodes = nodes_from_measurements(body)
            record(f"measurements_{fuel}_30min_{label}", body, f"one day {label}: {span(nodes, 'startAt', 'endAt')}")
            print(f"    {RESULTS[-1]['note']}")


def probe_meter_consumption(number: str) -> None:
    for grouping in ("HALF_HOUR", "DAY"):
        # first: 200 returns KT-CT-1202 (over the page limit); 100 is the documented Kraken maximum.
        body = gql(METER_CONSUMPTION, {"accountNumber": number, "startAt": RECENT, "grouping": grouping,
                                       "first": 100})
        rows = []
        for prop in (((body.get("data") or {}).get("account") or {}).get("properties") or []):
            for mp in (prop.get("electricityMeterPoints") or []) + (prop.get("gasMeterPoints") or []):
                for m in mp.get("meters") or []:
                    rows += [e["node"] for e in ((m.get("consumption") or {}).get("edges") or [])]
        record(f"meter_consumption_{grouping.lower()}", body, f"last 3 days: {span(rows, 'startAt', 'endAt')}")
        print(f"    {RESULTS[-1]['note']}")


def probe_telemetry(ids: dict) -> None:
    for device in ids["devices"]:
        if device["type"] not in ("ESME", "GSME"):
            continue
        tag = device["type"].lower()
        body = gql(TELEMETRY, {"deviceId": device["deviceId"]})
        rows = (body.get("data") or {}).get("smartMeterTelemetry") or []
        record(f"telemetry_{tag}_latest", body, f"{len(rows)} rows, readAt {[r.get('readAt') for r in rows][:3]}")
        print(f"    {RESULTS[-1]['note']}")
        body = gql(TELEMETRY, {"deviceId": device["deviceId"], "start": (NOW - timedelta(hours=2)).isoformat(),
                               "end": NOW.isoformat(), "grouping": "HALF_HOURLY"})
        rows = (body.get("data") or {}).get("smartMeterTelemetry") or []
        record(f"telemetry_{tag}_half_hourly_2h", body,
               f"{len(rows)} rows, readAt {[r.get('readAt') for r in rows][-3:]}")
        print(f"    {RESULTS[-1]['note']}")


def probe_readings(number: str, ids: dict) -> None:
    if ids["electricity"]:
        record("electricity_meter_readings", gql(READINGS, {"accountNumber": number,
                                                             "meterId": ids["electricity"][0]["meter_id"]}))
    record("unbilled_readings", gql(UNBILLED, {"accountNumber": number}))
    # E.ON Next-specific names suggested by community code; expected not to exist.
    record("consumptionDataByMpxn", gql("""query($m: String!) { consumptionDataByMpxn(mpxn: $m) { __typename } }""",
                                         {"m": ids["electricity"][0]["mpan"] if ids["electricity"] else ""}))


def rest_get(url: str, auth: str) -> tuple[int, str]:
    time.sleep(PAUSE_SECONDS)
    request = urllib.request.Request(url, headers={"Authorization": auth})
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return response.status, response.read().decode(errors="replace")
    except urllib.error.HTTPError as err:
        return err.code, err.read().decode(errors="replace")
    except urllib.error.URLError as err:
        return 0, str(err.reason)


def probe_rest(ids: dict) -> None:
    """Octopus-style REST consumption endpoints (GET only)."""
    key = os.environ["EON_API_KEY"].strip()
    auths = {"basic_api_key": "Basic " + base64.b64encode(f"{key}:".encode()).decode(),
             "bearer_token": f"Bearer {token()}"}
    paths = []
    if ids["electricity"]:
        e = ids["electricity"][0]
        paths.append(("rest_elec_consumption",
                      f"electricity-meter-points/{e['mpan']}/meters/{e['serial']}/consumption/?page_size=5"))
        paths.append(("rest_elec_meter_point", f"electricity-meter-points/{e['mpan']}/"))
    if ids["gas"]:
        g = ids["gas"][0]
        paths.append(("rest_gas_consumption", f"gas-meter-points/{g['mprn']}/meters/{g['serial']}/consumption/?page_size=5"))
        paths.append(("rest_elec_consumption_earliest",
                      f"electricity-meter-points/{e['mpan']}/meters/{e['serial']}/consumption/"
                      "?period_from=2019-01-01T00:00Z&order_by=period&page_size=3"))
    if ids["gas"]:
        paths.append(("rest_gas_consumption_earliest",
                      f"gas-meter-points/{g['mprn']}/meters/{g['serial']}/consumption/"
                      "?period_from=2019-01-01T00:00Z&order_by=period&page_size=3"))
    paths.append(("rest_products", "products/?page_size=3"))
    for name, path in paths:
        for auth_name, auth in auths.items():
            status, text = rest_get(f"https://api.eonnext-kraken.energy/v1/{path}", auth)
            try:
                body = json.loads(text)
            except ValueError:
                body = {"text": text[:500]}
            save(f"{name}_{auth_name}", {"http_status": status, "body": body})
            note = f"HTTP {status}: {scrub_text(text[:160])}"
            RESULTS.append({"route": f"{name} ({auth_name})", "ok": status == 200, "error": "" if status == 200
                            else note, "note": note if status == 200 else ""})
            print(f"[{'OK  ' if status == 200 else 'FAIL'}] {name} ({auth_name}): {note}")


def main() -> None:
    number, ids = discover()
    print("== property.measurements ==")
    probe_measurements(number, ids)
    print("== meter.consumption ==")
    probe_meter_consumption(number)
    print("== smartMeterTelemetry ==")
    probe_telemetry(ids)
    print("== readings / E.ON-specific ==")
    probe_readings(number, ids)
    print("== REST ==")
    probe_rest(ids)
    save("consumption_summary", RESULTS)


if __name__ == "__main__":
    main()

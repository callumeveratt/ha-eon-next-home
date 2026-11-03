"""Check current and past electricity/gas agreements, unit rates, standing charges and time-of-use windows."""
from __future__ import annotations

import json
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from probe_common import PAUSE_SECONDS, errors_summary, gql, redact, report, save, scrub_text  # noqa: E402
from probe_meter_points import discover  # noqa: E402

NOW = datetime.now(timezone.utc).replace(microsecond=0)

TARIFF_COMMON = "id displayName fullName productCode tariffCode standingCharge preVatStandingCharge isExport tags"
ELEC_TARIFF = f"""
  __typename
  ... on StandardTariff {{ {TARIFF_COMMON} unitRate preVatUnitRate }}
  ... on DayNightTariff {{ {TARIFF_COMMON} dayRate nightRate preVatDayRate preVatNightRate }}
  ... on ThreeRateTariff {{ {TARIFF_COMMON} dayRate nightRate offPeakRate }}
  ... on FourRateEvTariff {{ {TARIFF_COMMON} dayRate nightRate evDevicePeakRate evDeviceOffPeakRate }}
  ... on HalfHourlyTariff {{ {TARIFF_COMMON} unitRates {{ validFrom validTo value preVatValue rateType }} }}
  ... on PrepayTariff {{ {TARIFF_COMMON} }}
"""
TOU = """timeOfUseScheme { name timezone timeslots { timeslot activeFrom activeTo weekdays weekends season { name } } }"""

AGREEMENTS = f"""
query A($accountNumber: String!) {{
  account(accountNumber: $accountNumber) {{
    electricityAgreements(active: true) {{
      id validFrom validTo agreedFrom isRevoked
      tariff {{ {ELEC_TARIFF} }}
      {TOU}
    }}
    gasAgreements(active: true) {{
      id validFrom validTo agreedFrom isRevoked
      tariff {{ {TARIFF_COMMON} unitRate preVatUnitRate }}
    }}
  }}
}}
"""

AGREEMENT_HISTORY = f"""
query H($accountNumber: String!) {{
  account(accountNumber: $accountNumber) {{
    properties {{
      electricityMeterPoints {{ agreements(includeInactive: true) {{
        id validFrom validTo isRevoked tariff {{ {ELEC_TARIFF} }} }} }}
      gasMeterPoints {{ agreements(includeInactive: true) {{
        id validFrom validTo isRevoked tariff {{ {TARIFF_COMMON} unitRate }} }} }}
    }}
  }}
}}
"""

APPLICABLE_RATES = """
query R($accountNumber: String!, $mpxn: String!, $startAt: DateTime!, $endAt: DateTime!) {
  applicableRates(accountNumber: $accountNumber, mpxn: $mpxn, startAt: $startAt, endAt: $endAt, first: 100) {
    totalCount edges { node { value validFrom validTo } }
  }
}
"""

AGREEMENT_RATES = """
query AR($agreementId: ID!) {
  agreementRates(agreementId: $agreementId, first: 50) {
    totalCount
    edges { node { name category pricePerUnit unit unitDisplay currency isSalesTax
                   validityPeriod { start end } } }
  }
}
"""


def rest_get(path: str) -> tuple[int, dict]:
    """Unauthenticated public product endpoints (GET only)."""
    time.sleep(PAUSE_SECONDS)
    try:
        with urllib.request.urlopen(f"https://api.eonnext-kraken.energy/v1/{path}", timeout=30) as r:
            return r.status, json.load(r)
    except urllib.error.HTTPError as err:
        return err.code, {"text": err.read().decode(errors="replace")[:500]}


def main() -> None:
    number, ids = discover()

    print("== account agreements (active) ==")
    body = gql(AGREEMENTS, {"accountNumber": number})
    report("agreements_active", body)
    print(json.dumps(redact(body.get("data")), indent=2)[:3500])
    account = (body.get("data") or {}).get("account") or {}

    print("\n== meter point agreement history ==")
    body = gql(AGREEMENT_HISTORY, {"accountNumber": number})
    report("agreements_history", body)
    for prop in ((body.get("data") or {}).get("account") or {}).get("properties") or []:
        for fuel in ("electricityMeterPoints", "gasMeterPoints"):
            for mp in prop.get(fuel) or []:
                for a in mp.get("agreements") or []:
                    t = a.get("tariff") or {}
                    print(f"  {fuel[:4]} {a['validFrom']} -> {a['validTo']}  {t.get('__typename', '')} "
                          f"{t.get('tariffCode')}  sc={t.get('standingCharge')}")

    print("\n== applicableRates (next 24h) ==")
    for fuel, key in (("electricity", "mpan"), ("gas", "mprn")):
        if not ids[fuel]:
            continue
        body = gql(APPLICABLE_RATES, {"accountNumber": number, "mpxn": ids[fuel][0][key],
                                      "startAt": (NOW - timedelta(hours=12)).isoformat(),
                                      "endAt": (NOW + timedelta(hours=24)).isoformat()})
        report(f"applicable_rates_{fuel}", body)
        edges = (((body.get("data") or {}).get("applicableRates") or {}).get("edges") or [])
        for e in edges[:10]:
            print(f"    {e['node']}")

    print("\n== agreementRates ==")
    for fuel in ("electricityAgreements", "gasAgreements"):
        for a in account.get(fuel) or []:
            body = gql(AGREEMENT_RATES, {"agreementId": str(a["id"])})
            report(f"agreement_rates_{fuel}", body)
            edges = (((body.get("data") or {}).get("agreementRates") or {}).get("edges") or [])
            for e in edges[:10]:
                n = e["node"]
                print(f"    {n['name']}: {n['pricePerUnit']} {n['unitDisplay']} "
                      f"({n['validityPeriod']['start']} -> {n['validityPeriod']['end']})")

    print("\n== public REST product rates ==")
    for fuel in ("electricityAgreements", "gasAgreements"):
        for a in account.get(fuel) or []:
            t = a.get("tariff") or {}
            if not t.get("productCode"):
                continue
            kind = "electricity-tariffs" if fuel.startswith("elec") else "gas-tariffs"
            for leaf in ("standard-unit-rates", "standing-charges", "day-unit-rates", "night-unit-rates"):
                if kind == "gas-tariffs" and leaf in ("day-unit-rates", "night-unit-rates"):
                    continue
                path = f"products/{t['productCode']}/{kind}/{t['tariffCode']}/{leaf}/?page_size=10"
                status, data = rest_get(path)
                name = f"rest_{kind}_{leaf}"
                save(name, {"http_status": status, "path": path, "body": data})
                results = data.get("results") or []
                print(f"[{'OK  ' if status == 200 else 'FAIL'}] {name}: HTTP {status}, {data.get('count')} rows "
                      f"{scrub_text(json.dumps(results[:3]))[:300] if results else scrub_text(json.dumps(data))[:200]}")
            status, data = rest_get(f"products/{t['productCode']}/")
            save(f"rest_product_{kind}", {"http_status": status, "body": data})
            print(f"[{'OK  ' if status == 200 else 'FAIL'}] rest_product {t['productCode']}: HTTP {status} "
                  f"{data.get('display_name')} / {data.get('description', '')[:120]}")


if __name__ == "__main__":
    main()

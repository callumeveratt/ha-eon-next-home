"""Check which electricity and gas meter points, meters and smart devices the account exposes."""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from probe_common import account_number, gql, redact, report  # noqa: E402

QUERY = """
query MeterPoints($accountNumber: String!) {
  account(accountNumber: $accountNumber) {
    properties {
      id
      postcode
      electricityMeterPoints {
        id mpan status direction measurementClass profileClass
        smartStartDate dccServiceFlag smsOperator isMhhsEnabled
        meters(includeInactive: false) {
          id serialNumber isDigital meterType consumptionUnits
          activeFrom activeTo hasAndAllowsHhReadings
          smartImportElectricityMeter { deviceId type status manufacturer model }
          smartExportElectricityMeter { deviceId type status }
          smartDevices { deviceId type status manufacturer model paymentMode }
          registers { id identifier name }
        }
      }
      gasMeterPoints {
        id mprn status smartStartDate
        meters(includeInactive: false) {
          id serialNumber isDigital meterType consumptionUnits
          activeFrom activeTo hasAndAllowsHhReadings
          smartGasMeter { deviceId type status manufacturer model }
          smartDevices { deviceId type status manufacturer model paymentMode }
        }
      }
      smartDeviceNetworks {
        id
        smartDevices { deviceId type status manufacturer model }
      }
    }
  }
}
"""


def extract_ids(body: dict) -> dict:
    """Pull the identifiers other probes need out of a meter-point response (kept in memory only)."""
    ids: dict = {"electricity": [], "gas": [], "devices": [], "property_ids": []}
    for prop in (((body.get("data") or {}).get("account") or {}).get("properties") or []):
        ids["property_ids"].append(prop.get("id"))
        for mp in prop.get("electricityMeterPoints") or []:
            for m in mp.get("meters") or []:
                ids["electricity"].append({"mpan": mp["mpan"], "meter_id": m["id"],
                                           "serial": m["serialNumber"]})
                for d in (m.get("smartDevices") or []) + [m.get("smartImportElectricityMeter") or {}]:
                    if d.get("deviceId"):
                        ids["devices"].append({"deviceId": d["deviceId"], "type": d.get("type")})
        for mp in prop.get("gasMeterPoints") or []:
            for m in mp.get("meters") or []:
                ids["gas"].append({"mprn": mp["mprn"], "meter_id": m["id"], "serial": m["serialNumber"]})
                for d in (m.get("smartDevices") or []) + [m.get("smartGasMeter") or {}]:
                    if d.get("deviceId"):
                        ids["devices"].append({"deviceId": d["deviceId"], "type": d.get("type")})
        for net in prop.get("smartDeviceNetworks") or []:
            for d in net.get("smartDevices") or []:
                ids["devices"].append({"deviceId": d["deviceId"], "type": d.get("type")})
    seen = set()
    ids["devices"] = [d for d in ids["devices"] if not (d["deviceId"] in seen or seen.add(d["deviceId"]))]
    return ids


def discover() -> tuple[str, dict]:
    """Return (account number, ids) for use by the other probes."""
    number = account_number()
    body = gql(QUERY, {"accountNumber": number})
    redact(body)  # registers MPAN/MPRN/serials/device IDs so they're scrubbed from all later output
    return number, extract_ids(body)


def main() -> None:
    number = account_number()
    body = gql(QUERY, {"accountNumber": number})
    report("meter_points", body)
    ids = extract_ids(body)

    print(json.dumps(redact(body.get("data")), indent=2)[:4000])
    print(f"\nElectricity meters: {len(ids['electricity'])}, gas meters: {len(ids['gas'])}, "
          f"smart devices: {[d['type'] for d in ids['devices']]}")


if __name__ == "__main__":
    main()

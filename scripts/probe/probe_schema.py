"""Check whether GraphQL introspection works and dump the meter/tariff-related types."""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from probe_common import OUTPUT_DIR, errors_summary, gql, report, save  # noqa: E402

TYPE_QUERY = """
query T($name: String!) {
  __type(name: $name) {
    name kind description
    fields(includeDeprecated: true) {
      name isDeprecated deprecationReason
      args { name type { name kind ofType { name kind ofType { name kind } } } }
      type { name kind ofType { name kind ofType { name kind ofType { name kind } } } }
    }
    inputFields { name type { name kind ofType { name kind ofType { name kind } } } }
    enumValues { name }
    possibleTypes { name }
  }
}
"""

# Types worth inspecting, plus any root fields matching these words.
TYPES = [
    "Query", "AccountType", "PropertyType", "ElectricityMeterPointType",
    "GasMeterPointType", "ElectricityMeterType", "GasMeterType",
    "ElectricityAgreementType", "GasAgreementType", "AgreementInterface",
    "ElectricityTariffType", "GasTariffType", "StandardTariff", "DayNightTariff",
    "ThreeRateTariff", "HalfHourlyTariff", "PrepayTariff", "TariffType",
    "SmartMeterDeviceType", "SmartMeterTelemetryType", "ConsumptionType",
    "MeasurementInterface", "IntervalMeasurementType", "MeasurementConnection",
    "MeasurementsFilter", "UtilityFiltersInput", "ElectricityFiltersInput",
    "GasFiltersInput", "ReadingFrequencyType", "ReadingDirectionType",
    "ReadingQualityType", "TelemetryGranularity", "EnergyProductType",
    "UnitRateType", "HalfHourlyUnitRate", "ElectricityUnitRate",
]
KEYWORDS = ("meter", "consum", "measure", "tariff", "agreement", "product", "rate",
            "telemetry", "reading", "property", "mpxn", "smart", "supply")


def main() -> None:
    print("Full introspection (__schema):")
    body = gql("query { __schema { queryType { name } types { name kind } } }")
    full_ok = report("schema_full", body)

    print("\nRoot query fields (__type Query):")
    found: dict[str, dict] = {}
    for name in TYPES:
        body = gql(TYPE_QUERY, {"name": name})
        t = (body.get("data") or {}).get("__type")
        if t:
            found[name] = t
            print(f"  {name}: {len(t.get('fields') or t.get('inputFields') or t.get('enumValues') or [])} members")
        elif body.get("errors"):
            print(f"  {name}: {errors_summary(body)}")
            if name == "Query":
                break
        else:
            print(f"  {name}: not found")

    if full_ok:
        types = body_types = gql("query { __schema { types { name } } }")["data"]["__schema"]["types"]
        wanted = [t["name"] for t in body_types if not t["name"].startswith("__")
                  and any(k in t["name"].lower() for k in KEYWORDS) and t["name"] not in found]
        print(f"\nOther matching types from __schema: {len(wanted)}")
        save("schema_matching_type_names", wanted)

    query_fields = [f["name"] for f in (found.get("Query", {}).get("fields") or [])]
    relevant = [f for f in query_fields if any(k in f.lower() for k in KEYWORDS)]
    print(f"\nRoot query fields matching meter/tariff keywords ({len(relevant)}):")
    for f in relevant:
        print(f"  {f}")

    save("schema_types", found)
    (OUTPUT_DIR / "schema_root_fields.txt").write_text("\n".join(query_fields), encoding="utf-8")


if __name__ == "__main__":
    main()

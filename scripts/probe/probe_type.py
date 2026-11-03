"""Print the fields and arguments of named GraphQL types (or root Query fields) via introspection.

Usage: python scripts/probe/probe_type.py TypeName [TypeName ...]
       python scripts/probe/probe_type.py Query.fieldName
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from probe_common import errors_summary, gql  # noqa: E402
from probe_schema import TYPE_QUERY  # noqa: E402


def type_str(t: dict | None) -> str:
    if not t:
        return "?"
    if t["kind"] == "NON_NULL":
        return type_str(t.get("ofType")) + "!"
    if t["kind"] == "LIST":
        return "[" + type_str(t.get("ofType")) + "]"
    return t.get("name") or "?"


def show(name: str) -> None:
    field = None
    if "." in name:
        name, field = name.split(".", 1)
    body = gql(TYPE_QUERY, {"name": name})
    t = (body.get("data") or {}).get("__type")
    if not t:
        print(f"{name}: {errors_summary(body) if body.get('errors') else 'not found'}\n")
        return
    print(f"{t['name']} ({t['kind']})" + (f" - {t['description'][:200]}" if t.get("description") else ""))
    for f in t.get("fields") or []:
        if field and f["name"] != field:
            continue
        args = ", ".join(f"{a['name']}: {type_str(a['type'])}" for a in f.get("args") or [])
        dep = "  [deprecated]" if f.get("isDeprecated") else ""
        print(f"  {f['name']}({args}): {type_str(f['type'])}{dep}" if args
              else f"  {f['name']}: {type_str(f['type'])}{dep}")
    for f in t.get("inputFields") or []:
        print(f"  {f['name']}: {type_str(f['type'])}")
    if t.get("enumValues"):
        print("  enum: " + ", ".join(e["name"] for e in t["enumValues"]))
    if t.get("possibleTypes"):
        print("  implemented by: " + ", ".join(p["name"] for p in t["possibleTypes"]))
    print()


def main() -> None:
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    for name in sys.argv[1:]:
        show(name)


if __name__ == "__main__":
    main()

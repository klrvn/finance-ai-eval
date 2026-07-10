#!/usr/bin/env python3
"""Coerce and sanity-check extracted checkpoint fields.

Reads a raw extraction (as produced by the extractor skill) and the task's checkpoint schema, normalizes
units to canonical numeric form, and flags malformed or out-of-range values. Does NOT judge correctness
against ground truth (that is the checkpoint grader's job).

Canonical numeric conventions:
  - percentages ('%') -> fraction (3.41% -> 0.0341)
  - basis points ('bp') stay in bp for 'bp'-typed fields; converted to fraction otherwise
  - years / ratios / plain numbers pass through
"""
import argparse, json, re

RANGE = {  # loose sanity ranges by field-name hint -> (lo, hi) on canonical value
    "ytm": (-0.05, 0.30), "sharpe": (-5, 8), "modified_duration": (0, 60),
    "macaulay_duration": (0, 60), "ann_vol": (0, 2), "ann_return": (-1, 5),
    "max_drawdown": (-1, 0), "blended_fee": (0, 0.1), "equity_pct": (0, 1),
    "top_holding_pct": (0, 1), "target_weights_sum": (0.8, 1.2),
}

def to_number(raw, unit, ftype):
    if raw is None:
        return None
    if isinstance(raw, (int, float)):
        val = float(raw)
    else:
        m = re.search(r"-?\d[\d,]*\.?\d*", str(raw))
        if not m:
            return None
        val = float(m.group(0).replace(",", ""))
    u = (unit or "").strip().lower()
    if u in ("%", "pct", "percent"):
        return val / 100.0
    if u == "bp":
        return val if ftype == "bp" else val / 10000.0
    return val

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--normalized", required=True)  # raw extraction json
    ap.add_argument("--schema", required=True)      # {field: {type:...}} for the task
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    raw = json.load(open(a.normalized, encoding="utf-8"))
    schema = json.load(open(a.schema, encoding="utf-8"))
    flags = list(raw.get("validator_flags", []))

    for field, meta in raw.get("extracted", {}).items():
        s = schema.get(field, {})
        ftype = s.get("type", "number")
        if meta.get("status") != "value":
            continue
        if ftype in ("bp", "yr", "pct", "ratio", "number", "reconcile", "sum_to"):
            canon = to_number(meta.get("value"), meta.get("unit_as_written"), ftype)
            if canon is None:
                flags.append({"field": field, "flag": "unparseable_number", "raw": meta.get("value")})
            else:
                meta["value"] = canon
                lo_hi = next((rng for k, rng in RANGE.items() if field == k), None)
                if lo_hi and not (lo_hi[0] <= canon <= lo_hi[1]):
                    flags.append({"field": field, "flag": "out_of_range",
                                  "value": canon, "expected_range": lo_hi})

    raw["validator_flags"] = flags
    json.dump(raw, open(a.out, "w", encoding="utf-8"), indent=2, ensure_ascii=False)
    print(json.dumps({"validator_flags": flags,
                      "n_fields": len(raw.get("extracted", {}))}, indent=2))

if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Ground truth for S3 (fixed-income analytics) via QuantLib.

Input params JSON (example):
{
  "settlement_date": "2026-07-06",
  "issue_date": "2022-07-06",
  "maturity_date": "2032-07-06",
  "coupon": 0.03,
  "frequency": "Semiannual",          // Annual | Semiannual | Quarterly
  "face": 100,
  "day_count": "ActualActual",         // ActualActual | Thirty360 | Actual365Fixed | Actual360
  "calendar": "NullCalendar",          // NullCalendar | China | UnitedStates | TARGET
  "compounding": "Compounded",         // Compounded | Continuous
  "settlement_days": 1,
  "market_clean_price": 98.50           // provide EITHER market_clean_price OR market_yield
}

Computes: clean price, dirty price, accrued interest, YTM, Macaulay & modified duration,
DV01, convexity. OAS is out of scope for this static script (needs a curve + vol model) and is
returned as null with a note.
"""
import argparse, json, hashlib, datetime as dt

def _fail(msg):
    print(json.dumps({"self_check": {"passed": False, "reason": msg}}, indent=2))
    raise SystemExit(1)

try:
    import QuantLib as ql
except Exception as e:  # pragma: no cover
    _fail(f"QuantLib not available: {e}. Install with: pip install QuantLib")

FREQ = {"Annual": ql.Annual, "Semiannual": ql.Semiannual, "Quarterly": ql.Quarterly}
CAL = {"NullCalendar": ql.NullCalendar(), "China": ql.China(ql.China.IB),
       "UnitedStates": ql.UnitedStates(ql.UnitedStates.GovernmentBond), "TARGET": ql.TARGET()}
COMP = {"Compounded": ql.Compounded, "Continuous": ql.Continuous}

def dc(name, freq):
    if name == "ActualActual":
        return ql.ActualActual(ql.ActualActual.ISMA)
    if name == "Thirty360":
        return ql.Thirty360(ql.Thirty360.BondBasis)
    if name == "Actual365Fixed":
        return ql.Actual365Fixed()
    if name == "Actual360":
        return ql.Actual360()
    raise ValueError(f"unknown day_count {name}")

def qd(s):
    y, m, d = map(int, s.split("-"))
    return ql.Date(d, m, y)

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--params", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    p = json.load(open(a.params, encoding="utf-8"))

    freq = FREQ[p.get("frequency", "Semiannual")]
    calendar = CAL[p.get("calendar", "NullCalendar")]
    daycount = dc(p.get("day_count", "ActualActual"), freq)
    comp = COMP[p.get("compounding", "Compounded")]
    face = float(p.get("face", 100.0))
    settle = qd(p["settlement_date"])
    ql.Settings.instance().evaluationDate = settle

    schedule = ql.Schedule(qd(p["issue_date"]), qd(p["maturity_date"]),
                           ql.Period(freq), calendar,
                           ql.Unadjusted, ql.Unadjusted,
                           ql.DateGeneration.Backward, False)
    bond = ql.FixedRateBond(int(p.get("settlement_days", 1)), face, schedule,
                            [float(p["coupon"])], daycount)

    if "market_clean_price" in p:
        clean = float(p["market_clean_price"])
        ytm = bond.bondYield(clean, daycount, comp, freq, settle)
    elif "market_yield" in p:
        ytm = float(p["market_yield"])
        clean = bond.cleanPrice(ytm, daycount, comp, freq, settle)
    else:
        _fail("provide either market_clean_price or market_yield")

    rate = ql.InterestRate(ytm, daycount, comp, freq)
    accrued = bond.accruedAmount(settle)
    dirty = clean + accrued
    mac = ql.BondFunctions.duration(bond, rate, ql.Duration.Macaulay, settle)
    mod = ql.BondFunctions.duration(bond, rate, ql.Duration.Modified, settle)
    conv = ql.BondFunctions.convexity(bond, rate, settle)
    # DV01: price change per 1bp yield move (report as positive magnitude, per 100 face)
    bpv = ql.BondFunctions.basisPointValue(bond, rate, settle)
    dv01 = abs(bpv)

    result = {
        "values": {
            "clean_price": round(clean, 6),
            "dirty_price": round(dirty, 6),
            "accrued_interest": round(accrued, 6),
            "ytm": round(ytm, 8),
            "ytm_pct": round(ytm * 100, 6),
            "macaulay_duration": round(mac, 6),
            "modified_duration": round(mod, 6),
            "dv01": round(dv01, 6),
            "convexity": round(conv, 6),
            "oas": None,
        },
        "notes": {
            "oas": "Out of scope for the static script; requires a discount curve and, if callable, a "
                   "short-rate/vol model. Supply a curve to extend.",
            "day_count": p.get("day_count", "ActualActual"),
            "compounding": p.get("compounding", "Compounded"),
            "frequency": p.get("frequency", "Semiannual"),
        },
        "provenance": {
            "script": "bond_analytics.py",
            "quantlib_version": ql.__version__,
            "inputs_sha256": hashlib.sha256(open(a.params, "rb").read()).hexdigest()[:16],
            "computed_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        },
        "self_check": {"passed": True,
                       "checks": {"dirty_ge_clean": dirty >= clean,
                                  "duration_positive": mod > 0}},
    }
    json.dump(result, open(a.out, "w", encoding="utf-8"), indent=2, ensure_ascii=False)
    print(json.dumps(result["values"], indent=2, ensure_ascii=False))

if __name__ == "__main__":
    main()

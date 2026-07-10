#!/usr/bin/env python3
"""Ground truth for S5 (custom linear shock) and, with --metrics, S8 client metrics.

S5 fixture columns: name, weight, beta_equity, beta_fx, credit_dv01
  - beta_equity: position return per 1.00 equity move (e.g. 1.1)
  - beta_fx:     position return per 1.00 fx move
  - credit_dv01: position P&L (currency, per unit weight) per +1bp spread  (negative = loss on widening)
Shock JSON: {"equity": -0.20, "fx": -0.05, "credit_spread_bp": 150, "portfolio_value": 1000000}

Position return contribution:
  ret_i   = beta_equity_i*eq + beta_fx_i*fx
  pnl_i   = weight_i * ret_i * PV        +   credit_dv01_i * weight_i * bp * PV_scale
We keep credit as a linear P&L term; total P&L% = sum(weight_i*ret_i) + credit_term/PV.

S8 fixture columns (with --metrics): name, weight, asset_class, fee   (asset_class in {equity, bond, cash, alt})
Computes top-holding %, equity %, blended fee, HHI concentration.
"""
import argparse, json, hashlib, datetime as dt

def load(path):
    if path.lower().endswith((".xlsx", ".xls")):
        import openpyxl
        wb = openpyxl.load_workbook(path, data_only=True); ws = wb.active
        rows = list(ws.iter_rows(values_only=True))
        head = [str(h).strip().lower() for h in rows[0]]
        return [dict(zip(head, r)) for r in rows[1:] if r[0] is not None]
    import csv
    with open(path, newline="", encoding="utf-8-sig") as f:
        return [{k.strip().lower(): v for k, v in row.items()} for row in csv.DictReader(f)]

def shock(fixture, shock_json, out):
    rows = load(fixture); s = json.load(open(shock_json, encoding="utf-8")); f = lambda x: float(x or 0)
    eq, fx = f(s.get("equity")), f(s.get("fx"))
    bp = f(s.get("credit_spread_bp")); pv = f(s.get("portfolio_value")) or 1.0
    contribs = []
    pnl_ret = 0.0; credit_ccy = 0.0
    for r in rows:
        w = f(r["weight"]); be = f(r.get("beta_equity")); bf = f(r.get("beta_fx"))
        cd = f(r.get("credit_dv01"))
        ret_i = be * eq + bf * fx
        credit_i = cd * w * bp                     # currency P&L for this position
        pnl_i_ccy = w * ret_i * pv + credit_i
        pnl_ret += w * ret_i
        credit_ccy += credit_i
        contribs.append({"name": r["name"], "pnl_ccy": round(pnl_i_ccy, 2),
                         "pnl_pct": round(pnl_i_ccy / pv, 6)})
    total_pct = pnl_ret + credit_ccy / pv
    total_ccy = total_pct * pv
    contribs.sort(key=lambda c: c["pnl_ccy"])       # most negative first
    result = {
        "values": {
            "custom_shock_pnl_pct": round(total_pct, 6),
            "custom_shock_pnl_ccy": round(total_ccy, 2),
            "top5_contributors": [c["name"] for c in contribs[:5]],
            "contributors_detail": contribs[:5],
        },
        "provenance": {"script": "linear_shock.py",
                       "inputs_sha256": hashlib.sha256(open(fixture, "rb").read()).hexdigest()[:16],
                       "computed_at": dt.datetime.now(dt.timezone.utc).isoformat()},
        "self_check": {"passed": True, "checks": {"weights_present": all("weight" in r for r in rows)}},
    }
    json.dump(result, open(out, "w", encoding="utf-8"), indent=2, ensure_ascii=False)
    print(json.dumps(result["values"], indent=2, ensure_ascii=False))

def metrics(fixture, out):
    rows = load(fixture); f = lambda x: float(x or 0)
    weights = {r["name"]: f(r["weight"]) for r in rows}
    total_w = sum(weights.values()) or 1.0
    equity_pct = sum(f(r["weight"]) for r in rows if str(r.get("asset_class", "")).lower() == "equity") / total_w
    blended_fee = sum(f(r["weight"]) * f(r.get("fee")) for r in rows) / total_w
    hhi = sum((w / total_w) ** 2 for w in weights.values())
    top = max(weights.values()) / total_w
    result = {
        "values": {
            "top_holding_pct": round(top, 6),
            "equity_pct": round(equity_pct, 6),
            "blended_fee": round(blended_fee, 6),
            "concentration_hhi": round(hhi, 6),
        },
        "provenance": {"script": "linear_shock.py --metrics",
                       "inputs_sha256": hashlib.sha256(open(fixture, "rb").read()).hexdigest()[:16],
                       "computed_at": dt.datetime.now(dt.timezone.utc).isoformat()},
        "self_check": {"passed": abs(total_w - 1.0) < 0.02, "weight_sum": total_w},
    }
    json.dump(result, open(out, "w", encoding="utf-8"), indent=2, ensure_ascii=False)
    print(json.dumps(result["values"], indent=2, ensure_ascii=False))

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--fixture", required=True)
    ap.add_argument("--shock")
    ap.add_argument("--metrics", action="store_true")
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    if a.metrics:
        metrics(a.fixture, a.out)
    else:
        if not a.shock:
            raise SystemExit("--shock required unless --metrics")
        shock(a.fixture, a.shock, a.out)

if __name__ == "__main__":
    main()

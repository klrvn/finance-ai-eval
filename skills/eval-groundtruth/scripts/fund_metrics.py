#!/usr/bin/env python3
"""Ground truth for S7: fund risk/return metrics from authoritative NAV series.

Inputs:
  --navs fundA.csv fundB.csv ...   each with columns: date, nav   (fund identified by filename stem = fund code)
  --static static.csv              columns: fund, expense_ratio, aum   (passthrough; not computable)
  --pool candidate_pool.yaml       30-fund candidate pool (YAML); used to emit pool_codes for membership check
  --as-of 2026-07-10               fixed as-of date; NAV series truncated to this date before computation
  --rf 0.0                         annual risk-free for Sharpe (default 0)

Computes per fund (truncated to --as-of): ann_return_3y, ann_return_5y, ann_vol, sharpe, max_drawdown.
Expense ratio and AUM are carried through from --static (a screener cannot compute them).
Emits pool_codes set for the shortlist_from_pool checkpoint.
Requires pandas + numpy. PyYAML needed if --pool is provided.
"""
import argparse, json, os, datetime as dt


def load_pool(path):
    """Load candidate_pool.yaml and return list of fund codes."""
    import yaml
    with open(path, encoding="utf-8") as f:
        data = yaml.safe_load(f)
    return [f["code"] for f in data.get("funds", [])]


def main():
    import numpy as np, pandas as pd
    ap = argparse.ArgumentParser()
    ap.add_argument("--navs", nargs="+", required=True)
    ap.add_argument("--static")
    ap.add_argument("--pool", help="candidate_pool.yaml for membership verification")
    ap.add_argument("--as-of", dest="as_of", default=None, help="as-of date YYYY-MM-DD; NAV truncated before this date")
    ap.add_argument("--rf", type=float, default=0.0)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()

    # --- Load candidate pool ---
    pool_codes = []
    if a.pool:
        pool_codes = load_pool(a.pool)

    # --- Parse as-of date ---
    as_of_ts = None
    if a.as_of:
        as_of_ts = pd.Timestamp(a.as_of)

    # --- Load static data (expense_ratio, aum passthrough) ---
    static = {}
    if a.static:
        for row in __import__("csv").DictReader(open(a.static, encoding="utf-8-sig")):
            static[row["fund"].strip()] = row

    # --- Compute metrics per fund ---
    funds = {}
    for path in a.navs:
        name = os.path.splitext(os.path.basename(path))[0]
        s = pd.read_csv(path, parse_dates=["date"]).set_index("date").sort_index()["nav"]

        # Truncate NAV series to as-of date
        if as_of_ts is not None:
            s = s[s.index <= as_of_ts]

        if len(s) < 2:
            funds[name] = {
                "ann_return_3y": None, "ann_return_5y": None,
                "ann_vol": None, "sharpe": None, "max_drawdown": None,
                "expense_ratio": None, "aum": None,
            }
            continue

        ret = s.pct_change().dropna()
        ann = 252

        def ann_ret(years):
            cutoff = s.index[-1] - pd.DateOffset(years=years)
            w = s.loc[cutoff:]
            if len(w) < 20:
                return None
            r = w.pct_change().dropna()
            return float((w.iloc[-1] / w.iloc[0]) ** (ann / len(r)) - 1)

        vol = float(ret.std() * np.sqrt(ann))
        sharpe = float(((ret.mean() * ann) - a.rf) / (ret.std() * np.sqrt(ann))) if ret.std() else None
        dd = float((s / s.cummax() - 1).min())
        st = static.get(name, {})
        funds[name] = {
            "ann_return_3y": ann_ret(3),
            "ann_return_5y": ann_ret(5),
            "ann_vol": round(vol, 6),
            "sharpe": round(sharpe, 4) if sharpe is not None else None,
            "max_drawdown": round(dd, 6),
            "expense_ratio": float(st["expense_ratio"]) if st.get("expense_ratio") else None,
            "aum": float(st["aum"]) if st.get("aum") else None,
        }

    result = {
        "values": {"funds": funds},
        "pool_codes": pool_codes,
        "as_of_date": a.as_of,
        "provenance": {
            "script": "fund_metrics.py",
            "n_funds": len(funds),
            "n_pool": len(pool_codes),
            "as_of": a.as_of,
            "computed_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        },
        "self_check": {"passed": len(funds) > 0 and (not pool_codes or len(pool_codes) > 0)},
    }
    json.dump(result, open(a.out, "w", encoding="utf-8"), indent=2, ensure_ascii=False)
    print(json.dumps(result["values"], indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()

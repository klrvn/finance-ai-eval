#!/usr/bin/env python3
"""Ground truth for S2: monthly-rebalanced top-quintile 12-1 momentum, long-only, equal weight.

Inputs:
  --prices     prices.csv     Date index (col 'date'), one column per ticker, adjusted close
  --benchmark  benchmark.csv  columns: date, level  (benchmark total-return level)
  --membership membership.csv (optional) columns: date, ticker  (point-in-time constituents)
  --start 2019-01-01 --end 2024-12-31 --commission 0.001 --slippage 0.001

Outputs groundtruth.json with ann_return, ann_vol, sharpe, max_drawdown, ann_excess_return,
avg_monthly_turnover, plus writes nav.csv next to --out.
Requires pandas + numpy. This recipe needs market data; if unavailable, the orchestrator falls back
to NAV-reproduction consistency only.
"""
import argparse, json, os, hashlib, datetime as dt

def main():
    import numpy as np, pandas as pd
    ap = argparse.ArgumentParser()
    ap.add_argument("--prices", required=True)
    ap.add_argument("--benchmark", required=True)
    ap.add_argument("--membership")
    ap.add_argument("--start", default="2019-01-01")
    ap.add_argument("--end", default="2024-12-31")
    ap.add_argument("--commission", type=float, default=0.001)
    ap.add_argument("--slippage", type=float, default=0.001)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()

    px = pd.read_csv(a.prices, parse_dates=["date"]).set_index("date").sort_index()
    px = px.loc[a.start:a.end]
    bench = pd.read_csv(a.benchmark, parse_dates=["date"]).set_index("date").sort_index()["level"]
    bench = bench.loc[a.start:a.end]
    memb = None
    if a.membership:
        m = pd.read_csv(a.membership, parse_dates=["date"])
        memb = {d: set(g["ticker"]) for d, g in m.groupby("date")}

    month_ends = px.resample("ME").last().index
    daily_ret = px.pct_change()
    cost_rate = a.commission + a.slippage

    nav = pd.Series(index=px.index, dtype=float); nav.iloc[0] = 1.0
    prev_w = pd.Series(0.0, index=px.columns)
    turnovers = []
    rebal_dates = [d for d in month_ends if d in px.index]

    # precompute 12-1 momentum at each month end: P[t-1m]/P[t-12m]-1
    def momentum(asof):
        hist = px.loc[:asof]
        if len(hist) < 252:  # ~12 months of trading days
            return None
        p_skip = hist.iloc[-21]      # ~1 month ago
        p_form = hist.iloc[-252]     # ~12 months ago
        mom = p_skip / p_form - 1.0
        return mom.dropna()

    weights_by_date = {}
    for asof in rebal_dates:
        mom = momentum(asof)
        if mom is None or mom.empty:
            continue
        universe = mom.index
        if memb is not None:
            elig = memb.get(asof, set())
            universe = [t for t in universe if t in elig]
            mom = mom.loc[universe]
        if mom.empty:
            continue
        cutoff = mom.quantile(0.8)
        winners = mom[mom >= cutoff].index
        w = pd.Series(0.0, index=px.columns)
        if len(winners):
            w.loc[winners] = 1.0 / len(winners)
        weights_by_date[asof] = w

    # daily NAV walk with monthly rebalancing
    cur_w = pd.Series(0.0, index=px.columns)
    last_nav = 1.0
    nav_vals = []
    for i, d in enumerate(px.index):
        if d in weights_by_date:
            new_w = weights_by_date[d]
            to = (new_w - cur_w).abs().sum()
            turnovers.append(to)
            last_nav *= (1 - cost_rate * to)
            cur_w = new_w
        r = (cur_w * daily_ret.loc[d].fillna(0)).sum() if i > 0 else 0.0
        last_nav *= (1 + r)
        nav_vals.append(last_nav)
    nav = pd.Series(nav_vals, index=px.index)

    strat_ret = nav.pct_change().dropna()
    ann = 252
    ann_return = nav.iloc[-1] ** (ann / len(strat_ret)) - 1
    ann_vol = strat_ret.std() * np.sqrt(ann)
    sharpe = (strat_ret.mean() * ann) / (strat_ret.std() * np.sqrt(ann)) if strat_ret.std() else float("nan")
    dd = nav / nav.cummax() - 1
    max_dd = dd.min()
    bench_ret = (bench.iloc[-1] / bench.iloc[0]) ** (ann / len(strat_ret)) - 1
    ann_excess = ann_return - bench_ret
    avg_turnover = float(np.mean(turnovers)) if turnovers else 0.0

    nav_path = os.path.join(os.path.dirname(a.out) or ".", "nav.csv")
    nav.rename("nav").to_csv(nav_path)

    result = {
        "values": {
            "ann_return": round(float(ann_return), 6),
            "ann_vol": round(float(ann_vol), 6),
            "sharpe": round(float(sharpe), 4),
            "max_drawdown": round(float(max_dd), 6),
            "ann_excess_return": round(float(ann_excess), 6),
            "avg_monthly_turnover": round(avg_turnover, 6),
        },
        "artifacts": {"nav_csv": nav_path},
        "provenance": {"script": "momentum_backtest.py",
                       "params": {"commission": a.commission, "slippage": a.slippage,
                                  "start": a.start, "end": a.end},
                       "prices_sha256": hashlib.sha256(open(a.prices, "rb").read()).hexdigest()[:16],
                       "computed_at": dt.datetime.now(dt.timezone.utc).isoformat()},
        "self_check": {"passed": bool(len(strat_ret) > 200 and nav.iloc[-1] > 0),
                       "n_days": int(len(strat_ret)), "n_rebalances": len(weights_by_date)},
    }
    json.dump(result, open(a.out, "w", encoding="utf-8"), indent=2, ensure_ascii=False)
    print(json.dumps(result["values"], indent=2, ensure_ascii=False))

if __name__ == "__main__":
    main()

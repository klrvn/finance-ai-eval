#!/usr/bin/env python3
"""Shared calculator for single-period Brinson-Fachler sector attribution.

Fixture columns (csv or xlsx): sector, w_p, r_p, w_b, r_b
  w_p/w_b are weights (fractions summing ~1), r_p/r_b are sector total returns (fractions).

Brinson-Fachler decomposition (per sector i, R_b = sum(w_b_i * r_b_i)):
  allocation_i  = (w_p_i - w_b_i) * (r_b_i - R_b)
  selection_i   =  w_b_i          * (r_p_i - r_b_i)
  interaction_i = (w_p_i - w_b_i) * (r_p_i - r_b_i)
  sum over sectors of (alloc + sel + interaction) == R_p - R_b   (identity; self-checked)
"""
import argparse, json, hashlib, datetime as dt

def load(path):
    if path.lower().endswith((".xlsx", ".xls")):
        import openpyxl
        wb = openpyxl.load_workbook(path, data_only=True)
        ws = wb.active
        rows = list(ws.iter_rows(values_only=True))
        head = [str(h).strip().lower() for h in rows[0]]
        return [dict(zip(head, r)) for r in rows[1:] if r[0] is not None]
    else:
        import csv
        with open(path, newline="", encoding="utf-8-sig") as f:
            return [{k.strip().lower(): v for k, v in row.items()}
                    for row in csv.DictReader(f)]

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--fixture", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    rows = load(a.fixture)
    f = lambda x: float(x)

    R_b = sum(f(r["w_b"]) * f(r["r_b"]) for r in rows)
    R_p = sum(f(r["w_p"]) * f(r["r_p"]) for r in rows)

    per_sector, tot_a, tot_s, tot_i = [], 0.0, 0.0, 0.0
    for r in rows:
        wp, rp, wb, rb = f(r["w_p"]), f(r["r_p"]), f(r["w_b"]), f(r["r_b"])
        alloc = (wp - wb) * (rb - R_b)
        sel = wb * (rp - rb)
        inter = (wp - wb) * (rp - rb)
        tot_a += alloc; tot_s += sel; tot_i += inter
        per_sector.append({"sector": r["sector"],
                           "allocation": round(alloc, 8),
                           "selection": round(sel, 8),
                           "interaction": round(inter, 8)})

    active = R_p - R_b
    recon_err = (tot_a + tot_s + tot_i) - active
    # Keyed vectors (keyed by sector) so the grader's `vector` type matches robustly regardless of
    # the order in which a candidate lists sectors.
    alloc_by = {s["sector"]: s["allocation"] for s in per_sector}
    sel_by = {s["sector"]: s["selection"] for s in per_sector}
    inter_by = {s["sector"]: s["interaction"] for s in per_sector}
    result = {
        "values": {
            "per_sector": per_sector,
            "allocation_by_sector": alloc_by,
            "selection_by_sector": sel_by,
            "interaction_by_sector": inter_by,
            "total_allocation": round(tot_a, 8),
            "total_selection": round(tot_s, 8),
            "total_interaction": round(tot_i, 8),
            "total_active_return": round(active, 8),
            "R_p": round(R_p, 8), "R_b": round(R_b, 8),
        },
        "provenance": {
            "script": "brinson.py",
            "inputs_sha256": hashlib.sha256(open(a.fixture, "rb").read()).hexdigest()[:16],
            "computed_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        },
        "self_check": {"passed": abs(recon_err) < 1e-6,
                       "reconciliation_error": recon_err},
    }
    json.dump(result, open(a.out, "w", encoding="utf-8"), indent=2, ensure_ascii=False)
    print(json.dumps(result["values"], indent=2, ensure_ascii=False))
    if not result["self_check"]["passed"]:
        raise SystemExit(f"reconciliation failed: err={recon_err}")

if __name__ == "__main__":
    main()

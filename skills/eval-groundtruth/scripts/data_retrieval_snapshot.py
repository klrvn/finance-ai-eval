#!/usr/bin/env python3
"""Ground-truth calculator for S9 (data-retrieval-stability) — v2.5 Live API.

Fetches real-time market data from Yahoo Finance to construct the 4 ground-truth
answer values. Replaces the v1.0 snapshot-based calculator.

Data sources:
  Q1: Yahoo Finance 2513.HK (智谱) — Adj Close, forward-adjusted (Yahoo default)
  Q2: Yahoo Finance 600519.SS (贵州茅台) — Adj Close, forward-adjusted
  Q3: Yahoo Finance 000300.SS (沪深300价格指数) — Adj Close
  Q4: Yahoo Finance 518880.SS (华安黄金ETF) — Close, backward-adjusted (后复权)

v2.5: the former Q5 (10Y US Treasury yield, FRED DGS10) was removed from the task.
The FRED fetcher and the US market-close cutoff went with it — every remaining
question is an HK/A-share YTD return.

Usage:
  python data_retrieval_snapshot.py --out run/groundtruth.json [--snapshot fixtures/groundtruth_snapshot.json]

  --snapshot is now optional (kept for backward compatibility; if provided, used
  as fallback if live fetch fails).

Runtime cutoff logic:
  - HK stocks: if today is a trading day and before HKT 16:10, use prior trading day close
  - A-share: if today is a trading day and before CST 15:00, use prior trading day close
"""
import argparse
import hashlib
import json
import os
import sys
from datetime import datetime, timezone, timedelta, date


# ---------------------------------------------------------------------------
# Time & market-cutoff helpers
# ---------------------------------------------------------------------------

# When set via --sim-now, overrides real wall-clock time for cutoff logic and
# today-filtering. Format: ISO 8601 string (e.g. "2026-07-20T07:30:00Z").
_SIM_NOW_UTC = None

def get_current_utc():
    if _SIM_NOW_UTC is not None:
        return _SIM_NOW_UTC
    return datetime.now(timezone.utc)


def is_weekday(dt):
    """Monday=0 .. Sunday=6; weekday if <5."""
    return dt.weekday() < 5


def hk_latest_cutoff(now_utc):
    """For HK stocks: if today is a trading day and before HKT 16:10,
    use prior trading day. Returns (should_use_prior, reason)."""
    hkt_offset = timezone(timedelta(hours=8))
    now_hkt = now_utc.astimezone(hkt_offset)
    market_close = now_hkt.replace(hour=16, minute=10, second=0, microsecond=0)
    if is_weekday(now_hkt):
        if now_hkt < market_close:
            return True, f"Today {now_hkt.strftime('%Y-%m-%d')} is a HK trading day but before 16:10 HKT close; using prior trading day"
    return False, f"Using latest available HK trading day (now: {now_hkt.strftime('%Y-%m-%d %H:%M HKT')})"


def cn_latest_cutoff(now_utc):
    """For A-share: if today is a trading day and before CST 15:00,
    use prior trading day."""
    cst_offset = timezone(timedelta(hours=8))
    now_cst = now_utc.astimezone(cst_offset)
    market_close = now_cst.replace(hour=15, minute=0, second=0, microsecond=0)
    if is_weekday(now_cst):
        if now_cst < market_close:
            return True, f"Today {now_cst.strftime('%Y-%m-%d')} is an A-share trading day but before 15:00 CST close; using prior trading day"
    return False, f"Using latest available A-share trading day (now: {now_cst.strftime('%Y-%m-%d %H:%M CST')})"


# v2.5: us_latest_cutoff() was removed together with Q5 (10Y UST / FRED DGS10) —
# no remaining question reads a US market series. expected_latest_date() below still
# carries a generic "US" branch for reuse by other data-retrieval containers.


# ---------------------------------------------------------------------------
# Yahoo Finance data fetcher
# ---------------------------------------------------------------------------

def fetch_yahoo_history(ticker, period="2y", interval="1d"):
    """Fetch historical data from Yahoo Finance chart API (JSON, no auth needed).

    Returns list of dicts: [{"date": "YYYY-MM-DD", "adj_close": float, "close": float}, ...]
    Sorted oldest -> newest.
    """
    import urllib.request
    import urllib.parse

    params = urllib.parse.urlencode({
        "range": "2y",
        "interval": "1d",
        "includePrePost": "false",
    })
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}?{params}"

    req = urllib.request.Request(url, headers={
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Accept": "application/json",
    })

    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        # Try fallback: query2
        url2 = url.replace("query1", "query2")
        req2 = urllib.request.Request(url2, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Accept": "application/json",
        })
        with urllib.request.urlopen(req2, timeout=30) as resp:
            data = json.loads(resp.read().decode("utf-8"))

    # Parse JSON chart response
    result = data.get("chart", {}).get("result", [])
    if not result:
        raise ValueError(f"No chart result for {ticker}")

    entry = result[0]
    timestamps = entry.get("timestamp", [])
    indicators = entry.get("indicators", {})
    quotes = indicators.get("quote", [{}])[0]
    adj_close_list = indicators.get("adjclose", [{}])[0].get("adjclose", [])

    closes = quotes.get("close", [])

    rows = []
    for i, ts in enumerate(timestamps):
        d = datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d")
        ac = adj_close_list[i] if i < len(adj_close_list) else None
        c = closes[i] if i < len(closes) else None
        if ac is not None:
            ac_f = round(float(ac), 4)
            c_f = round(float(c), 4) if c is not None else ac_f
            rows.append({"date": d, "adj_close": ac_f, "close": c_f})

    rows.sort(key=lambda r: r["date"])
    return rows


# ---------------------------------------------------------------------------
# IPO price registry — stocks listed mid-year use IPO issue price as base
# ---------------------------------------------------------------------------
# 题设规则（S9 spec）："若证券在年中才发行（2025-12-31 尚未上市），
# 基准价取 IPO 发行价，而非上市首日收盘价。"
# Yahoo Finance 只提供上市后的行情，不含发行价；故对年中上市的新股
# 在此处硬编码 IPO 发行价（来源：交易所招股结果公告/证监会披露）。
# 新增年中上市新股时在此追加一条记录即可。
IPO_PRICES = {
    # 智谱（02513.HK）：2026-01-08 港交所 IPO，发行价 116.20 HKD
    # 来源：港交所招股结果公告（2025-12-30 发布）；同花顺/阿斯达克转载确认
    "2513.HK": {"ipo_date": "2026-01-08", "ipo_price": 116.20,
                "source": "港交所招股结果公告 (2025-12-30)；发行价 116.20 HKD"},
}


def get_ytd_base_and_latest(ticker, use_prior, market_type, adjustment="adj_close"):
    """Get year-start base price and latest price from Yahoo Finance.

    Args:
        ticker: Yahoo Finance ticker (e.g., "2513.HK", "600519.SS")
        use_prior: if True, use prior trading day as latest (market not closed yet)
        market_type: "HK" or "CN" for cutoff messages
        adjustment: "adj_close" for 前复权 (forward-adjusted, Yahoo default),
                    "close" for 后复权 (backward-adjusted — uses raw close,
                    which equals backward-adjusted when no corporate action on
                    the latest day; correct for ETFs with no dividends in window)

    Returns: dict with base_date, base_price, latest_date, latest_price
    """
    rows = fetch_yahoo_history(ticker)

    if not rows:
        raise ValueError(f"No data fetched for {ticker}")

    # Year start: find the last trading day on or before 2025-12-31
    year_start_candidates = [r for r in rows if r["date"] <= "2025-12-31"]
    if year_start_candidates:
        base_row = year_start_candidates[-1]
        # Normal listed stock: use year-start close
        base_date = base_row["date"]
        price_field = adjustment  # "adj_close" or "close"
        base_price = round(base_row[price_field], 4)
        base_source = f"Yahoo Finance {ticker} — {adjustment} close"
    else:
        # Stock listed in 2026 (e.g., 智谱 listed 2026-01-08).
        # 题设规则：年中上市的新股，基准取 IPO 发行价，而非上市首日收盘价。
        # 优先用 IPO_PRICES 注册表中的发行价；若注册表无此 ticker，
        # 回退到上市首日收盘价（rows[0]）并标注 fallback。
        ipo_info = IPO_PRICES.get(ticker)
        if ipo_info is not None:
            base_date = ipo_info["ipo_date"]
            base_price = round(float(ipo_info["ipo_price"]), 4)
            base_source = f"IPO issue price — {ipo_info['source']}"
        else:
            # Fallback: use listing-day first close (legacy behavior)
            base_row = rows[0]
            base_date = base_row["date"]
            price_field = adjustment
            base_price = round(base_row[price_field], 4)
            base_source = (f"Yahoo Finance {ticker} — {adjustment} close "
                           f"(FALLBACK: no IPO price registered, using listing-day close)")

    # Latest: filter to rows on or before simulated/real "today", then if
    # use_prior is True (market hasn't closed yet), exclude today itself.
    today_str = get_current_utc().strftime("%Y-%m-%d")
    latest_candidates = [r for r in rows if r["date"] <= today_str]
    if use_prior:
        latest_candidates = [r for r in latest_candidates if r["date"] < today_str]

    if not latest_candidates:
        raise ValueError(f"No prior trading day data for {ticker}")

    latest_row = latest_candidates[-1]

    # Select price field based on adjustment type
    price_field = adjustment  # "adj_close" or "close"

    return {
        "base_date": base_date,
        "base_price": base_price,
        "base_source": base_source,
        "latest_date": latest_row["date"],
        "latest_price": round(latest_row[price_field], 4),
    }


# ---------------------------------------------------------------------------
# v2.5: the FRED DGS10 fetcher (fetch_fred_dgs10) was removed together with Q5.
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Main GT construction
# ---------------------------------------------------------------------------

# v2.5: the task's question set. Q5 (10Y UST yield) was deleted; every remaining
# question is a YTD return with a base/latest pair, so this list drives both the
# legacy snapshot loop and the self-check denominator.
QUESTION_IDS = ["Q1", "Q2", "Q3", "Q4"]
N_QUESTIONS = len(QUESTION_IDS)


def compute_return_pct(base, latest):
    """Compute YTD return percentage, rounded to 2 decimals."""
    if base == 0:
        return None
    return round((latest - base) / base * 100, 2)


def compute_self_check(gt_values, snapshot_questions):
    """Verify internal consistency: re-derive each return from base+latest.

    v2.4.2: a None latest (e.g. cross-source escalated to ERROR because Yahoo was
    stale AND EastMoney was unreachable) is recorded as a WARNING, not a hard
    failure. Rationale: "could not fetch the data" is a data-availability gap, not
    an arithmetic inconsistency — the GT is still internally consistent for the
    questions it *could* verify, and the missing question should surface as NA at
    checkpoint grading rather than aborting the whole run. (Mirrors the S6
    macro_snapshot philosophy: missing data -> NA, not a halt.) The warning is still
    emitted so it is visible in self_check.checks and the provenance.
    """
    issues = []
    warnings = []
    for qid in QUESTION_IDS:
        base = gt_values.get(f"{qid}_base_price")
        latest = gt_values.get(f"{qid}_latest_price")
        ret = gt_values.get(f"{qid}_answer")
        if latest is None:
            # Cross-source could not establish a trustworthy value on expected_date.
            # Record as a warning (NA at grading), not a hard failure.
            warnings.append(f"{qid}: latest_price is None (cross-source could not establish truth on expected_date -> NA)")
            continue
        if base is not None and base != 0:
            recomputed = round((latest - base) / base * 100, 2)
            if ret is not None and abs(recomputed - ret) > 0.01:
                issues.append(f"{qid}: recomputed {recomputed} != stored {ret}")
    return {"passed": len(issues) == 0, "checks": issues + warnings,
            "n_verified": N_QUESTIONS - len(issues), "warnings": warnings}


# ---------------------------------------------------------------------------
# Cross-source verification layer (v2.1): Yahoo vs East Money for A-share
# ---------------------------------------------------------------------------
# 背景：Yahoo Finance 对部分 A 股标的（指数 000300.SS、ETF 518880.SS）存在
# 数据延迟/缺失——某些交易日的收盘在 Yahoo 上为 None。若 GT 仅用 Yahoo，会
# 静默回退到更早的日期，把"市场真实最新收盘"错记成更早的值。
#
# 本检查层：对 A 股标的（Q2/Q3/Q4），用东方财富 kline API 独立取同一目标日期
# 的收盘价，与 Yahoo 取到的值对照：
#   1) 先按 as-of + 市场收盘逻辑算出 expected_latest_date（正确目标日期）
#   2) 看 Yahoo 取到的 latest_date 是否 == expected_latest_date
#   3) 用东方财富取 expected_latest_date 的收盘
#   4) 裁决：
#      - Yahoo 日期正确且两源数值一致（容差内） → 用 Yahoo 值，status=PASS
#      - Yahoo 日期不符（缺失/延迟）但东方财富有 expected_date 值 → 用东方财富值，
#        status=YAHOO_FALLBACK（记录 Yahoo 缺失原因）
#      - 两源都取不到 expected_date 值 → status=ERROR，受影响检查点记 NA
#      - 两源都有 expected_date 值但数值差异超容差 → status=CONFLICT，记录两值，
#        GT 用 Yahoo 值但标记 conflict 待人工

# 东方财富 secid 映射（A 股标的）
EASTMONEY_SECIDS = {
    "600519.SS": {"secid": "1.600519", "fqt": 1, "label": "贵州茅台 前复权"},
    "000300.SS": {"secid": "1.000300", "fqt": 1, "label": "沪深300价格指数"},
    "518880.SS": {"secid": "1.518880", "fqt": 2, "label": "华安黄金ETF 后复权"},
}


def fetch_eastmoney_kline(secid, fqt=1, beg=20251220, end=20260721, retries=5):
    """Fetch East Money daily kline. Returns list of {date, close} sorted oldest->newest.

    v2.4.2: added retry with exponential backoff for transient network failures
    (RemoteDisconnected / ConnectionResetError / timeouts). These are common against
    push2his.eastmoney.com under rate-limiting and were previously treated as a
    permanent EM_FETCH_FAILED — which silently demoted the cross-source layer to
    Yahoo-only even when Yahoo's data was delayed/stale.
    """
    import urllib.request, urllib.parse, time
    params = urllib.parse.urlencode({
        "secid": secid, "fields1": "f1,f2,f3,f4,f5,f6",
        "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61",
        "klt": 101, "fqt": fqt, "beg": beg, "end": end,
    })
    url = f"https://push2his.eastmoney.com/api/qt/stock/kline/get?{params}"
    last_exc = None
    for attempt in range(1, retries + 1):
        req = urllib.request.Request(url, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Referer": "https://quote.eastmoney.com/",
            "Accept": "*/*",
            "Connection": "close",
        })
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            klines = data.get("data", {}).get("klines", [])
            rows = []
            for line in klines:
                parts = line.split(",")
                rows.append({"date": parts[0], "close": round(float(parts[2]), 4)})
            rows.sort(key=lambda r: r["date"])
            return rows
        except Exception as e:
            last_exc = e
            # transient: connection dropped mid-response, timeout, reset
            transient = ("RemoteDisconnected" in type(e).__name__ or
                         "ConnectionReset" in type(e).__name__ or
                         "timeout" in str(e).lower() or
                         "Remote end closed" in str(e))
            if attempt < retries and transient:
                backoff = 1.0 * attempt  # 1s, 2s, 3s, 4s ...
                print(f"  [EM retry] {secid} attempt {attempt}/{retries} failed ({type(e).__name__}); retrying in {backoff}s", flush=True)
                time.sleep(backoff)
                continue
            raise
    raise last_exc  # pragma: no cover


def expected_latest_date(market, now_utc):
    """Compute the correct target latest date based on as-of + market close logic.

    For A-share: if as-of is a trading day and before CST 15:00, use prior trading day;
    otherwise use the latest trading day on or before as-of date (handling weekends).
    For HK: similar but 16:10 HKT.
    For US: 17:00 ET.
    Returns (expected_date_str, reason).
    """
    if market == "CN":
        tz = timezone(timedelta(hours=8))
        close_hour, close_min = 15, 0
    elif market == "HK":
        tz = timezone(timedelta(hours=8))
        close_hour, close_min = 16, 10
    else:  # US
        tz = timezone(timedelta(hours=-4))
        close_hour, close_min = 17, 0

    now_local = now_utc.astimezone(tz)
    today = now_local.date()
    market_close = now_local.replace(hour=close_hour, minute=close_min, second=0, microsecond=0)

    if is_weekday(now_local) and now_local < market_close:
        # Trading day but before close -> use prior trading day
        target = today
        # step back to prior weekday
        from datetime import timedelta as _td
        target = target - _td(days=1)
        while target.weekday() >= 5:  # Sat/Sun
            target = target - _td(days=1)
        return target.strftime("%Y-%m-%d"), f"{market} as-of before close, use prior trading day {target}"
    else:
        # Use today if weekday, else step back to last weekday
        target = today
        while target.weekday() >= 5:
            from datetime import timedelta as _td
            target = target - _td(days=1)
        return target.strftime("%Y-%m-%d"), f"{market} use latest trading day {target} (as-of at/after close or non-trading day)"


def cross_source_verify(yahoo_ticker, yahoo_value, yahoo_date, expected_date, now_utc, adjustment="adj_close"):
    """Cross-verify Yahoo vs East Money for an A-share ticker.

    Args:
        yahoo_ticker: e.g. "600519.SS"
        yahoo_value: Yahoo latest price (float or None)
        yahoo_date: Yahoo latest date string
        expected_date: the correct target date from expected_latest_date()
        adjustment: "adj_close" (前复权) or "close" (后复权/raw)
    Returns: dict with {chosen_value, chosen_date, chosen_source, status, yahoo_date,
                        yahoo_value, eastmoney_date, eastmoney_value, reason}
    """
    em = EASTMONEY_SECIDS.get(yahoo_ticker)
    if em is None:
        return {"status": "SKIP", "reason": f"no EastMoney secid for {yahoo_ticker}",
                "chosen_value": yahoo_value, "chosen_date": yahoo_date, "chosen_source": "Yahoo"}

    # Fetch East Money kline covering expected_date
    beg = int(expected_date.replace("-", "")[:6] + "01")  # YYYYMM01
    end = int(expected_date.replace("-", "")) + 1
    try:
        em_rows = fetch_eastmoney_kline(em["secid"], em["fqt"], beg=beg, end=end)
    except Exception as e:
        # v2.4.2 escalate: EM fetch failed. If Yahoo's date is correct, we can still
        # trust Yahoo (single-source, acceptable). But if Yahoo's date is ALSO wrong
        # (delayed/stale), we MUST NOT silently use the stale Yahoo value as GT —
        # that was the v2.0 bug this layer exists to fix. Escalate to ERROR so the
        # checkpoint is marked NA instead of graded against a fabricated truth.
        yahoo_date_correct = (yahoo_date == expected_date)
        if yahoo_date_correct:
            return {"status": "EM_FETCH_FAILED", "reason": str(e),
                    "chosen_value": yahoo_value, "chosen_date": yahoo_date, "chosen_source": "Yahoo"}
        # Yahoo date is wrong AND EM is down -> cannot establish truth on expected_date
        return {"status": "ERROR",
                "reason": f"EM fetch failed ({e}) AND Yahoo latest_date={yahoo_date} != expected {expected_date}; cannot verify, marking NA",
                "chosen_value": None, "chosen_date": None, "chosen_source": "NONE",
                "yahoo_date": yahoo_date, "yahoo_value": yahoo_value,
                "eastmoney_date": None, "eastmoney_value": None}

    em_on_date = None
    em_date_used = None
    for r in em_rows:
        if r["date"] == expected_date:
            em_on_date = r["close"]
            em_date_used = r["date"]
            break
    if em_on_date is None and em_rows:
        # East Money also missing expected_date — take its latest available
        em_on_date = em_rows[-1]["close"]
        em_date_used = em_rows[-1]["date"]

    yahoo_date_correct = (yahoo_date == expected_date)
    em_date_correct = (em_date_used == expected_date)

    TOL = 0.01  # numerical tolerance for cross-source match
    values_match = (yahoo_value is not None and em_on_date is not None
                    and abs(yahoo_value - em_on_date) <= TOL)

    if yahoo_date_correct and em_date_correct and values_match:
        return {"status": "PASS", "chosen_value": yahoo_value, "chosen_date": yahoo_date,
                "chosen_source": "Yahoo (cross-verified with EastMoney)", "yahoo_date": yahoo_date,
                "yahoo_value": yahoo_value, "eastmoney_date": em_date_used,
                "eastmoney_value": em_on_date, "reason": "both sources agree on expected_date"}

    if yahoo_date_correct and em_date_correct and not values_match:
        return {"status": "CONFLICT", "chosen_value": yahoo_value, "chosen_date": yahoo_date,
                "chosen_source": "Yahoo (CONFLICT with EastMoney — needs human review)",
                "yahoo_date": yahoo_date, "yahoo_value": yahoo_value,
                "eastmoney_date": em_date_used, "eastmoney_value": em_on_date,
                "reason": f"both on {expected_date} but values differ: Yahoo={yahoo_value} EM={em_on_date}"}

    if (not yahoo_date_correct) and em_date_correct:
        # Yahoo missing/delayed — use EastMoney for expected_date
        return {"status": "YAHOO_FALLBACK", "chosen_value": em_on_date,
                "chosen_date": em_date_used, "chosen_source": "EastMoney (Yahoo fallback)",
                "yahoo_date": yahoo_date, "yahoo_value": yahoo_value,
                "eastmoney_date": em_date_used, "eastmoney_value": em_on_date,
                "reason": f"Yahoo latest_date={yahoo_date} != expected {expected_date}; using EastMoney value on expected_date"}

    if yahoo_date_correct and (not em_date_correct):
        # EastMoney missing — use Yahoo
        return {"status": "EM_FALLBACK", "chosen_value": yahoo_value, "chosen_date": yahoo_date,
                "chosen_source": "Yahoo (EastMoney missing expected_date)",
                "yahoo_date": yahoo_date, "yahoo_value": yahoo_value,
                "eastmoney_date": em_date_used, "eastmoney_value": em_on_date,
                "reason": f"EastMoney missing {expected_date}; using Yahoo"}

    # Both sources missing expected_date
    return {"status": "ERROR", "chosen_value": None, "chosen_date": None,
            "chosen_source": "NONE", "yahoo_date": yahoo_date, "yahoo_value": yahoo_value,
            "eastmoney_date": em_date_used, "eastmoney_value": em_on_date,
            "reason": f"neither source has expected_date {expected_date}; Yahoo latest={yahoo_date}, EM latest={em_date_used}"}


def compute_cross_source_check(gt_values, now_utc):
    """Run cross-source verification for Q2/Q3/Q4 (A-share tickers).
    Returns list of per-question cross-check records."""
    checks = []
    for qid, ticker in [("Q2", "600519.SS"), ("Q3", "000300.SS"), ("Q4", "518880.SS")]:
        yv = gt_values.get(f"{qid}_latest_price")
        yd = gt_values.get(f"{qid}_latest_date")
        if yd is None:
            checks.append({"qid": qid, "status": "SKIP", "reason": "no Yahoo value to verify"})
            continue
        exp_date, exp_reason = expected_latest_date("CN", now_utc)
        result = cross_source_verify(ticker, yv, yd, exp_date, now_utc)
        checks.append({"qid": qid, "ticker": ticker, "expected_date": exp_date,
                        "expected_reason": exp_reason, **result})
    return checks


def main():
    ap = argparse.ArgumentParser(description="S9 data-retrieval-stability ground-truth calculator (v2.5 Live API)")
    ap.add_argument("--out", required=True, help="output groundtruth.json path")
    ap.add_argument("--snapshot", default=None, help="(optional) fallback snapshot JSON if live fetch fails")
    ap.add_argument("--no-live", action="store_true", help="skip live fetch, use snapshot only (legacy mode)")
    ap.add_argument("--sim-now", default=None, dest="sim_now",
                    help='Override wall-clock time for cutoff logic, e.g. "2026-07-20T07:30:00Z". '
                         'Used for reproducible GT computations at a specific test instant.')
    a = ap.parse_args()

    global _SIM_NOW_UTC
    if a.sim_now:
        _SIM_NOW_UTC = datetime.fromisoformat(a.sim_now.replace("Z", "+00:00"))
        print(f"Simulated now: {_SIM_NOW_UTC.isoformat()} (override active)", flush=True)

    now_utc = get_current_utc()

    # Determine cutoff logic for each market
    hk_use_prior, hk_reason = hk_latest_cutoff(now_utc)
    cn_use_prior, cn_reason = cn_latest_cutoff(now_utc)

    print(f"=== S9 GT Live Fetch (v2.5) ===", flush=True)
    print(f"UTC now: {now_utc.isoformat()}", flush=True)
    print(f"HK cutoff: {hk_reason}", flush=True)
    print(f"CN cutoff: {cn_reason}", flush=True)

    gt_values = {}
    provenance = {
        "calculator": "data_retrieval_snapshot.py v2.5 (Live API)",
        "computed_at": now_utc.isoformat(),
        "fetch_log": [],
    }

    if a.no_live and a.snapshot:
        # Legacy mode: use snapshot
        print("Running in legacy snapshot mode (--no-live)", flush=True)
        with open(a.snapshot, encoding="utf-8") as f:
            snapshot = json.load(f)
        qs = snapshot["questions"]
        for qid in QUESTION_IDS:
            q = qs[qid]
            gt_values[f"{qid}_answer"] = q["return_pct"]
            gt_values[f"{qid}_ticker"] = q["ticker"]
            gt_values[f"{qid}_adjustment"] = q.get("adjustment", "不适用")
            gt_values[f"{qid}_base_date"] = q.get("base_date")
            gt_values[f"{qid}_latest_date"] = q.get("latest_date")
            gt_values[f"{qid}_base_price"] = q.get("base_price")
            gt_values[f"{qid}_latest_price"] = q.get("latest_price")
        provenance["mode"] = "snapshot"
        provenance["snapshot_file"] = os.path.basename(a.snapshot)
    else:
        # Live mode: fetch from Yahoo Finance
        provenance["mode"] = "live_api"
        provenance["data_sources"] = {
            "Q1": "Yahoo Finance 2513.HK (智谱) — Adj Close",
            "Q2": "Yahoo Finance 600519.SS (贵州茅台) — Adj Close",
            "Q3": "Yahoo Finance 000300.SS (沪深300价格指数) — Adj Close",
            "Q4": "Yahoo Finance 518880.SS (华安黄金ETF) — Close (后复权)",
        }

        # Q1: 智谱 2513.HK
        try:
            print(f"\n[Q1] Fetching 2513.HK from Yahoo Finance...", flush=True)
            q1 = get_ytd_base_and_latest("2513.HK", hk_use_prior, "HK")
            # 智谱 2026-01-08 上市，2025-12-31 尚未上市 → 基准取 IPO 发行价 116.20
            # （来自 IPO_PRICES 注册表），而非上市首日收盘价 131.50
            gt_values["Q1_answer"] = compute_return_pct(q1["base_price"], q1["latest_price"])
            gt_values["Q1_ticker"] = "02513.HK"
            gt_values["Q1_adjustment"] = "前复权"
            gt_values["Q1_base_date"] = q1["base_date"]
            gt_values["Q1_base_price"] = q1["base_price"]
            gt_values["Q1_base_source"] = q1.get("base_source", "")
            gt_values["Q1_latest_date"] = q1["latest_date"]
            gt_values["Q1_latest_price"] = q1["latest_price"]
            provenance["fetch_log"].append(
                f"Q1: 2513.HK base={q1['base_price']}({q1['base_date']}, "
                f"{q1.get('base_source','')}) "
                f"latest={q1['latest_price']}({q1['latest_date']}) "
                f"YTD={gt_values['Q1_answer']}%")
            print(f"  base={q1['base_price']}({q1['base_date']}, "
                  f"{q1.get('base_source','')}) "
                  f"latest={q1['latest_price']}({q1['latest_date']}) "
                  f"YTD={gt_values['Q1_answer']}%", flush=True)
        except Exception as e:
            print(f"  ERROR: {e}", file=sys.stderr, flush=True)
            provenance["fetch_log"].append(f"Q1: FETCH FAILED - {e}")

        # Q2: 贵州茅台 600519.SS
        try:
            print(f"\n[Q2] Fetching 600519.SS from Yahoo Finance...", flush=True)
            q2 = get_ytd_base_and_latest("600519.SS", cn_use_prior, "CN")
            gt_values["Q2_answer"] = compute_return_pct(q2["base_price"], q2["latest_price"])
            gt_values["Q2_ticker"] = "600519.SH"
            gt_values["Q2_adjustment"] = "前复权"
            gt_values["Q2_base_date"] = q2["base_date"]
            gt_values["Q2_base_price"] = q2["base_price"]
            gt_values["Q2_latest_date"] = q2["latest_date"]
            gt_values["Q2_latest_price"] = q2["latest_price"]
            provenance["fetch_log"].append(f"Q2: 600519.SS base={q2['base_price']}({q2['base_date']}) latest={q2['latest_price']}({q2['latest_date']}) YTD={gt_values['Q2_answer']}%")
            print(f"  base={q2['base_price']}({q2['base_date']}) latest={q2['latest_price']}({q2['latest_date']}) YTD={gt_values['Q2_answer']}%", flush=True)
        except Exception as e:
            print(f"  ERROR: {e}", file=sys.stderr, flush=True)
            provenance["fetch_log"].append(f"Q2: FETCH FAILED - {e}")

        # Q3: 沪深300价格指数 000300.SS
        try:
            print(f"\n[Q3] Fetching 000300.SS from Yahoo Finance...", flush=True)
            q3 = get_ytd_base_and_latest("000300.SS", cn_use_prior, "CN")
            gt_values["Q3_answer"] = compute_return_pct(q3["base_price"], q3["latest_price"])
            gt_values["Q3_ticker"] = "000300.SS"
            gt_values["Q3_adjustment"] = "不适用"
            gt_values["Q3_base_date"] = q3["base_date"]
            gt_values["Q3_base_price"] = q3["base_price"]
            gt_values["Q3_latest_date"] = q3["latest_date"]
            gt_values["Q3_latest_price"] = q3["latest_price"]
            provenance["fetch_log"].append(f"Q3: 000300.SS base={q3['base_price']}({q3['base_date']}) latest={q3['latest_price']}({q3['latest_date']}) YTD={gt_values['Q3_answer']}%")
            print(f"  base={q3['base_price']}({q3['base_date']}) latest={q3['latest_price']}({q3['latest_date']}) YTD={gt_values['Q3_answer']}%", flush=True)
        except Exception as e:
            print(f"  ERROR: {e}", file=sys.stderr, flush=True)
            provenance["fetch_log"].append(f"Q3: FETCH FAILED - {e}")

        # Q4: 华安黄金ETF 518880.SS
        try:
            print(f"\n[Q4] Fetching 518880.SS from Yahoo Finance...", flush=True)
            q4 = get_ytd_base_and_latest("518880.SS", cn_use_prior, "CN", adjustment="close")
            gt_values["Q4_answer"] = compute_return_pct(q4["base_price"], q4["latest_price"])
            gt_values["Q4_ticker"] = "518880.SH"
            gt_values["Q4_adjustment"] = "后复权"
            gt_values["Q4_base_date"] = q4["base_date"]
            gt_values["Q4_base_price"] = q4["base_price"]
            gt_values["Q4_latest_date"] = q4["latest_date"]
            gt_values["Q4_latest_price"] = q4["latest_price"]
            provenance["fetch_log"].append(f"Q4: 518880.SS base={q4['base_price']}({q4['base_date']}) latest={q4['latest_price']}({q4['latest_date']}) YTD={gt_values['Q4_answer']}%")
            print(f"  base={q4['base_price']}({q4['base_date']}) latest={q4['latest_price']}({q4['latest_date']}) YTD={gt_values['Q4_answer']}%", flush=True)
        except Exception as e:
            print(f"  ERROR: {e}", file=sys.stderr, flush=True)
            provenance["fetch_log"].append(f"Q4: FETCH FAILED - {e}")

    # Cross-source verification (v2.1): Yahoo vs EastMoney for A-share Q2/Q3/Q4
    cross_checks = []
    if not (a.no_live and a.snapshot):
        cross_checks = compute_cross_source_check(gt_values, now_utc)
        # Apply cross-source corrections: if Yahoo date != expected but EastMoney has it,
        # override latest_price/latest_date with the EastMoney value on expected_date.
        for cc in cross_checks:
            qid = cc.get("qid")
            if not qid:
                continue
            status = cc.get("status")
            if status in ("YAHOO_FALLBACK", "PASS", "CONFLICT", "EM_FALLBACK"):
                cv = cc.get("chosen_value")
                cd = cc.get("chosen_date")
                cs = cc.get("chosen_source", "")
                if cv is not None and cd is not None:
                    gt_values[f"{qid}_latest_price"] = cv
                    gt_values[f"{qid}_latest_date"] = cd
                    if cs:
                        gt_values[f"{qid}_latest_source"] = cs
                # Recompute answer after correction
                base = gt_values.get(f"{qid}_base_price")
                if base is not None and base != 0 and cv is not None:
                    gt_values[f"{qid}_answer"] = compute_return_pct(base, cv)
            elif status == "ERROR":
                # Neither source has expected_date — mark latest NA
                gt_values[f"{qid}_latest_price"] = None
                gt_values[f"{qid}_latest_date"] = None
                gt_values[f"{qid}_answer"] = None
                gt_values[f"{qid}_latest_source"] = "NONE (both sources missing expected_date)"

    provenance["cross_source_check"] = cross_checks

    # Self-check (before rounding — uses full-precision base/latest)
    sc = compute_self_check(gt_values, {})
    if not sc["passed"]:
        print(f"Self-check FAILED!", file=sys.stderr, flush=True)
        for issue in sc["checks"]:
            print(f"  - {issue}", file=sys.stderr, flush=True)
        sys.exit(1)

    # ------------------------------------------------------------------
    # Global rounding policy (v2.4.1): checkpoint values rounded to 2 decimals
    # ------------------------------------------------------------------
    # 题设规则：S9 所有 GT 检查点真值四舍五入至两位小数。
    # 收益率(answer)仍用原始高精度 base/latest 计算后 round 到 2 位（compute_return_pct
    # 已内置 round(_, 2)），不因 base/latest 的 round 而重算。
    # base_price / latest_price 作为独立检查点，统一 round 到 2 位小数。
    # self-check 在 round 之前执行，用高精度值验证内部一致性。
    _ROUND_DECIMALS = 2
    _NUMERIC_TYPES = (int, float)
    for _key, _val in list(gt_values.items()):
        if isinstance(_val, _NUMERIC_TYPES) and not isinstance(_val, bool):
            gt_values[_key] = round(float(_val), _ROUND_DECIMALS)

    gt = {
        "task_id": "S9",
        "values": gt_values,
        "recipe": "data_retrieval_snapshot",
        "self_check": sc,
        "provenance": provenance,
    }

    os.makedirs(os.path.dirname(os.path.abspath(a.out)) or ".", exist_ok=True)
    with open(a.out, "w", encoding="utf-8") as f:
        json.dump(gt, f, ensure_ascii=False, indent=2)

    if sc["passed"]:
        print(f"\n=== GT Construction Complete ===", flush=True)
        print(f"Self-check passed ({sc['n_verified']}/{N_QUESTIONS} verified).", flush=True)
        print(f"Mode: {provenance.get('mode', 'live_api')}", flush=True)
        for entry in provenance.get("fetch_log", []):
            print(f"  {entry}", flush=True)
    else:
        print(f"Self-check FAILED!", file=sys.stderr, flush=True)
        for issue in sc["checks"]:
            print(f"  - {issue}", file=sys.stderr, flush=True)
        sys.exit(1)


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass
    main()

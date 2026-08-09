#!/usr/bin/env python3
"""
Macro Economic Dashboard builder.

Fetches 18 key macro indicators from free, no-API-key sources and writes a
self-contained `dashboard.html` you can open in any browser.

Sources:
  * FRED  (fredgraph.csv, keyless)  -> macro series
  * Yahoo Finance (chart API)       -> DXY, S&P 500, Gold, Bitcoin
  * multpl.com                      -> S&P 500 P/E ratio
  * computed                        -> Buffett Indicator (Wilshire 5000 / GDP)

Re-run any time to refresh:  python build_dashboard.py
"""

import json
import re
import sys
import time
import urllib.request
from datetime import datetime, timezone
from html import unescape

HERE = __import__("os").path.dirname(__import__("os").path.abspath(__file__))
OUT = __import__("os").path.join(HERE, "dashboard.html")

UA = {"User-Agent": "Mozilla/5.0 (compatible; MacroDashboard/1.0)"}


def _get(url, headers=None, timeout=45, retries=3):
    return _get_bytes(url, headers, timeout, retries).decode("utf-8", "replace")


def _get_bytes(url, headers=None, timeout=45, retries=3):
    last = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers=headers or UA)
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read()
        except Exception as e:  # noqa: BLE001
            last = e
            time.sleep(1.5 * (attempt + 1))
    raise last


# --------------------------------------------------------------------------- #
# Fetchers
# --------------------------------------------------------------------------- #
def fetch_fred(series_id, start=None):
    """Return list of [iso_date, value] from FRED's keyless CSV endpoint."""
    url = f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={series_id}"
    if start:
        url += f"&cosd={start}"
    text = _get(url)
    out = []
    for line in text.splitlines()[1:]:  # skip header
        parts = line.split(",")
        if len(parts) < 2:
            continue
        date, val = parts[0], parts[1]
        if val in ("", "."):
            continue
        try:
            out.append([date, float(val)])
        except ValueError:
            continue
    return out


def fetch_yahoo(symbol, rng="10y", interval="1d"):
    """Return list of [iso_date, close] from Yahoo Finance chart API."""
    url = (
        f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
        f"?range={rng}&interval={interval}"
    )
    data = json.loads(_get(url))
    res = data["chart"]["result"][0]
    ts = res["timestamp"]
    closes = res["indicators"]["quote"][0]["close"]
    out = []
    for t, c in zip(ts, closes):
        if c is None:
            continue
        d = datetime.fromtimestamp(t, tz=timezone.utc).strftime("%Y-%m-%d")
        out.append([d, round(float(c), 2)])
    return out


def fetch_multpl_pe():
    """Scrape S&P 500 P/E ratio monthly table from multpl.com."""
    html = _get("https://www.multpl.com/s-p-500-pe-ratio/table/by-month")
    m = re.search(r'id="datatable".*?</table>', html, re.S)
    if not m:
        return []
    rows = re.findall(r"<tr[^>]*>(.*?)</tr>", m.group(0), re.S)
    out = []
    for row in rows:
        cells = re.findall(r"<td[^>]*>(.*?)</td>", row, re.S)
        if len(cells) < 2:
            continue
        # Strip tags, then unescape entities BEFORE pulling the number - the
        # value cell prefixes the figure with entities like &#x2002; (en-space)
        # whose digits (2002) would otherwise corrupt the parsed value.
        date_txt = unescape(re.sub(r"<[^>]+>", " ", cells[0])).strip()
        val_txt = unescape(re.sub(r"<[^>]+>", " ", cells[1]))
        nums = re.findall(r"-?\d+(?:\.\d+)?", val_txt)
        if not nums:
            continue
        try:
            d = datetime.strptime(date_txt, "%b %d, %Y").strftime("%Y-%m-%d")
            out.append([d, float(nums[-1])])
        except (ValueError, TypeError):
            continue
    out.sort(key=lambda x: x[0])
    return out


def yoy_percent(series):
    """Convert a level series to year-over-year % change (monthly assumed)."""
    by_date = {d: v for d, v in series}
    dates = [d for d, _ in series]
    out = []
    for d in dates:
        y = datetime.strptime(d, "%Y-%m-%d")
        prior = f"{y.year - 1:04d}-{y.month:02d}-{y.day:02d}"
        # find nearest prior-year point
        if prior in by_date and by_date[prior] != 0:
            out.append([d, round((by_date[d] / by_date[prior] - 1) * 100, 2)])
    return out


def buffett_ratio(mktcap_b, gdp_b):
    """Market cap / GDP as a percentage (both in $B), aligned on quarterly GDP."""
    gdp_sorted = sorted(gdp_b, key=lambda x: x[0])
    out = []
    gi = 0
    for d, c in mktcap_b:
        while gi + 1 < len(gdp_sorted) and gdp_sorted[gi + 1][0] <= d:
            gi += 1
        gd, gv = gdp_sorted[gi]
        if gd <= d and gv:
            out.append([d, round(c / gv * 100, 1)])
    return out


# --------------------------------------------------------------------------- #
# Indicator definitions
# --------------------------------------------------------------------------- #
# fmt: off
PLAN = [
    # key, title, section, source, unit, fmt, source_label
    ("gdp",        "U.S. GDP",                       "Growth & Activity",      ("fred", "GDP"),          "$B",   "$,.0fB",  "FRED: GDP"),
    ("earnings",   "Corporate Earnings (Profits)",   "Growth & Activity",      ("fred", "CP"),           "$B",   "$,.0fB",  "FRED: CP"),
    ("unrate",     "Unemployment Rate",              "Growth & Activity",      ("fred", "UNRATE"),       "%",    ",.1f%",   "FRED: UNRATE"),
    ("wei",        "Weekly Economic Index",          "Growth & Activity",      ("fred", "WEI"),          "%",    ",.2f",    "FRED: WEI"),
    ("pce",        "PCE Inflation (YoY)",            "Inflation & Rates",      ("fred_yoy", "PCEPI"),    "%",    ",.2f%",   "FRED: PCEPI (YoY)"),
    ("fedrate",    "Fed Funds Rate",                 "Inflation & Rates",      ("fred", "FEDFUNDS"),     "%",    ",.2f%",   "FRED: FEDFUNDS"),
    ("dgs10",      "10-Year Treasury Yield",         "Inflation & Rates",      ("fred", "DGS10"),        "%",    ",.2f%",   "FRED: DGS10"),
    ("m2",         "M2 Money Supply",                "Money & Liquidity",      ("fred", "M2SL"),         "$B",   "$,.0fB",  "FRED: M2SL"),
    ("walcl",      "Fed Balance Sheet",              "Money & Liquidity",      ("fred", "WALCL"),        "$M",   "$,.0fM",  "FRED: WALCL"),
    ("dxy",        "U.S. Dollar Index (DXY)",        "Money & Liquidity",      ("yahoo", "DX-Y.NYB"),    "",     ",.2f",    "Yahoo: DX-Y.NYB"),
    ("sp500",      "S&P 500",                        "Markets & Valuation",    ("yahoo", "%5EGSPC"),     "",     ",.0f",    "Yahoo: ^GSPC"),
    ("pe",         "S&P 500 P/E Ratio",              "Markets & Valuation",    ("multpl", ""),           "",     ",.1f",    "multpl.com"),
    ("mktcap",     "U.S. Stock Market Value",        "Markets & Valuation",    ("mktcap", ""),           "$T",   "$,.1fT",  "FRED: NCBEILQ027S (corp. equities)"),
    ("buffett",    "Market Cap / GDP (Buffett)",     "Markets & Valuation",    ("buffett", ""),          "%",    ",.0f%",   "computed: corp. equities / GDP"),
    ("crude",      "Crude Oil (WTI)",                "Commodities & Crypto",   ("fred", "DCOILWTICO"),   "$",    "$,.2f",   "FRED: DCOILWTICO"),
    ("gold",       "Gold",                           "Commodities & Crypto",   ("yahoo", "GC=F"),        "$",    "$,.0f",   "Yahoo: GC=F"),
    ("btc",        "Bitcoin",                        "Commodities & Crypto",   ("yahoo", "BTC-USD"),     "$",    "$,.0f",   "Yahoo: BTC-USD"),
    ("house",      "U.S. House Price (Case-Shiller)","Housing",                ("fred", "CSUSHPINSA"),   "idx",  ",.1f",    "FRED: CSUSHPINSA"),
]
# fmt: on


# Heavy daily FRED series that often time out: trim history + Yahoo fallback.
HEAVY_START = {"DGS10": "1990-01-01", "DCOILWTICO": "1990-01-01"}
YAHOO_FALLBACK = {"dgs10": "%5ETNX", "crude": "CL=F"}


# --------------------------------------------------------------------------- #
# Qualitative "macro theme" tiles - each anchored to a live data source so the
# status word + summary sentence are generated from current numbers, not prose.
# --------------------------------------------------------------------------- #
def fetch_gpr():
    """Geopolitical Risk Index (Caldara & Iacoviello), monthly. Needs xlrd."""
    import xlrd  # optional dependency; theme degrades gracefully if missing

    data = _get_bytes("https://www.matteoiacoviello.com/gpr_files/data_gpr_export.xls")
    wb = xlrd.open_workbook(file_contents=data)
    sh = wb.sheet_by_index(0)
    hdr = [str(c.value).strip().upper() for c in sh.row(0)]
    gc = hdr.index("GPR")
    epoch = datetime(1899, 12, 30)
    out = []
    for r in range(1, sh.nrows):
        v = sh.cell_value(r, gc)
        if v in ("", None):
            continue
        d = (epoch + __import__("datetime").timedelta(days=int(sh.cell_value(r, 0)))).strftime("%Y-%m-%d")
        out.append([d, round(float(v), 1)])
    return out


def _spark(series, n=90):
    return series[-n:] if len(series) > n else series


def _pct(series, lookback):
    """% change over the last `lookback` points."""
    if len(series) <= lookback:
        lookback = len(series) - 1
    a, b = series[-1 - lookback][1], series[-1][1]
    return (b / a - 1) * 100 if a else 0.0


def _ago(series, days):
    """Value at or before (last_date - days); None if unavailable."""
    if not series:
        return None
    from datetime import timedelta
    last_d = datetime.strptime(series[-1][0], "%Y-%m-%d")
    cstr = (last_d - timedelta(days=days)).strftime("%Y-%m-%d")
    prior = None
    for d, v in series:
        if d <= cstr:
            prior = v
        else:
            break
    return prior


def _yoy(series):
    a = _ago(series, 365)
    if a not in (None, 0):
        return (series[-1][1] / a - 1) * 100
    return None


def summarize(key, series):
    """One-sentence, data-driven read on an indicator (mirrors theme tiles)."""
    if not series:
        return ""
    last = series[-1][1]
    yoy = _yoy(series)
    y = f"{yoy:+.1f}% y/y" if yoy is not None else "flat y/y"

    if key == "gdp":
        t = "expanding briskly" if (yoy or 0) > 5 else "growing" if (yoy or 0) > 3 else "slowing" if (yoy or 0) > 0 else "contracting"
        return f"Nominal GDP is ${last/1000:.1f}T, {y} — the economy is {t} (nominal terms)."
    if key == "earnings":
        return f"Corporate after-tax profits are ${last/1000:.2f}T, {y} — margins are {'rising' if (yoy or 0) > 0 else 'under pressure'}."
    if key == "unrate":
        chg = last - (_ago(series, 365) or last)
        lvl = "historically low" if last < 4.5 else "moderate" if last < 5.5 else "elevated"
        tr = "holding near lows" if abs(chg) < 0.3 else "drifting higher" if chg > 0 else "falling"
        return f"Unemployment at {last:.1f}% is {lvl} and {tr} ({chg:+.1f} pts y/y) — labor market {'tight' if last < 4.5 else 'loosening'}."
    if key == "wei":
        return f"The Weekly Economic Index at {last:.1f} maps to ~{last:.0f}% real growth — activity is {'solid' if last > 2 else 'sluggish' if last > 0 else 'contracting'}."
    if key == "pce":
        a = _ago(series, 365)
        acc = (last - a) if a is not None else 0
        st = "above the Fed's 2% target" if last > 2.3 else "near the Fed's 2% goal"
        tr = "cooling" if acc < -0.2 else "re-accelerating" if acc > 0.2 else "roughly steady"
        return f"PCE inflation at {last:.1f}% is {st} and {tr}."
    if key == "fedrate":
        chg = last - (_ago(series, 365) or last)
        tr = "cutting rates" if chg < -0.25 else "holding steady" if abs(chg) <= 0.25 else "hiking"
        return f"Fed funds at {last:.2f}% — policy is {'restrictive' if last > 3 else 'neutral'}; the Fed is {tr} ({chg:+.2f} pts y/y)."
    if key == "dgs10":
        chg = last - (_ago(series, 365) or last)
        return f"The 10-year Treasury yields {last:.2f}%, {chg:+.2f} pts y/y — {'higher' if chg > 0 else 'lower'} long-term borrowing costs."
    if key == "m2":
        return f"M2 money supply is ${last/1000:.1f}T, {y} — liquidity is {'expanding' if (yoy or 0) > 0 else 'contracting'}."
    if key == "walcl":
        return f"The Fed's balance sheet is ${last/1e6:.1f}T, {y} — {'quantitative tightening is draining' if (yoy or 0) < 0 else 'the Fed is adding'} liquidity."
    if key == "dxy":
        return f"The dollar index at {last:.1f} is {y} — the greenback is {'strengthening' if (yoy or 0) > 0 else 'weakening'}."
    if key == "sp500":
        return f"The S&P 500 at {last:,.0f} is {y} — equities are {'in a strong uptrend' if (yoy or 0) > 8 else 'grinding higher' if (yoy or 0) > 0 else 'under pressure'}."
    if key == "pe":
        vs = "well above" if last > 22 else "above" if last > 17 else "near" if last > 14 else "below"
        val = "stretched" if last > 25 else "rich" if last > 20 else "fair" if last > 14 else "cheap"
        return f"The S&P 500 trades at {last:.1f}× earnings, {vs} the ~16× historical average — valuations look {val}."
    if key == "mktcap":
        return f"U.S. corporate equities are worth ~${last:.0f}T, {y}."
    if key == "buffett":
        vs = "well above" if last > 130 else "above" if last > 105 else "near" if last > 85 else "below"
        return f"Market cap is {last:.0f}% of GDP, {vs} the ~100% fair-value line — the market looks {'richly valued' if last > 120 else 'fairly valued' if last > 85 else 'cheap'}."
    if key == "crude":
        return f"WTI crude at ${last:.0f}, {y} — energy costs are {'rising' if (yoy or 0) > 0 else 'easing'}."
    if key == "gold":
        return f"Gold at ${last:,.0f}, {y} — {'strong safe-haven and inflation-hedge demand' if (yoy or 0) > 5 else 'steady demand'}."
    if key == "btc":
        return f"Bitcoin near ${last:,.0f}, {y} — risk appetite is {'firm' if (yoy or 0) > 0 else 'soft'}."
    if key == "house":
        return f"The Case-Shiller index at {last:.0f} is {y} — home prices are {'still appreciating' if (yoy or 0) > 1 else 'flattening' if (yoy or 0) > -1 else 'declining'}."
    return f"Latest: {last:,.2f} ({y})."


def build_themes(cache):
    themes = []

    def add(key, title, tone, status, metric, summary, source, spark):
        themes.append({
            "key": key, "title": title, "tone": tone, "status": status,
            "metric": metric, "summary": summary, "source": source,
            "spark": spark,
        })

    # 1) Credit spreads ------------------------------------------------------ #
    try:
        hy = fetch_fred("BAMLH0A0HYM2")   # HY OAS, %
        ig = fetch_fred("BAMLC0A0CM")     # IG OAS, %
        hyb, igb = hy[-1][1] * 100, ig[-1][1] * 100  # -> bps
        if hyb < 350:
            tone, status = "good", "Calm"
            read = f"well below the ~500bp long-run average — credit is risk-on with little default stress priced in."
        elif hyb < 500:
            tone, status = "good", "Normal"
            read = "near long-run norms; credit conditions are orderly."
        elif hyb < 750:
            tone, status = "warn", "Widening"
            read = "above average — investors are demanding more compensation for risk."
        else:
            tone, status = "bad", "Stressed"
            read = "sharply elevated — markets are pricing meaningful default/recession risk."
        add("credit", "Credit Spreads", tone, status,
            f"HY {hyb:.0f}bp · IG {igb:.0f}bp",
            f"High-yield spreads at {hyb:.0f}bp (investment-grade {igb:.0f}bp) sit {read}",
            "FRED: ICE BofA HY & IG OAS", _spark(hy, 2600))
    except Exception as e:  # noqa: BLE001
        print(f"  theme credit FAILED ({e})")

    # 2) Investor sentiment (VIX) ------------------------------------------- #
    try:
        vix = fetch_yahoo("%5EVIX", rng="10y")
        v = vix[-1][1]
        if v < 13:
            tone, status, read = "warn", "Complacent", "an unusually calm tape; sub-13 readings often mark investor complacency and thin hedging demand."
        elif v < 20:
            tone, status, read = "good", "Calm", "a low-volatility, risk-on backdrop with no acute fear priced in."
        elif v < 28:
            tone, status, read = "warn", "Cautious", "rising anxiety — investors are paying up for protection."
        else:
            tone, status, read = "bad", "Fearful", "outright fear; elevated volatility typically coincides with market stress."
        add("sentiment", "Investor Sentiment", tone, status, f"VIX {v:.1f}",
            f"The VIX “fear gauge” at {v:.1f} reflects {read}", "Yahoo: ^VIX", _spark(vix, 3000))
    except Exception as e:  # noqa: BLE001
        print(f"  theme sentiment FAILED ({e})")

    # 3) Fiscal policy ------------------------------------------------------- #
    try:
        debt = cache.get("__debtgdp") or fetch_fred("GFDEGDQ188S")  # debt/GDP %
        defl = fetch_fred("MTSDS133FMS")  # monthly surplus/deficit, $M
        ttm = sum(v for _, v in defl[-12:]) / 1000  # -> $B, trailing 12m
        dg = debt[-1][1]
        tone = "warn"
        status = "Expansionary"
        add("fiscal", "Fiscal Policy", tone, status,
            f"Debt {dg:.0f}% of GDP",
            f"Federal debt is {dg:.0f}% of GDP with the Treasury running a ~${abs(ttm)/1000:.1f}T "
            f"deficit over the trailing 12 months — fiscal stance remains loose/expansionary.",
            "FRED: Debt/GDP + Monthly Treasury Statement", _spark(debt, 400))
    except Exception as e:  # noqa: BLE001
        print(f"  theme fiscal FAILED ({e})")

    # 4) Geopolitics (GPR) --------------------------------------------------- #
    try:
        gpr = fetch_gpr()
        g = gpr[-1][1]
        trend = "easing from recent highs" if len(gpr) > 3 and gpr[-1][1] < gpr[-3][1] else "rising"
        if g < 80:
            tone, status = "good", "Low"
        elif g < 130:
            tone, status = "good", "Normal"
        elif g < 180:
            tone, status = "warn", "Elevated"
        else:
            tone, status = "bad", "High"
        add("geopolitics", "Geopolitics", tone, status, f"GPR {g:.0f}",
            f"The Geopolitical Risk Index at {g:.0f} is {'above' if g > 100 else 'near'} its "
            f"long-run average (~100), signalling {status.lower()} geopolitical tension ({trend}).",
            "Caldara & Iacoviello GPR Index", _spark(gpr, 360))
    except Exception as e:  # noqa: BLE001
        print(f"  theme geopolitics FAILED ({e}); skipping (needs 'pip install xlrd')")

    # 5) Tech / AI (semiconductor proxy) ------------------------------------ #
    try:
        sox = fetch_yahoo("%5ESOX", rng="10y")
        chg = _pct(sox, 252)  # ~1y (252 trading days) return
        if chg > 15:
            tone, status, read = "good", "Booming", "a powerful AI/compute capex cycle driving the group higher."
        elif chg > 0:
            tone, status, read = "good", "Expanding", "steady gains as the AI investment cycle broadens."
        else:
            tone, status, read = "warn", "Cooling", "a pullback in the AI/semiconductor trade after its run."
        add("techai", "Tech / AI", tone, status, f"Semis {chg:+.0f}% / yr",
            f"The Philadelphia Semiconductor Index (a market proxy for the AI/compute cycle) is "
            f"{chg:+.0f}% over the past year — {read}", "Yahoo: ^SOX (PHLX Semiconductor)", _spark(sox, 3000)),
    except Exception as e:  # noqa: BLE001
        print(f"  theme techai FAILED ({e})")

    return themes


def build_outlook(indicators, themes):
    """Rules-based synthesis of the indicators into a 12-month equity outlook."""
    ind = {i["key"]: i["data"] for i in indicators}
    th = {t["key"]: t for t in themes}
    nz = lambda x, d=0.0: d if x is None else x

    def last(k):
        s = ind.get(k) or []
        return s[-1][1] if s else None

    def yoy(k):
        s = ind.get(k) or []
        return _yoy(s) if s else None

    def ago(k, d=365):
        s = ind.get(k) or []
        return _ago(s, d) if s else None

    def th_last(k):
        t = th.get(k)
        return t["spark"][-1][1] if t and t.get("spark") else None

    wei, unrate, pce = last("wei"), last("unrate"), last("pce")
    fed, ten = last("fedrate"), last("dgs10")
    pe, buff = last("pe"), last("buffett")
    spx, spx_yoy = last("sp500"), yoy("sp500")
    earn_yoy, m2_yoy = yoy("earnings"), yoy("m2")
    hy = nz(th_last("credit")) * 100  # -> bps
    vix, gpr = th_last("sentiment"), th_last("geopolitics")

    unrate_chg = nz(unrate) - nz(ago("unrate"), nz(unrate))
    pce_accel = nz(pce) - nz(ago("pce"), nz(pce))
    fed_chg = nz(fed) - nz(ago("fedrate"), nz(fed))

    bulls, risks, score = [], [], 0
    if wei is not None:
        if wei >= 2:
            score += 1; bulls.append(f"Economy expanding at/above trend (WEI ≈ {wei:.1f}% real growth)")
        elif wei < 0:
            score -= 1; risks.append(f"Activity contracting (WEI {wei:.1f}%)")
    if unrate is not None:
        if unrate < 4.5 and unrate_chg < 0.3:
            score += 1; bulls.append(f"Labor market resilient (unemployment {unrate:.1f}%, {unrate_chg:+.1f} pts y/y)")
        elif unrate_chg > 0.5:
            score -= 1; risks.append(f"Unemployment rising ({unrate:.1f}%, {unrate_chg:+.1f} pts y/y)")
    if pce is not None:
        if pce <= 2.3:
            score += 1; bulls.append(f"Inflation near the Fed's 2% target (PCE {pce:.1f}%)")
        elif pce > 3 and pce_accel > 0:
            score -= 1; risks.append(f"Inflation sticky / re-accelerating (PCE {pce:.1f}%, {pce_accel:+.1f} pts y/y)")
        else:
            risks.append(f"Inflation still above the Fed's 2% goal (PCE {pce:.1f}%)")
    if fed is not None:
        if fed_chg < -0.25:
            score += 1; bulls.append(f"Fed easing ({fed:.2f}% funds, {fed_chg:+.2f} pts y/y)")
        elif fed_chg > 0.25:
            score -= 1; risks.append(f"Fed tightening ({fed:.2f}% funds, {fed_chg:+.2f} pts y/y)")
    if ten is not None:
        if ten > 4.5:
            score -= 1; risks.append(f"Elevated 10-year yield ({ten:.2f}%) lifts discount rates and competes with equities")
        elif ten < 3:
            score += 1; bulls.append(f"Low 10-year yield ({ten:.2f}%) supports valuations")
    if hy:
        if hy < 350:
            score += 1; bulls.append(f"Credit conditions calm (high-yield spread {hy:.0f}bp)")
        elif hy > 550:
            score -= 1; risks.append(f"Credit spreads widening ({hy:.0f}bp)")
    if earn_yoy is not None:
        if earn_yoy > 5:
            score += 1; bulls.append(f"Corporate profits growing ({earn_yoy:+.0f}% y/y)")
        elif earn_yoy < 0:
            score -= 1; risks.append(f"Corporate profits falling ({earn_yoy:+.0f}% y/y)")
    if spx_yoy is not None:
        if spx_yoy > 8:
            score += 1; bulls.append(f"Strong price momentum (S&P 500 {spx_yoy:+.0f}% y/y)")
        elif spx_yoy < 0:
            score -= 1; risks.append(f"Negative price momentum (S&P 500 {spx_yoy:+.0f}% y/y)")
    if m2_yoy is not None:
        if m2_yoy > 3:
            score += 1; bulls.append(f"Money supply reflating (M2 {m2_yoy:+.1f}% y/y)")
        elif m2_yoy < 0:
            score -= 1; risks.append(f"Money supply contracting (M2 {m2_yoy:+.1f}% y/y)")
    if pe is not None and buff is not None:
        if pe > 25 or buff > 150:
            score -= 1; risks.append(f"Valuations stretched (S&P P/E {pe:.0f}×, market-cap/GDP {buff:.0f}%) — a headwind to forward returns")
        elif pe < 16:
            score += 1; bulls.append(f"Undemanding valuations (S&P P/E {pe:.0f}×)")
    if vix is not None:
        if vix > 28:
            score -= 1; risks.append(f"Elevated volatility (VIX {vix:.0f})")
        elif vix < 12:
            risks.append(f"Very low volatility (VIX {vix:.0f}) hints at complacency")
        elif vix < 20:
            score += 1; bulls.append(f"Calm volatility backdrop (VIX {vix:.0f})")
    if gpr is not None:
        if gpr > 150:
            score -= 1; risks.append(f"Elevated geopolitical risk (GPR {gpr:.0f})")
        elif gpr < 100:
            score += 1; bulls.append(f"Subdued geopolitical risk (GPR {gpr:.0f})")

    if score >= 4:
        stance, tone = "Constructive", "good"
    elif score >= 2:
        stance, tone = "Cautiously constructive", "good"
    elif score >= -1:
        stance, tone = "Balanced / mixed", "warn"
    elif score >= -3:
        stance, tone = "Cautious", "warn"
    else:
        stance, tone = "Defensive", "bad"

    _wei, _un, _pce = nz(wei), nz(unrate), nz(pce)
    _fed, _ten, _pe, _buff = nz(fed), nz(ten), nz(pe), nz(buff)
    _spx, _spy, _earn = nz(spx), nz(spx_yoy), nz(earn_yoy)
    infl_desc = "above the Fed's 2% goal" if _pce > 2.3 else "near target"
    pol_desc = "easing" if fed_chg < -0.25 else ("tightening" if fed_chg > 0.25 else "on hold")
    mom_desc = "in an uptrend" if _spy > 0 else "under pressure"
    val_desc = "historically rich" if _pe > 22 else "reasonable"
    narrative = (
        f"The U.S. economy is growing near trend (WEI ≈ {_wei:.1f}%, unemployment {_un:.1f}%), while PCE "
        f"inflation at {_pce:.1f}% remains {infl_desc}. The Fed is {pol_desc} with policy rates at {_fed:.2f}% "
        f"and the 10-year Treasury at {_ten:.2f}%. Credit is {'calm' if hy and hy < 350 else 'tightening'} "
        f"(high-yield spread {hy:.0f}bp) and the S&P 500 is {mom_desc} ({_spy:+.0f}% y/y) — but equity "
        f"valuations are {val_desc} (P/E {_pe:.0f}×, market-cap/GDP {_buff:.0f}%), which historically caps "
        f"medium-term upside. Net read across {len(bulls)} tailwinds and {len(risks)} headwinds: {stance.lower()}."
    )

    scenarios = [
        {"name": "Base case", "prob": "~55%", "tone": tone, "text": (
            f"{stance}. Solid growth, {pol_desc} policy and contained credit can keep equities grinding higher, "
            f"but rich starting valuations (P/E {_pe:.0f}×) have historically coincided with below-average 12-month "
            f"returns — expect modest, single-digit gains with above-average sensitivity to rate and earnings surprises.")},
        {"name": "Bull case", "prob": "~25%", "tone": "good", "text": (
            f"If inflation resumes cooling toward 2%, the Fed eases further, and profits (currently {_earn:+.0f}% y/y) "
            f"stay strong, multiples can hold and the index could deliver above-average, low-double-digit gains.")},
        {"name": "Bear case", "prob": "~20%", "tone": "bad", "text": (
            f"A re-acceleration in inflation, a jump in credit spreads from today's {hy:.0f}bp, or an earnings/growth "
            f"disappointment against stretched valuations (market-cap/GDP {_buff:.0f}%) could drive a double-digit drawdown.")},
    ]

    metrics = [
        {"label": "Real growth (WEI)", "value": f"{_wei:.1f}%"},
        {"label": "Unemployment", "value": f"{_un:.1f}%"},
        {"label": "PCE inflation", "value": f"{_pce:.1f}%"},
        {"label": "Fed funds", "value": f"{_fed:.2f}%"},
        {"label": "10Y Treasury", "value": f"{_ten:.2f}%"},
        {"label": "HY credit spread", "value": f"{hy:.0f}bp"},
        {"label": "S&P 500", "value": f"{_spx:,.0f}"},
        {"label": "S&P 500 P/E", "value": f"{_pe:.1f}×"},
        {"label": "Buffett (cap/GDP)", "value": f"{_buff:.0f}%"},
        {"label": "VIX", "value": f"{nz(vix):.1f}"},
    ]

    disclaimer = (
        "Rules-based synthesis of the indicators below, generated from the data for informational purposes only — "
        "not investment advice, a recommendation, or a guarantee. Scenario probabilities are illustrative. Markets "
        "are uncertain and can deviate sharply from any data-driven base case."
    )

    return {
        "stance": stance, "tone": tone, "score": score,
        "narrative": narrative, "metrics": metrics,
        "bulls": bulls, "risks": risks, "scenarios": scenarios,
        "disclaimer": disclaimer,
    }


def build():
    indicators = []
    cache = {}  # store raw fred series for reuse (gdp, equities)

    for key, title, section, (kind, sid), unit, fmt, src_label in PLAN:
        print(f"  fetching {key:10s} <- {src_label} ... ", end="", flush=True)
        try:
            if kind == "fred":
                series = fetch_fred(sid, start=HEAVY_START.get(sid))
                cache[sid] = series
            elif kind == "fred_yoy":
                series = yoy_percent(fetch_fred(sid))
            elif kind == "yahoo":
                series = fetch_yahoo(sid)
            elif kind == "multpl":
                series = fetch_multpl_pe()
            elif kind == "mktcap":
                eq = cache.get("NCBEILQ027S") or fetch_fred("NCBEILQ027S")
                cache["NCBEILQ027S"] = eq
                series = [[d, round(v / 1e6, 3)] for d, v in eq]  # $M -> $T
            elif kind == "buffett":
                eq = cache.get("NCBEILQ027S") or fetch_fred("NCBEILQ027S")
                cache["NCBEILQ027S"] = eq
                eq_b = [[d, v / 1e3] for d, v in eq]  # $M -> $B
                gdp = cache.get("GDP") or fetch_fred("GDP")
                series = buffett_ratio(eq_b, gdp)
            else:
                series = []
            if not series:
                raise ValueError("empty series")
            print(f"ok ({len(series)} pts, latest {series[-1][1]})")
        except Exception as e:  # noqa: BLE001 - one failure shouldn't kill the rest
            series = []
            if key in YAHOO_FALLBACK:
                print(f"FAILED ({e}); trying Yahoo fallback ... ", end="", flush=True)
                try:
                    series = fetch_yahoo(YAHOO_FALLBACK[key])
                    src_label += " (Yahoo fallback)"
                    print(f"ok ({len(series)} pts, latest {series[-1][1]})")
                except Exception as e2:  # noqa: BLE001
                    print(f"FAILED ({e2})")
            else:
                print(f"FAILED ({e})")

        indicators.append(
            {
                "key": key,
                "title": title,
                "section": section,
                "unit": unit,
                "fmt": fmt,
                "source": src_label,
                "data": series,
                "summary": summarize(key, series),
            }
        )
        time.sleep(0.3)  # be polite

    print("\n  building macro theme tiles ...")
    themes = build_themes(cache)
    print(f"  {len(themes)}/5 theme tiles built.")

    print("  building executive summary & outlook ...")
    outlook = build_outlook(indicators, themes)
    print(f"  stance: {outlook['stance']} (score {outlook['score']:+d})")

    payload = {
        "generated": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "indicators": indicators,
        "themes": themes,
        "outlook": outlook,
    }
    html = HTML_TEMPLATE.replace("/*__DATA__*/", json.dumps(payload))
    with open(OUT, "w", encoding="utf-8") as f:
        f.write(html)
    ok = sum(1 for i in indicators if i["data"])
    print(f"\nWrote {OUT}")
    print(f"{ok}/{len(indicators)} indicators loaded successfully.")


# --------------------------------------------------------------------------- #
# HTML / JS template (self-contained, Chart.js from CDN)
# --------------------------------------------------------------------------- #
HTML_TEMPLATE = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Macro Economic Dashboard</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.1/dist/chart.umd.min.js"></script>
<style>
  :root{
    --bg:#0b0f17; --panel:#141b27; --panel2:#1b2434;
    --border:#243047; --text:#e6ecf5; --muted:#8aa0bd; --accent:#4ea1ff;
    --up:#3ddc84; --down:#ff5c6c; --grid:rgba(255,255,255,.05);
  }
  *{box-sizing:border-box}
  body{margin:0;background:var(--bg);color:var(--text);
       font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;}
  header{padding:22px 28px;border-bottom:1px solid var(--border);
         display:flex;align-items:baseline;gap:16px;flex-wrap:wrap;
         background:linear-gradient(180deg,#101725,#0b0f17);}
  header h1{font-size:20px;margin:0;letter-spacing:.3px}
  header .sub{color:var(--muted);font-size:13px}
  .controls{margin-left:auto;display:flex;gap:6px}
  .controls button{background:var(--panel2);color:var(--muted);border:1px solid var(--border);
       padding:6px 12px;border-radius:8px;cursor:pointer;font-size:12px;font-weight:600}
  .controls button.active{background:var(--accent);color:#04101f;border-color:var(--accent)}
  .section{padding:8px 28px 0}
  .section h2{font-size:13px;text-transform:uppercase;letter-spacing:1.2px;
              color:var(--muted);margin:22px 0 10px;font-weight:700}
  .grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(330px,1fr));gap:16px}
  .card{background:var(--panel);border:1px solid var(--border);border-radius:14px;
        padding:16px 16px 10px;display:flex;flex-direction:column;min-height:250px}
  .card .csum{font-size:11.5px;color:#a9bcd6;line-height:1.45;margin-top:8px}
  .card .top{display:flex;justify-content:space-between;align-items:flex-start;gap:8px}
  .card .title{font-size:13px;color:var(--muted);font-weight:600;line-height:1.3}
  .card .val{font-size:26px;font-weight:700;margin-top:2px}
  .card .chg{font-size:12px;font-weight:700;padding:2px 7px;border-radius:6px;white-space:nowrap}
  .chg.up{color:var(--up);background:rgba(61,220,132,.12)}
  .chg.down{color:var(--down);background:rgba(255,92,108,.12)}
  .card .chartwrap{flex:1;position:relative;margin-top:8px;min-height:110px}
  .card .foot{display:flex;justify-content:space-between;color:#5b6f8c;
              font-size:10.5px;margin-top:6px}
  .card.empty .val{color:var(--down);font-size:15px}
  /* macro theme tiles */
  .themes{display:grid;grid-template-columns:repeat(auto-fill,minmax(300px,1fr));gap:16px}
  .tile{background:linear-gradient(160deg,#161f2f,#121826);border:1px solid var(--border);
        border-radius:14px;padding:16px;display:flex;flex-direction:column;position:relative;overflow:hidden;min-height:250px}
  .tile:before{content:"";position:absolute;left:0;top:0;bottom:0;width:4px}
  .tile.good:before{background:var(--up)} .tile.warn:before{background:#f5b74e} .tile.bad:before{background:var(--down)}
  .tile .thead{display:flex;justify-content:space-between;align-items:center;gap:8px}
  .tile .ttitle{font-size:14px;font-weight:700}
  .tile .badge{font-size:11px;font-weight:800;letter-spacing:.4px;text-transform:uppercase;
        padding:3px 9px;border-radius:20px;white-space:nowrap}
  .tile.good .badge{color:var(--up);background:rgba(61,220,132,.13)}
  .tile.warn .badge{color:#f5b74e;background:rgba(245,183,78,.14)}
  .tile.bad .badge{color:var(--down);background:rgba(255,92,108,.14)}
  .tile .metric{font-size:20px;font-weight:700;margin:8px 0 2px}
  .tile .summary{font-size:12px;color:#a9bcd6;line-height:1.5}
  .tile .tspark{flex:1;min-height:110px;margin:8px 0 4px}
  .tile .tsource{color:#5b6f8c;font-size:10px}
  /* executive summary & outlook */
  .outlook{background:linear-gradient(160deg,#15202f,#0d131e);border:1px solid var(--border);
        border-radius:16px;padding:20px 22px;margin-top:4px}
  .outlook .ohead{display:flex;align-items:center;gap:12px;flex-wrap:wrap;margin-bottom:4px}
  .outlook .otitle{font-size:16px;font-weight:800}
  .outlook .stance{font-size:12px;font-weight:800;text-transform:uppercase;letter-spacing:.5px;
        padding:4px 12px;border-radius:20px}
  .outlook .stance.good{color:var(--up);background:rgba(61,220,132,.13)}
  .outlook .stance.warn{color:#f5b74e;background:rgba(245,183,78,.14)}
  .outlook .stance.bad{color:var(--down);background:rgba(255,92,108,.14)}
  .outlook .oscore{font-size:11px;color:var(--muted);margin-left:auto}
  .outlook .narr{font-size:13px;color:#cdd9ea;line-height:1.6;margin:8px 0 14px;max-width:1150px}
  .metrics-strip{display:grid;grid-template-columns:repeat(auto-fill,minmax(140px,1fr));gap:10px;margin-bottom:16px}
  .mchip{background:rgba(255,255,255,.03);border:1px solid var(--border);border-radius:10px;padding:8px 11px}
  .mchip .ml{font-size:10px;color:var(--muted);text-transform:uppercase;letter-spacing:.4px}
  .mchip .mv{font-size:16px;font-weight:700;margin-top:2px}
  .ocols{display:grid;grid-template-columns:1fr 1fr;gap:18px;margin-bottom:16px}
  .ocol h4{font-size:12px;text-transform:uppercase;letter-spacing:.6px;margin:0 0 7px}
  .ocol.bull h4{color:var(--up)} .ocol.risk h4{color:#f5b74e}
  .ocol ul{margin:0;padding-left:16px} .ocol li{font-size:12px;color:#b9c7db;line-height:1.55;margin-bottom:5px}
  .scenarios{display:grid;grid-template-columns:repeat(3,1fr);gap:12px}
  .scen{border:1px solid var(--border);border-radius:12px;padding:12px 13px;background:rgba(255,255,255,.02)}
  .scen .sname{font-weight:800;font-size:11.5px;text-transform:uppercase;letter-spacing:.5px;
        display:flex;justify-content:space-between;gap:8px}
  .scen.good .sname{color:var(--up)} .scen.warn .sname{color:#f5b74e} .scen.bad .sname{color:var(--down)}
  .scen .stext{font-size:12px;color:#b9c7db;line-height:1.55;margin-top:7px}
  .outlook .odisc{font-size:10.5px;color:#5b6f8c;margin-top:15px;line-height:1.5}
  @media(max-width:760px){.ocols,.scenarios{grid-template-columns:1fr}}
  footer{color:var(--muted);font-size:12px;padding:24px 28px;border-top:1px solid var(--border);margin-top:24px}
  a{color:var(--accent);text-decoration:none}
</style>
</head>
<body>
<header>
  <h1>📊 Macro Economic Dashboard</h1>
  <span class="sub" id="updated"></span>
  <div class="controls" id="ranges">
    <button data-r="365">1Y</button>
    <button data-r="1825">5Y</button>
    <button data-r="3650">10Y</button>
    <button data-r="0" class="active">Max</button>
  </div>
</header>
<div id="app"></div>
<footer>
  Data: FRED (St. Louis Fed), Yahoo Finance, and multpl.com. This analysis is for informational purposes only and does not constitute any investment advice. AI can make mistakes. Always verify the information.
</footer>

<script>
const PAYLOAD = /*__DATA__*/;
let RANGE = 0; // days, 0 = max
const charts = {};

function fmtNum(v, fmt){
  if(v===null||v===undefined||isNaN(v)) return "—";
  let prefix = fmt.includes("$") ? "$" : "";
  let suffix = "";
  if(fmt.endsWith("%")) suffix = "%";
  else if(fmt.endsWith("T")) suffix = "T";
  else if(fmt.endsWith("B")) suffix = "B";
  else if(fmt.endsWith("M")) suffix = "M";
  let dec = 2;
  const m = fmt.match(/\.(\d)f/); if(m) dec = +m[1];
  let n = Math.abs(v) >= 1000 || dec===0
        ? v.toLocaleString("en-US",{maximumFractionDigits:dec, minimumFractionDigits:dec})
        : v.toFixed(dec);
  return prefix + n + suffix;
}

function sliceRange(data){
  if(!RANGE || !data.length) return data;
  const cutoff = new Date();
  cutoff.setDate(cutoff.getDate() - RANGE);
  const c = cutoff.toISOString().slice(0,10);
  const s = data.filter(d => d[0] >= c);
  return s.length > 1 ? s : data.slice(-2);
}

function render(){
  const app = document.getElementById("app");
  app.innerHTML = "";
  document.getElementById("updated").textContent =
    "Updated " + PAYLOAD.generated + "  •  " +
    (PAYLOAD.indicators.filter(i=>i.data.length).length + (PAYLOAD.themes||[]).length) + "/" +
    (PAYLOAD.indicators.length + (PAYLOAD.themes||[]).length) + " series live";

  renderOutlook();
  renderThemes();

  const sections = [];
  PAYLOAD.indicators.forEach(i => { if(!sections.includes(i.section)) sections.push(i.section); });

  sections.forEach(sec => {
    const wrap = document.createElement("div");
    wrap.className = "section";
    wrap.innerHTML = `<h2>${sec}</h2><div class="grid"></div>`;
    const grid = wrap.querySelector(".grid");
    PAYLOAD.indicators.filter(i=>i.section===sec).forEach(ind => grid.appendChild(card(ind)));
    app.appendChild(wrap);
  });
  PAYLOAD.indicators.forEach(drawChart);
}

function renderOutlook(){
  const o = PAYLOAD.outlook;
  if(!o) return;
  const app = document.getElementById("app");
  const wrap = document.createElement("div");
  wrap.className = "section";
  wrap.innerHTML = `
    <h2>Executive Summary &amp; 12-Month Outlook</h2>
    <div class="outlook">
      <div class="ohead">
        <div class="otitle">U.S. Stock Market — 12-Month Outlook</div>
        <div class="stance ${o.tone}">${o.stance}</div>
        <div class="oscore">net factor score ${o.score>0?"+":""}${o.score}</div>
      </div>
      <div class="narr">${o.narrative}</div>
      <div class="metrics-strip">${o.metrics.map(m=>
        `<div class="mchip"><div class="ml">${m.label}</div><div class="mv">${m.value}</div></div>`).join("")}</div>
      <div class="ocols">
        <div class="ocol bull"><h4>Tailwinds</h4><ul>${o.bulls.map(b=>`<li>${b}</li>`).join("")}</ul></div>
        <div class="ocol risk"><h4>Headwinds</h4><ul>${o.risks.map(r=>`<li>${r}</li>`).join("")}</ul></div>
      </div>
      <div class="scenarios">${o.scenarios.map(s=>
        `<div class="scen ${s.tone}"><div class="sname"><span>${s.name}</span><span>${s.prob}</span></div>
         <div class="stext">${s.text}</div></div>`).join("")}</div>
      <div class="odisc">${o.disclaimer}</div>
    </div>`;
  app.appendChild(wrap);
}

function renderThemes(){
  const themes = PAYLOAD.themes || [];
  if(!themes.length) return;
  const app = document.getElementById("app");
  const wrap = document.createElement("div");
  wrap.className = "section";
  wrap.innerHTML = `<h2>Macro Themes — Current State</h2><div class="themes"></div>`;
  const grid = wrap.querySelector(".themes");
  themes.forEach(t => {
    const el = document.createElement("div");
    el.className = "tile " + t.tone;
    el.innerHTML = `
      <div class="thead"><div class="ttitle">${t.title}</div>
        <div class="badge">${t.status}</div></div>
      <div class="metric">${t.metric}</div>
      <div class="summary">${t.summary}</div>
      <div class="tspark"><canvas id="t_${t.key}"></canvas></div>
      <div class="tsource">${t.source}</div>`;
    grid.appendChild(el);
  });
  app.appendChild(wrap);
  themes.forEach(drawSpark);
}

function drawSpark(t){
  const ctx = document.getElementById("t_"+t.key);
  if(!ctx || !t.spark || !t.spark.length) return;
  if(charts["t_"+t.key]) charts["t_"+t.key].destroy();
  const sl = sliceRange(t.spark);
  const vals = sl.map(d=>d[1]);
  let mn = Math.min(...vals), mx = Math.max(...vals);
  const pad = (mx - mn) * 0.08 || Math.abs(mx) * 0.05 || 1;
  mn -= pad; mx += pad;
  const fmtY = v => Math.abs(v) >= 1000 ? v.toLocaleString("en-US",{maximumFractionDigits:0})
                  : Math.abs(v) >= 100 ? v.toFixed(0) : v.toFixed(1);
  const col = t.tone==="bad" ? "#ff5c6c" : t.tone==="warn" ? "#f5b74e" : "#3ddc84";
  charts["t_"+t.key] = new Chart(ctx, {
    type:"line",
    data:{ labels:sl.map(d=>d[0]),
      datasets:[{ data:vals, borderColor:col, borderWidth:1.5,
        fill:false, pointRadius:0, tension:.15 }] },
    options:{ responsive:true, maintainAspectRatio:false, animation:false,
      plugins:{ legend:{display:false},
        tooltip:{ mode:"index", intersect:false,
          callbacks:{ label:c=>c.parsed.y.toLocaleString() } } },
      scales:{ x:{display:false},
        y:{ min:mn, max:mx, position:"right",
            ticks:{ color:"#5b6f8c", font:{size:8}, maxTicksLimit:2, callback:fmtY },
            grid:{ color:"rgba(255,255,255,.04)" } } } }
  });
}

function card(ind){
  const el = document.createElement("div");
  el.className = "card" + (ind.data.length ? "" : " empty");
  if(!ind.data.length){
    el.innerHTML = `<div class="top"><div class="title">${ind.title}</div></div>
      <div class="val">data unavailable</div>
      <div class="foot"><span>${ind.source}</span></div>`;
    return el;
  }
  const sl = sliceRange(ind.data);
  const last = sl[sl.length-1][1];
  const first = sl[0][1];
  const chg = last - first;
  const pct = first ? (chg/Math.abs(first))*100 : 0;
  const up = chg >= 0;
  const arrow = up ? "▲" : "▼";
  el.innerHTML = `
    <div class="top">
      <div>
        <div class="title">${ind.title}</div>
        <div class="val">${fmtNum(last, ind.fmt)}</div>
      </div>
      <div class="chg ${up?'up':'down'}">${arrow} ${fmtNum(Math.abs(pct),",.1f")}%</div>
    </div>
    ${ind.summary ? `<div class="csum">${ind.summary}</div>` : ""}
    <div class="chartwrap"><canvas id="c_${ind.key}"></canvas></div>
    <div class="foot"><span>${ind.source}</span><span>since ${sl[0][0]}</span></div>`;
  return el;
}

function drawChart(ind){
  if(!ind.data.length) return;
  const sl = sliceRange(ind.data);
  const ctx = document.getElementById("c_"+ind.key);
  if(!ctx) return;
  if(charts[ind.key]) charts[ind.key].destroy();
  const up = sl[sl.length-1][1] >= sl[0][1];
  const col = up ? "#3ddc84" : "#ff5c6c";
  const grad = ctx.getContext("2d").createLinearGradient(0,0,0,140);
  grad.addColorStop(0, up?"rgba(61,220,132,.30)":"rgba(255,92,108,.30)");
  grad.addColorStop(1, "rgba(0,0,0,0)");
  charts[ind.key] = new Chart(ctx, {
    type:"line",
    data:{ labels: sl.map(d=>d[0]),
      datasets:[{ data: sl.map(d=>d[1]), borderColor:col, backgroundColor:grad,
        borderWidth:1.6, fill:true, pointRadius:0, tension:.15 }] },
    options:{ responsive:true, maintainAspectRatio:false, animation:false,
      plugins:{ legend:{display:false},
        tooltip:{ mode:"index", intersect:false,
          callbacks:{ label:c=>fmtNum(c.parsed.y, ind.fmt) } } },
      scales:{
        x:{ display:false },
        y:{ ticks:{ color:"#6b80a0", font:{size:9}, maxTicksLimit:4,
              callback:v=>fmtNum(v, ind.fmt) },
            grid:{ color:"rgba(255,255,255,.05)" } } } }
  });
}

document.getElementById("ranges").addEventListener("click", e=>{
  if(e.target.tagName!=="BUTTON") return;
  RANGE = +e.target.dataset.r;
  document.querySelectorAll("#ranges button").forEach(b=>b.classList.remove("active"));
  e.target.classList.add("active");
  render();
});

render();
</script>
</body>
</html>
"""


if __name__ == "__main__":
    print("Building Macro Economic Dashboard...\n")
    build()

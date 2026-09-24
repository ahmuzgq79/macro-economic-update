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
import shutil
import subprocess
import sys
import time
import urllib.request
from datetime import datetime, timezone
from html import unescape

HERE = __import__("os").path.dirname(__import__("os").path.abspath(__file__))
OUT = __import__("os").path.join(HERE, "dashboard.html")

# Keep the UA simple: impersonating a full browser makes Akamai (FRED's CDN)
# expect a matching browser TLS fingerprint and reset the connection.
UA = {"User-Agent": "Mozilla/5.0 (compatible; MacroDashboard/1.0)"}


def _get(url, headers=None, timeout=40, retries=4):
    return _get_bytes(url, headers, timeout, retries).decode("utf-8", "replace")


def _curl_get(url, timeout, retries=4):
    """Fetch via system curl. Preferred over urllib because on some networks
    urllib's TLS handshake to FRED's CDN is reset while curl (system TLS)
    succeeds. Retries to ride out intermittent connection resets."""
    exe = shutil.which("curl")
    if not exe:
        return None
    for attempt in range(retries):
        try:
            p = subprocess.run(
                [exe, "-sSL", "-m", str(int(timeout)), "-A", UA["User-Agent"], url],
                capture_output=True, timeout=timeout + 10,
            )
            if p.returncode == 0 and p.stdout:
                return p.stdout
        except Exception:  # noqa: BLE001
            pass
        time.sleep(1.2 * (attempt + 1))
    return None


def _get_bytes(url, headers=None, timeout=40, retries=4):
    # curl first (reliable here), urllib as fallback for environments without curl.
    data = _curl_get(url, timeout, retries)
    if data is not None:
        return data
    last = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers=headers or UA)
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read()
        except Exception as e:  # noqa: BLE001
            last = e
            time.sleep(min(1.5 * (attempt + 1), 8))
    raise last if last else RuntimeError("fetch failed: " + url)


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
    """One-sentence, data-driven read on an indicator, returned as {en, zh}."""
    if not series:
        return {"en": "", "zh": ""}
    last = series[-1][1]
    yoy = _yoy(series)
    y = f"{yoy:+.1f}% y/y" if yoy is not None else "flat y/y"
    yz = f"同比{yoy:+.1f}%" if yoy is not None else "同比持平"
    g = yoy or 0

    if key == "gdp":
        t = "expanding briskly" if g > 5 else "growing" if g > 3 else "slowing" if g > 0 else "contracting"
        tz = "强劲扩张" if g > 5 else "增长" if g > 3 else "放缓" if g > 0 else "萎缩"
        return {"en": f"Nominal GDP is ${last/1000:.1f}T, {y} — the economy is {t} (nominal terms).",
                "zh": f"名义GDP为{last/1000:.1f}万亿美元，{yz}——经济{tz}（名义值）。"}
    if key == "earnings":
        return {"en": f"Corporate after-tax profits are ${last/1000:.2f}T, {y} — margins are {'rising' if g > 0 else 'under pressure'}.",
                "zh": f"企业税后利润为{last/1000:.2f}万亿美元，{yz}——利润率{'上升' if g > 0 else '承压'}。"}
    if key == "unrate":
        chg = last - (_ago(series, 365) or last)
        lvl = "historically low" if last < 4.5 else "moderate" if last < 5.5 else "elevated"
        lvlz = "处于历史低位" if last < 4.5 else "中等" if last < 5.5 else "偏高"
        tr = "holding near lows" if abs(chg) < 0.3 else "drifting higher" if chg > 0 else "falling"
        trz = "在低位附近企稳" if abs(chg) < 0.3 else "缓慢上升" if chg > 0 else "下降"
        return {"en": f"Unemployment at {last:.1f}% is {lvl} and {tr} ({chg:+.1f} pts y/y) — labor market {'tight' if last < 4.5 else 'loosening'}.",
                "zh": f"失业率为{last:.1f}%，{lvlz}，且{trz}（同比{chg:+.1f}个百分点）——劳动力市场{'紧张' if last < 4.5 else '趋松'}。"}
    if key == "wei":
        act = "solid" if last > 2 else "sluggish" if last > 0 else "contracting"
        actz = "稳健" if last > 2 else "疲弱" if last > 0 else "萎缩"
        return {"en": f"The Weekly Economic Index at {last:.1f} maps to ~{last:.0f}% real growth — activity is {act}.",
                "zh": f"每周经济指数为{last:.1f}，对应约{last:.0f}%的实际增长——经济活动{actz}。"}
    if key == "pce":
        a = _ago(series, 365)
        acc = (last - a) if a is not None else 0
        st = "above the Fed's 2% target" if last > 2.3 else "near the Fed's 2% goal"
        stz = "高于美联储2%的目标" if last > 2.3 else "接近美联储2%的目标"
        tr = "cooling" if acc < -0.2 else "re-accelerating" if acc > 0.2 else "roughly steady"
        trz = "正在降温" if acc < -0.2 else "重新加速" if acc > 0.2 else "大致平稳"
        return {"en": f"PCE inflation at {last:.1f}% is {st} and {tr}.",
                "zh": f"PCE通胀为{last:.1f}%，{stz}，且{trz}。"}
    if key == "fedrate":
        chg = last - (_ago(series, 365) or last)
        tr = "cutting rates" if chg < -0.25 else "holding steady" if abs(chg) <= 0.25 else "hiking"
        trz = "正在降息" if chg < -0.25 else "维持不变" if abs(chg) <= 0.25 else "加息"
        return {"en": f"Fed funds at {last:.2f}% — policy is {'restrictive' if last > 3 else 'neutral'}; the Fed is {tr} ({chg:+.2f} pts y/y).",
                "zh": f"联邦基金利率为{last:.2f}%——政策{'偏紧' if last > 3 else '中性'}；美联储{trz}（同比{chg:+.2f}个百分点）。"}
    if key == "dgs10":
        chg = last - (_ago(series, 365) or last)
        return {"en": f"The 10-year Treasury yields {last:.2f}%, {chg:+.2f} pts y/y — {'higher' if chg > 0 else 'lower'} long-term borrowing costs.",
                "zh": f"10年期美国国债收益率为{last:.2f}%，同比{chg:+.2f}个百分点——长期借贷成本{'上升' if chg > 0 else '下降'}。"}
    if key == "m2":
        return {"en": f"M2 money supply is ${last/1000:.1f}T, {y} — liquidity is {'expanding' if g > 0 else 'contracting'}.",
                "zh": f"M2货币供应为{last/1000:.1f}万亿美元，{yz}——流动性{'扩张' if g > 0 else '收缩'}。"}
    if key == "walcl":
        return {"en": f"The Fed's balance sheet is ${last/1e6:.1f}T, {y} — {'quantitative tightening is draining' if g < 0 else 'the Fed is adding'} liquidity.",
                "zh": f"美联储资产负债表为{last/1e6:.1f}万亿美元，{yz}——{'量化紧缩正在抽走' if g < 0 else '美联储正在投放'}流动性。"}
    if key == "dxy":
        return {"en": f"The dollar index at {last:.1f} is {y} — the greenback is {'strengthening' if g > 0 else 'weakening'}.",
                "zh": f"美元指数为{last:.1f}，{yz}——美元{'走强' if g > 0 else '走弱'}。"}
    if key == "sp500":
        tr = "in a strong uptrend" if g > 8 else "grinding higher" if g > 0 else "under pressure"
        trz = "处于强劲上升趋势" if g > 8 else "稳步走高" if g > 0 else "承压"
        return {"en": f"The S&P 500 at {last:,.0f} is {y} — equities are {tr}.",
                "zh": f"标普500为{last:,.0f}点，{yz}——股市{trz}。"}
    if key == "pe":
        vs = "well above" if last > 22 else "above" if last > 17 else "near" if last > 14 else "below"
        vsz = "远高于" if last > 22 else "高于" if last > 17 else "接近" if last > 14 else "低于"
        val = "stretched" if last > 25 else "rich" if last > 20 else "fair" if last > 14 else "cheap"
        valz = "过高" if last > 25 else "偏高" if last > 20 else "合理" if last > 14 else "偏低"
        return {"en": f"The S&P 500 trades at {last:.1f}× earnings, {vs} the ~16× historical average — valuations look {val}.",
                "zh": f"标普500市盈率为{last:.1f}倍，{vsz}约16倍的历史均值——估值{valz}。"}
    if key == "mktcap":
        return {"en": f"U.S. corporate equities are worth ~${last:.0f}T, {y}.",
                "zh": f"美国企业股票总市值约{last:.0f}万亿美元，{yz}。"}
    if key == "buffett":
        vs = "well above" if last > 130 else "above" if last > 105 else "near" if last > 85 else "below"
        vsz = "远高于" if last > 130 else "高于" if last > 105 else "接近" if last > 85 else "低于"
        val = "richly valued" if last > 120 else "fairly valued" if last > 85 else "cheap"
        valz = "估值偏高" if last > 120 else "估值合理" if last > 85 else "估值偏低"
        return {"en": f"Market cap is {last:.0f}% of GDP, {vs} the ~100% fair-value line — the market looks {val}.",
                "zh": f"市值占GDP的{last:.0f}%，{vsz}约100%的合理估值线——市场{valz}。"}
    if key == "crude":
        return {"en": f"WTI crude at ${last:.0f}, {y} — energy costs are {'rising' if g > 0 else 'easing'}.",
                "zh": f"WTI原油为{last:.0f}美元，{yz}——能源成本{'上升' if g > 0 else '回落'}。"}
    if key == "gold":
        d = "strong safe-haven and inflation-hedge demand" if g > 5 else "steady demand"
        dz = "避险与抗通胀需求强劲" if g > 5 else "需求平稳"
        return {"en": f"Gold at ${last:,.0f}, {y} — {d}.",
                "zh": f"黄金为{last:,.0f}美元，{yz}——{dz}。"}
    if key == "btc":
        return {"en": f"Bitcoin near ${last:,.0f}, {y} — risk appetite is {'firm' if g > 0 else 'soft'}.",
                "zh": f"比特币约{last:,.0f}美元，{yz}——风险偏好{'偏强' if g > 0 else '偏弱'}。"}
    if key == "house":
        h = "still appreciating" if g > 1 else "flattening" if g > -1 else "declining"
        hz = "仍在上涨" if g > 1 else "趋于平缓" if g > -1 else "下跌"
        return {"en": f"The Case-Shiller index at {last:.0f} is {y} — home prices are {h}.",
                "zh": f"Case-Shiller房价指数为{last:.0f}，{yz}——房价{hz}。"}
    return {"en": f"Latest: {last:,.2f} ({y}).", "zh": f"最新值：{last:,.2f}（{yz}）。"}


SECTION_ZH = {
    "Growth & Activity": "增长与经济活动",
    "Inflation & Rates": "通胀与利率",
    "Money & Liquidity": "货币与流动性",
    "Markets & Valuation": "市场与估值",
    "Commodities & Crypto": "大宗商品与加密货币",
    "Housing": "房地产",
}
TITLE_ZH = {
    "gdp": "美国GDP", "earnings": "企业盈利（利润）", "unrate": "失业率",
    "wei": "每周经济指数", "pce": "PCE通胀（同比）", "fedrate": "联邦基金利率",
    "dgs10": "10年期国债收益率", "m2": "M2货币供应", "walcl": "美联储资产负债表",
    "dxy": "美元指数（DXY）", "sp500": "标普500", "pe": "标普500市盈率",
    "mktcap": "美国股市市值", "buffett": "市值/GDP（巴菲特指标）", "crude": "原油（WTI）",
    "gold": "黄金", "btc": "比特币", "house": "美国房价（Case-Shiller）",
}


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
            tone, st, stz = "good", "Calm", "平静"
            read = "well below the ~500bp long-run average — credit is risk-on with little default stress priced in."
            readz = "远低于约500个基点的长期均值——信用市场风险偏好高，几乎未计入违约压力。"
        elif hyb < 500:
            tone, st, stz = "good", "Normal", "正常"
            read = "near long-run norms; credit conditions are orderly."
            readz = "接近长期均值；信用状况有序。"
        elif hyb < 750:
            tone, st, stz = "warn", "Widening", "走阔"
            read = "above average — investors are demanding more compensation for risk."
            readz = "高于均值——投资者要求更高的风险补偿。"
        else:
            tone, st, stz = "bad", "Stressed", "承压"
            read = "sharply elevated — markets are pricing meaningful default/recession risk."
            readz = "大幅走高——市场正在计入明显的违约/衰退风险。"
        add("credit", {"en": "Credit Spreads", "zh": "信用利差"}, tone, {"en": st, "zh": stz},
            {"en": f"HY {hyb:.0f}bp · IG {igb:.0f}bp", "zh": f"高收益 {hyb:.0f}基点 · 投资级 {igb:.0f}基点"},
            {"en": f"High-yield spreads at {hyb:.0f}bp (investment-grade {igb:.0f}bp) sit {read}",
             "zh": f"高收益债利差为{hyb:.0f}个基点（投资级{igb:.0f}个基点），{readz}"},
            "FRED: ICE BofA HY & IG OAS", _spark(hy, 2600))
    except Exception as e:  # noqa: BLE001
        print(f"  theme credit FAILED ({e})")

    # 2) Investor sentiment (VIX) ------------------------------------------- #
    try:
        vix = fetch_yahoo("%5EVIX", rng="10y")
        v = vix[-1][1]
        if v < 13:
            tone, st, stz = "warn", "Complacent", "自满"
            read = "an unusually calm tape; sub-13 readings often mark investor complacency and thin hedging demand."
            readz = "市场异常平静；低于13的读数通常意味着投资者自满、对冲需求低迷。"
        elif v < 20:
            tone, st, stz = "good", "Calm", "平静"
            read = "a low-volatility, risk-on backdrop with no acute fear priced in."
            readz = "低波动、风险偏好的环境，未计入明显恐慌。"
        elif v < 28:
            tone, st, stz = "warn", "Cautious", "谨慎"
            read = "rising anxiety — investors are paying up for protection."
            readz = "焦虑上升——投资者为对冲付出更高成本。"
        else:
            tone, st, stz = "bad", "Fearful", "恐慌"
            read = "outright fear; elevated volatility typically coincides with market stress."
            readz = "明显恐慌；高波动通常伴随市场压力。"
        add("sentiment", {"en": "Investor Sentiment", "zh": "投资者情绪"}, tone, {"en": st, "zh": stz},
            {"en": f"VIX {v:.1f}", "zh": f"VIX {v:.1f}"},
            {"en": f"The VIX “fear gauge” at {v:.1f} reflects {read}",
             "zh": f"VIX「恐慌指数」为{v:.1f}，反映{readz}"},
            "Yahoo: ^VIX", _spark(vix, 3000))
    except Exception as e:  # noqa: BLE001
        print(f"  theme sentiment FAILED ({e})")

    # 3) Fiscal policy ------------------------------------------------------- #
    try:
        debt = cache.get("__debtgdp") or fetch_fred("GFDEGDQ188S")  # debt/GDP %
        defl = fetch_fred("MTSDS133FMS")  # monthly surplus/deficit, $M
        ttm = sum(v for _, v in defl[-12:]) / 1000  # -> $B, trailing 12m
        dg = debt[-1][1]
        add("fiscal", {"en": "Fiscal Policy", "zh": "财政政策"}, "warn",
            {"en": "Expansionary", "zh": "扩张性"},
            {"en": f"Debt {dg:.0f}% of GDP", "zh": f"债务占GDP {dg:.0f}%"},
            {"en": f"Federal debt is {dg:.0f}% of GDP with the Treasury running a ~${abs(ttm)/1000:.1f}T "
                   f"deficit over the trailing 12 months — fiscal stance remains loose/expansionary.",
             "zh": f"联邦债务占GDP的{dg:.0f}%，过去12个月财政赤字约{abs(ttm)/1000:.1f}万亿美元——财政立场仍然宽松/扩张。"},
            "FRED: Debt/GDP + Monthly Treasury Statement", _spark(debt, 400))
    except Exception as e:  # noqa: BLE001
        print(f"  theme fiscal FAILED ({e})")

    # 4) Geopolitics (GPR) --------------------------------------------------- #
    try:
        gpr = fetch_gpr()
        g = gpr[-1][1]
        easing = len(gpr) > 3 and gpr[-1][1] < gpr[-3][1]
        trend = "easing from recent highs" if easing else "rising"
        trendz = "较近期高点回落" if easing else "上升"
        if g < 80:
            tone, st, stz = "good", "Low", "低"
        elif g < 130:
            tone, st, stz = "good", "Normal", "正常"
        elif g < 180:
            tone, st, stz = "warn", "Elevated", "偏高"
        else:
            tone, st, stz = "bad", "High", "高"
        add("geopolitics", {"en": "Geopolitics", "zh": "地缘政治"}, tone, {"en": st, "zh": stz},
            {"en": f"GPR {g:.0f}", "zh": f"GPR {g:.0f}"},
            {"en": f"The Geopolitical Risk Index at {g:.0f} is {'above' if g > 100 else 'near'} its "
                   f"long-run average (~100), signalling {st.lower()} geopolitical tension ({trend}).",
             "zh": f"地缘政治风险指数为{g:.0f}，{'高于' if g > 100 else '接近'}其长期均值（约100），"
                   f"显示地缘政治紧张程度{stz}（{trendz}）。"},
            "Caldara & Iacoviello GPR Index", _spark(gpr, 360))
    except Exception as e:  # noqa: BLE001
        print(f"  theme geopolitics FAILED ({e}); skipping (needs 'pip install xlrd')")

    # 5) Tech / AI (semiconductor proxy) ------------------------------------ #
    try:
        sox = fetch_yahoo("%5ESOX", rng="10y")
        chg = _pct(sox, 252)  # ~1y (252 trading days) return
        if chg > 15:
            tone, st, stz = "good", "Booming", "强劲"
            read = "a powerful AI/compute capex cycle driving the group higher."
            readz = "强劲的AI/算力资本开支周期推动板块走高。"
        elif chg > 0:
            tone, st, stz = "good", "Expanding", "扩张"
            read = "steady gains as the AI investment cycle broadens."
            readz = "AI投资周期扩散，稳步上涨。"
        else:
            tone, st, stz = "warn", "Cooling", "降温"
            read = "a pullback in the AI/semiconductor trade after its run."
            readz = "AI/半导体交易在大涨后回调。"
        add("techai", {"en": "Tech / AI", "zh": "科技 / 人工智能"}, tone, {"en": st, "zh": stz},
            {"en": f"Semis {chg:+.0f}% / yr", "zh": f"半导体 同比{chg:+.0f}%"},
            {"en": f"The Philadelphia Semiconductor Index (a market proxy for the AI/compute cycle) is "
                   f"{chg:+.0f}% over the past year — {read}",
             "zh": f"费城半导体指数（AI/算力周期的市场代理）过去一年{chg:+.0f}%——{readz}"},
            "Yahoo: ^SOX (PHLX Semiconductor)", _spark(sox, 3000))
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

    def BULL(en, zh):
        bulls.append({"en": en, "zh": zh})

    def RISK(en, zh):
        risks.append({"en": en, "zh": zh})

    if wei is not None:
        if wei >= 2:
            score += 1; BULL(f"Economy expanding at/above trend (WEI ≈ {wei:.1f}% real growth)", f"经济增长达到或高于趋势水平（WEI≈{wei:.1f}%实际增长）")
        elif wei < 0:
            score -= 1; RISK(f"Activity contracting (WEI {wei:.1f}%)", f"经济活动萎缩（WEI {wei:.1f}%）")
    if unrate is not None:
        if unrate < 4.5 and unrate_chg < 0.3:
            score += 1; BULL(f"Labor market resilient (unemployment {unrate:.1f}%, {unrate_chg:+.1f} pts y/y)", f"劳动力市场稳健（失业率{unrate:.1f}%，同比{unrate_chg:+.1f}个百分点）")
        elif unrate_chg > 0.5:
            score -= 1; RISK(f"Unemployment rising ({unrate:.1f}%, {unrate_chg:+.1f} pts y/y)", f"失业率上升（{unrate:.1f}%，同比{unrate_chg:+.1f}个百分点）")
    if pce is not None:
        if pce <= 2.3:
            score += 1; BULL(f"Inflation near the Fed's 2% target (PCE {pce:.1f}%)", f"通胀接近美联储2%目标（PCE {pce:.1f}%）")
        elif pce > 3 and pce_accel > 0:
            score -= 1; RISK(f"Inflation sticky / re-accelerating (PCE {pce:.1f}%, {pce_accel:+.1f} pts y/y)", f"通胀粘性/重新加速（PCE {pce:.1f}%，同比{pce_accel:+.1f}个百分点）")
        else:
            RISK(f"Inflation still above the Fed's 2% goal (PCE {pce:.1f}%)", f"通胀仍高于美联储2%目标（PCE {pce:.1f}%）")
    if fed is not None:
        if fed_chg < -0.25:
            score += 1; BULL(f"Fed easing ({fed:.2f}% funds, {fed_chg:+.2f} pts y/y)", f"美联储正在宽松（利率{fed:.2f}%，同比{fed_chg:+.2f}个百分点）")
        elif fed_chg > 0.25:
            score -= 1; RISK(f"Fed tightening ({fed:.2f}% funds, {fed_chg:+.2f} pts y/y)", f"美联储正在紧缩（利率{fed:.2f}%，同比{fed_chg:+.2f}个百分点）")
    if ten is not None:
        if ten > 4.5:
            score -= 1; RISK(f"Elevated 10-year yield ({ten:.2f}%) lifts discount rates and competes with equities", f"10年期收益率偏高（{ten:.2f}%），推高贴现率并与股票争夺资金")
        elif ten < 3:
            score += 1; BULL(f"Low 10-year yield ({ten:.2f}%) supports valuations", f"10年期收益率较低（{ten:.2f}%），支撑估值")
    if hy:
        if hy < 350:
            score += 1; BULL(f"Credit conditions calm (high-yield spread {hy:.0f}bp)", f"信用状况平静（高收益债利差{hy:.0f}个基点）")
        elif hy > 550:
            score -= 1; RISK(f"Credit spreads widening ({hy:.0f}bp)", f"信用利差走阔（{hy:.0f}个基点）")
    if earn_yoy is not None:
        if earn_yoy > 5:
            score += 1; BULL(f"Corporate profits growing ({earn_yoy:+.0f}% y/y)", f"企业利润增长（同比{earn_yoy:+.0f}%）")
        elif earn_yoy < 0:
            score -= 1; RISK(f"Corporate profits falling ({earn_yoy:+.0f}% y/y)", f"企业利润下滑（同比{earn_yoy:+.0f}%）")
    if spx_yoy is not None:
        if spx_yoy > 8:
            score += 1; BULL(f"Strong price momentum (S&P 500 {spx_yoy:+.0f}% y/y)", f"价格动能强劲（标普500同比{spx_yoy:+.0f}%）")
        elif spx_yoy < 0:
            score -= 1; RISK(f"Negative price momentum (S&P 500 {spx_yoy:+.0f}% y/y)", f"价格动能为负（标普500同比{spx_yoy:+.0f}%）")
    if m2_yoy is not None:
        if m2_yoy > 3:
            score += 1; BULL(f"Money supply reflating (M2 {m2_yoy:+.1f}% y/y)", f"货币供应再通胀（M2同比{m2_yoy:+.1f}%）")
        elif m2_yoy < 0:
            score -= 1; RISK(f"Money supply contracting (M2 {m2_yoy:+.1f}% y/y)", f"货币供应收缩（M2同比{m2_yoy:+.1f}%）")
    if pe is not None and buff is not None:
        if pe > 25 or buff > 150:
            score -= 1; RISK(f"Valuations stretched (S&P P/E {pe:.0f}×, market-cap/GDP {buff:.0f}%) — a headwind to forward returns", f"估值偏高（标普市盈率{pe:.0f}倍，市值/GDP {buff:.0f}%）——对未来回报构成阻力")
        elif pe < 16:
            score += 1; BULL(f"Undemanding valuations (S&P P/E {pe:.0f}×)", f"估值不高（标普市盈率{pe:.0f}倍）")
    if vix is not None:
        if vix > 28:
            score -= 1; RISK(f"Elevated volatility (VIX {vix:.0f})", f"波动率偏高（VIX {vix:.0f}）")
        elif vix < 12:
            RISK(f"Very low volatility (VIX {vix:.0f}) hints at complacency", f"波动率极低（VIX {vix:.0f}）暗示自满")
        elif vix < 20:
            score += 1; BULL(f"Calm volatility backdrop (VIX {vix:.0f})", f"波动率平静（VIX {vix:.0f}）")
    if gpr is not None:
        if gpr > 150:
            score -= 1; RISK(f"Elevated geopolitical risk (GPR {gpr:.0f})", f"地缘政治风险偏高（GPR {gpr:.0f}）")
        elif gpr < 100:
            score += 1; BULL(f"Subdued geopolitical risk (GPR {gpr:.0f})", f"地缘政治风险温和（GPR {gpr:.0f}）")

    if score >= 4:
        stance, stancez, tone = "Constructive", "积极", "good"
    elif score >= 2:
        stance, stancez, tone = "Cautiously constructive", "谨慎乐观", "good"
    elif score >= -1:
        stance, stancez, tone = "Balanced / mixed", "中性/分化", "warn"
    elif score >= -3:
        stance, stancez, tone = "Cautious", "谨慎", "warn"
    else:
        stance, stancez, tone = "Defensive", "防御", "bad"

    _wei, _un, _pce = nz(wei), nz(unrate), nz(pce)
    _fed, _ten, _pe, _buff = nz(fed), nz(ten), nz(pe), nz(buff)
    _spx, _spy, _earn = nz(spx), nz(spx_yoy), nz(earn_yoy)
    infl_desc = "above the Fed's 2% goal" if _pce > 2.3 else "near target"
    infl_descz = "高于美联储2%目标" if _pce > 2.3 else "接近目标"
    pol_desc = "easing" if fed_chg < -0.25 else ("tightening" if fed_chg > 0.25 else "on hold")
    pol_descz = "宽松" if fed_chg < -0.25 else ("紧缩" if fed_chg > 0.25 else "按兵不动")
    mom_desc = "in an uptrend" if _spy > 0 else "under pressure"
    mom_descz = "处于上升趋势" if _spy > 0 else "承压"
    val_desc = "historically rich" if _pe > 22 else "reasonable"
    val_descz = "处于历史高位" if _pe > 22 else "合理"
    credit_zh = "平静" if (hy and hy < 350) else "收紧"

    narrative_en = (
        f"The U.S. economy is growing near trend (WEI ≈ {_wei:.1f}%, unemployment {_un:.1f}%), while PCE "
        f"inflation at {_pce:.1f}% remains {infl_desc}. The Fed is {pol_desc} with policy rates at {_fed:.2f}% "
        f"and the 10-year Treasury at {_ten:.2f}%. Credit is {'calm' if hy and hy < 350 else 'tightening'} "
        f"(high-yield spread {hy:.0f}bp) and the S&P 500 is {mom_desc} ({_spy:+.0f}% y/y) — but equity "
        f"valuations are {val_desc} (P/E {_pe:.0f}×, market-cap/GDP {_buff:.0f}%), which historically caps "
        f"medium-term upside. Net read across {len(bulls)} tailwinds and {len(risks)} headwinds: {stance.lower()}."
    )
    narrative_zh = (
        f"美国经济增长接近趋势水平（WEI≈{_wei:.1f}%，失业率{_un:.1f}%），PCE通胀为{_pce:.1f}%，{infl_descz}。"
        f"美联储{pol_descz}，政策利率为{_fed:.2f}%，10年期国债收益率为{_ten:.2f}%。信用状况{credit_zh}"
        f"（高收益债利差{hy:.0f}个基点），标普500{mom_descz}（同比{_spy:+.0f}%）——但股票估值{val_descz}"
        f"（市盈率{_pe:.0f}倍，市值/GDP {_buff:.0f}%），这在历史上会限制中期上行空间。"
        f"综合{len(bulls)}项利好与{len(risks)}项利空：{stancez}。"
    )

    scenarios = [
        {"name": {"en": "Base case", "zh": "基准情形"}, "prob": "~55%", "tone": tone, "text": {
            "en": (f"{stance}. Solid growth, {pol_desc} policy and contained credit can keep equities grinding higher, "
                   f"but rich starting valuations (P/E {_pe:.0f}×) have historically coincided with below-average 12-month "
                   f"returns — expect modest, single-digit gains with above-average sensitivity to rate and earnings surprises."),
            "zh": (f"{stancez}。稳健增长、{pol_descz}的政策以及可控的信用环境可推动股市继续走高，但偏高的起始估值"
                   f"（市盈率{_pe:.0f}倍）在历史上往往对应低于平均的未来12个月回报——预计温和的个位数涨幅，"
                   f"且对利率和盈利意外的敏感度高于平均。")}},
        {"name": {"en": "Bull case", "zh": "乐观情形"}, "prob": "~25%", "tone": "good", "text": {
            "en": (f"If inflation resumes cooling toward 2%, the Fed eases further, and profits (currently {_earn:+.0f}% y/y) "
                   f"stay strong, multiples can hold and the index could deliver above-average, low-double-digit gains."),
            "zh": (f"若通胀重新向2%回落、美联储进一步宽松、且企业利润（目前同比{_earn:+.0f}%）保持强劲，"
                   f"估值倍数可维持，指数可能取得高于平均的低两位数涨幅。")}},
        {"name": {"en": "Bear case", "zh": "悲观情形"}, "prob": "~20%", "tone": "bad", "text": {
            "en": (f"A re-acceleration in inflation, a jump in credit spreads from today's {hy:.0f}bp, or an earnings/growth "
                   f"disappointment against stretched valuations (market-cap/GDP {_buff:.0f}%) could drive a double-digit drawdown."),
            "zh": (f"通胀重新加速、信用利差从当前{hy:.0f}个基点跳升，或在估值偏高（市值/GDP {_buff:.0f}%）背景下"
                   f"盈利/增长不及预期，都可能引发两位数的回撤。")}},
    ]

    metrics = [
        {"label": {"en": "Real growth (WEI)", "zh": "实际增长（WEI）"}, "value": f"{_wei:.1f}%"},
        {"label": {"en": "Unemployment", "zh": "失业率"}, "value": f"{_un:.1f}%"},
        {"label": {"en": "PCE inflation", "zh": "PCE通胀"}, "value": f"{_pce:.1f}%"},
        {"label": {"en": "Fed funds", "zh": "联邦基金利率"}, "value": f"{_fed:.2f}%"},
        {"label": {"en": "10Y Treasury", "zh": "10年期国债"}, "value": f"{_ten:.2f}%"},
        {"label": {"en": "HY credit spread", "zh": "高收益债利差"}, "value": f"{hy:.0f}bp"},
        {"label": {"en": "S&P 500", "zh": "标普500"}, "value": f"{_spx:,.0f}"},
        {"label": {"en": "S&P 500 P/E", "zh": "标普500市盈率"}, "value": f"{_pe:.1f}×"},
        {"label": {"en": "Buffett (cap/GDP)", "zh": "巴菲特指标（市值/GDP）"}, "value": f"{_buff:.0f}%"},
        {"label": {"en": "VIX", "zh": "VIX"}, "value": f"{nz(vix):.1f}"},
    ]

    disclaimer = {
        "en": ("Rules-based synthesis of the indicators below, generated from the data for informational purposes only — "
               "not investment advice, a recommendation, or a guarantee. Scenario probabilities are illustrative. Markets "
               "are uncertain and can deviate sharply from any data-driven base case."),
        "zh": ("基于以下指标、由数据自动生成的规则化综合分析，仅供参考——不构成任何投资建议、推荐或保证。"
               "情景概率仅为示意。市场充满不确定性，实际走势可能与任何数据驱动的基准情形大相径庭。"),
    }

    return {
        "stance": {"en": stance, "zh": stancez}, "tone": tone, "score": score,
        "narrative": {"en": narrative_en, "zh": narrative_zh}, "metrics": metrics,
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
                "title": {"en": title, "zh": TITLE_ZH.get(key, title)},
                "section": {"en": section, "zh": SECTION_ZH.get(section, section)},
                "unit": unit,
                "fmt": fmt,
                "source": src_label,
                "data": series,
                "summary": summarize(key, series),
            }
        )
        time.sleep(0.8)  # be polite / avoid burst rate-limiting

    # Quality gate: never overwrite the last good dashboard with a degraded one.
    # A healthy run loads all 18; a data-source outage loads only a handful.
    ok = sum(1 for i in indicators if i["data"])
    MIN_OK = 15
    if ok < MIN_OK:
        print(f"\n  DEGRADED BUILD: only {ok}/{len(indicators)} indicators loaded "
              f"(need >= {MIN_OK}) — likely a temporary data-source outage.")
        print("  Aborting WITHOUT writing dashboard.html so the last good version is preserved.")
        sys.exit(2)

    print("\n  building macro theme tiles ...")
    themes = build_themes(cache)
    print(f"  {len(themes)}/5 theme tiles built.")

    print("  building executive summary & outlook ...")
    outlook = build_outlook(indicators, themes)
    print(f"  stance: {outlook['stance']['en']} (score {outlook['score']:+d})")

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
  .langsel{margin-left:auto;align-self:center;display:flex;border:1px solid var(--border);
       border-radius:8px;overflow:hidden}
  .langsel button{background:var(--panel2);color:var(--muted);border:0;padding:6px 12px;
       cursor:pointer;font-size:12px;font-weight:700}
  .langsel button.active{background:var(--accent);color:#04101f}
  .rangebar{display:flex;align-items:center;gap:12px;margin:2px 0}
  .rangebar .controls{margin-left:0}
  .rblabel{font-size:11px;text-transform:uppercase;letter-spacing:.6px;color:var(--muted);font-weight:700}
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
  <h1 id="title">📊 Macro Economic Dashboard</h1>
  <span class="sub" id="updated"></span>
  <div class="langsel" id="langsel">
    <button data-lang="en">EN</button>
    <button data-lang="zh">中文</button>
  </div>
</header>
<div id="app"></div>
<footer id="footer"></footer>

<script>
const PAYLOAD = /*__DATA__*/;
let RANGE = 0; // days, 0 = max
const charts = {};

// --- i18n -------------------------------------------------------------- //
let LANG = (localStorage.getItem("dashLang") === "zh") ? "zh" : "en";
function T(x){ return (x && typeof x === "object" && !Array.isArray(x) && ("en" in x || "zh" in x))
  ? (x[LANG] != null ? x[LANG] : x.en) : x; }
const UI = {
  en: { title:"📊 Macro Economic Dashboard", updated:"Updated", live:"series live",
        timeRange:"Time range", themes:"Macro Themes — Current State",
        exec:"Executive Summary & 12-Month Outlook", outlookTitle:"U.S. Stock Market — 12-Month Outlook",
        score:"net factor score", tailwinds:"Tailwinds", headwinds:"Headwinds",
        unavailable:"data unavailable", since:"since", pageTitle:"Macro Economic Dashboard",
        footer:"Data: FRED (St. Louis Fed), Yahoo Finance, and multpl.com. This analysis is for informational purposes only and does not constitute any investment advice. AI can make mistakes. Always verify the information." },
  zh: { title:"📊 宏观经济仪表盘", updated:"更新于", live:"项数据在线",
        timeRange:"时间范围", themes:"宏观主题 — 当前状态",
        exec:"执行摘要与12个月展望", outlookTitle:"美国股市 — 12个月展望",
        score:"净因子得分", tailwinds:"利好因素", headwinds:"利空因素",
        unavailable:"暂无数据", since:"自", pageTitle:"宏观经济仪表盘",
        footer:"数据来源：FRED（圣路易斯联储）、Yahoo Finance 与 multpl.com。本分析仅供参考，不构成任何投资建议。AI 可能出错，请务必自行核实信息。" },
};
function U(k){ return UI[LANG][k]; }
const RANGES = [{r:365,en:"1Y",zh:"1年"},{r:1825,en:"5Y",zh:"5年"},
                {r:3650,en:"10Y",zh:"10年"},{r:0,en:"Max",zh:"全部"}];

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
  document.documentElement.lang = (LANG === "zh") ? "zh-CN" : "en";
  document.title = U("pageTitle");
  document.getElementById("title").textContent = U("title");
  document.getElementById("footer").textContent = U("footer");
  document.querySelectorAll("#langsel button").forEach(b =>
    b.classList.toggle("active", b.dataset.lang === LANG));

  const app = document.getElementById("app");
  app.innerHTML = "";
  const live = PAYLOAD.indicators.filter(i=>i.data.length).length + (PAYLOAD.themes||[]).length;
  const total = PAYLOAD.indicators.length + (PAYLOAD.themes||[]).length;
  document.getElementById("updated").textContent =
    U("updated") + " " + PAYLOAD.generated + "  •  " + live + "/" + total + " " + U("live");

  renderOutlook();
  renderRangeBar();
  renderThemes();

  const seen = new Set(), sections = [];
  PAYLOAD.indicators.forEach(i => {
    const en = (i.section && i.section.en) || i.section;
    if(!seen.has(en)){ seen.add(en); sections.push(i.section); }
  });

  sections.forEach(sec => {
    const en = (sec && sec.en) || sec;
    const wrap = document.createElement("div");
    wrap.className = "section";
    wrap.innerHTML = `<h2>${T(sec)}</h2><div class="grid"></div>`;
    const grid = wrap.querySelector(".grid");
    PAYLOAD.indicators.filter(i => ((i.section && i.section.en) || i.section) === en)
      .forEach(ind => grid.appendChild(card(ind)));
    app.appendChild(wrap);
  });
  PAYLOAD.indicators.forEach(drawChart);
}

function renderRangeBar(){
  const app = document.getElementById("app");
  const bar = document.createElement("div");
  bar.className = "section";
  bar.innerHTML = `
    <div class="rangebar">
      <span class="rblabel">${U("timeRange")}</span>
      <div class="controls" id="ranges">
        ${RANGES.map(x=>`<button data-r="${x.r}">${x[LANG]}</button>`).join("")}
      </div>
    </div>`;
  bar.querySelectorAll("#ranges button").forEach(b=>{
    if(+b.dataset.r === RANGE) b.classList.add("active");
  });
  app.appendChild(bar);
}

function renderOutlook(){
  const o = PAYLOAD.outlook;
  if(!o) return;
  const app = document.getElementById("app");
  const wrap = document.createElement("div");
  wrap.className = "section";
  wrap.innerHTML = `
    <h2>${U("exec")}</h2>
    <div class="outlook">
      <div class="ohead">
        <div class="otitle">${U("outlookTitle")}</div>
        <div class="stance ${o.tone}">${T(o.stance)}</div>
        <div class="oscore">${U("score")} ${o.score>0?"+":""}${o.score}</div>
      </div>
      <div class="narr">${T(o.narrative)}</div>
      <div class="metrics-strip">${o.metrics.map(m=>
        `<div class="mchip"><div class="ml">${T(m.label)}</div><div class="mv">${m.value}</div></div>`).join("")}</div>
      <div class="ocols">
        <div class="ocol bull"><h4>${U("tailwinds")}</h4><ul>${o.bulls.map(b=>`<li>${T(b)}</li>`).join("")}</ul></div>
        <div class="ocol risk"><h4>${U("headwinds")}</h4><ul>${o.risks.map(r=>`<li>${T(r)}</li>`).join("")}</ul></div>
      </div>
      <div class="scenarios">${o.scenarios.map(s=>
        `<div class="scen ${s.tone}"><div class="sname"><span>${T(s.name)}</span><span>${s.prob}</span></div>
         <div class="stext">${T(s.text)}</div></div>`).join("")}</div>
      <div class="odisc">${T(o.disclaimer)}</div>
    </div>`;
  app.appendChild(wrap);
}

function renderThemes(){
  const themes = PAYLOAD.themes || [];
  if(!themes.length) return;
  const app = document.getElementById("app");
  const wrap = document.createElement("div");
  wrap.className = "section";
  wrap.innerHTML = `<h2>${U("themes")}</h2><div class="themes"></div>`;
  const grid = wrap.querySelector(".themes");
  themes.forEach(t => {
    const el = document.createElement("div");
    el.className = "tile " + t.tone;
    el.innerHTML = `
      <div class="thead"><div class="ttitle">${T(t.title)}</div>
        <div class="badge">${T(t.status)}</div></div>
      <div class="metric">${T(t.metric)}</div>
      <div class="summary">${T(t.summary)}</div>
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
    el.innerHTML = `<div class="top"><div class="title">${T(ind.title)}</div></div>
      <div class="val">${U("unavailable")}</div>
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
        <div class="title">${T(ind.title)}</div>
        <div class="val">${fmtNum(last, ind.fmt)}</div>
      </div>
      <div class="chg ${up?'up':'down'}">${arrow} ${fmtNum(Math.abs(pct),",.1f")}%</div>
    </div>
    ${T(ind.summary) ? `<div class="csum">${T(ind.summary)}</div>` : ""}
    <div class="chartwrap"><canvas id="c_${ind.key}"></canvas></div>
    <div class="foot"><span>${ind.source}</span><span>${U("since")} ${sl[0][0]}</span></div>`;
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

document.addEventListener("click", e=>{
  const btn = e.target.closest("#ranges button");
  if(!btn) return;
  RANGE = +btn.dataset.r;
  render();
});

document.getElementById("langsel").addEventListener("click", e=>{
  const b = e.target.closest("button[data-lang]");
  if(!b) return;
  LANG = b.dataset.lang;
  localStorage.setItem("dashLang", LANG);
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

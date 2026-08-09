# Macro Economic Dashboard

A self-contained dashboard tracking 18 key macro indicators. No API keys required.

## Use

```powershell
python build_dashboard.py   # fetch latest data + regenerate dashboard.html
```

Then open `dashboard.html` in any browser. The file is fully self-contained
(data is embedded), so you can email it or open it offline. Use the
1Y / 5Y / 10Y / Max buttons to change the time window.

Re-run the script any time to refresh with the newest data.

## Indicators & sources

| # | Indicator | Source |
|---|-----------|--------|
| 1 | U.S. GDP | FRED `GDP` |
| 2 | Corporate Earnings (Profits) | FRED `CP` |
| 3 | Unemployment Rate | FRED `UNRATE` |
| 4 | PCE Inflation (YoY) | FRED `PCEPI`, computed YoY |
| 5 | Fed Funds Rate | FRED `FEDFUNDS` |
| 6 | 10-Year Treasury Yield | FRED `DGS10` (Yahoo `^TNX` fallback) |
| 7 | M2 Money Supply | FRED `M2SL` |
| 8 | Fed Balance Sheet | FRED `WALCL` |
| 9 | U.S. Dollar Index (DXY) | Yahoo `DX-Y.NYB` |
| 10 | S&P 500 P/E Ratio | multpl.com |
| 11 | U.S. Stock Market Value | FRED `NCBEILQ027S` (corporate equities) |
| 12 | Market Cap / GDP (Buffett) | computed: equities ÷ GDP |
| 13 | S&P 500 | Yahoo `^GSPC` |
| 14 | Weekly Economic Index | FRED `WEI` |
| 15 | Crude Oil (WTI) | FRED `DCOILWTICO` (Yahoo `CL=F` fallback) |
| 16 | Gold | Yahoo `GC=F` |
| 17 | Bitcoin | Yahoo `BTC-USD` |
| 18 | U.S. House Price (Case-Shiller) | FRED `CSUSHPINSA` |

## Macro Theme tiles

A "Macro Themes — Current State" row sits at the top with five qualitative-but-
data-anchored tiles. The status word and summary sentence are generated from the
live numbers on each build (no hand-written prose), so they stay current:

| Tile | Live source | Status logic |
|------|-------------|--------------|
| Credit Spreads | FRED `BAMLH0A0HYM2` (HY OAS) + `BAMLC0A0CM` (IG OAS) | spread level in bps |
| Investor Sentiment | Yahoo `^VIX` | VIX level (complacent → fearful) |
| Fiscal Policy | FRED `GFDEGDQ188S` (debt/GDP) + `MTSDS133FMS` (TTM deficit) | debt burden + deficit |
| Geopolitics | Caldara & Iacoviello GPR Index (`.xls`) | GPR vs ~100 average |
| Tech / AI | Yahoo `^SOX` (PHLX Semiconductor) | 1-year return as AI-cycle proxy |

The Geopolitics tile needs the `xlrd` package to read the GPR Excel file:

```powershell
pip install xlrd
```

If `xlrd` isn't installed (or the source is unreachable), that one tile is
skipped and the rest still build.

## Notes

- **Market series** (DXY, S&P 500, Gold, Bitcoin, and the two fallbacks) are
  pulled from Yahoo Finance over a 10-year daily window. FRED macro series carry
  their full history.
- **Buffett Indicator** uses the Fed Z.1 "Corporate Equities" series divided by
  GDP (the Wilshire 5000 series was discontinued on FRED). Currently ~218%.
- Each fetch is isolated — if one source is temporarily down, the rest still
  build and that card shows "data unavailable".
- For analysis only; not investment advice.

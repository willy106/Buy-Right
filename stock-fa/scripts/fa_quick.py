#!/usr/bin/env python3
"""快速基本面快照（1 支或多支；yfinance info + 台股 FinMind 月營收）。

用法:
  python fa_quick.py 2330
  python fa_quick.py 2330 2454 3711            # 同業並排
  python fa_quick.py NVDA AMD --json out.json
所有數字標「來源」，讓報告能標示【事實】/【推估】。
"""
import argparse
from datetime import date, timedelta
import pandas as pd
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "stock-common"))   # 共用資料層
from stockdata import normalize, tw_code, yf_info, finmind, dump


def _pct(v):
    return None if v is None or pd.isna(v) else round(float(v) * 100, 1)


def _num(v, nd=2):
    return None if v is None or pd.isna(v) else round(float(v), nd)


def tw_revenue(code: str) -> dict:
    start = date(date.today().year - 1, 1, 1).isoformat()  # 要涵蓋去年 1 月起，YTD YoY 才比得到同期
    r = finmind("TaiwanStockMonthRevenue", code, start)
    if not len(r):
        return {}
    r = r.sort_values(["revenue_year", "revenue_month"])
    r["ym"] = r["revenue_year"].astype(str) + "-" + r["revenue_month"].astype(str).str.zfill(2)
    last = r.iloc[-1]
    yoy = r[(r.revenue_year == last.revenue_year - 1) & (r.revenue_month == last.revenue_month)]
    prev = r.iloc[-2] if len(r) > 1 else None
    ytd = r[r.revenue_year == last.revenue_year]["revenue"].sum()
    ly = r[(r.revenue_year == last.revenue_year - 1) & (r.revenue_month <= last.revenue_month)]
    ytd_ly = ly["revenue"].sum() if len(ly) == last.revenue_month else None  # 去年同期月份不齊就不算
    return {
        "最新月份": last.ym,
        "月營收_億": round(float(last.revenue) / 1e8, 2),
        "YoY_pct": _pct(last.revenue / yoy.revenue.iloc[0] - 1) if len(yoy) else None,
        "MoM_pct": _pct(last.revenue / prev.revenue - 1) if prev is not None else None,
        "YTD_YoY_pct": _pct(ytd / ytd_ly - 1) if ytd_ly else None,
        "近12月_億": [round(float(v) / 1e8, 2) for v in r["revenue"].tail(12)],
        "source": "FinMind TaiwanStockMonthRevenue（公司 MOPS 申報）",
    }


def _yield(i: dict):
    """yfinance 不同版本 dividendYield 有時是 0.0123、有時是 1.23；用 dividendRate/price 優先。"""
    px = i.get("currentPrice") or i.get("regularMarketPrice")
    if i.get("dividendRate") and px:
        return round(float(i["dividendRate"]) / float(px) * 100, 2)
    y = i.get("dividendYield")
    if y is None:
        return None
    return round(float(y) * 100, 2) if y < 0.3 else round(float(y), 2)


def snapshot(ticker: str) -> dict:
    sym, mkt = normalize(ticker)
    i = yf_info(sym)
    g = i.get
    out = {
        "symbol": sym, "name": g("longName") or g("shortName"), "sector": g("sector"), "industry": g("industry"),
        "price": _num(g("currentPrice") or g("regularMarketPrice")),
        "market_cap_bn": _num((g("marketCap") or 0) / 1e9, 1),
        "valuation": {
            "PE_trailing": _num(g("trailingPE")), "PE_forward": _num(g("forwardPE")),
            "PB": _num(g("priceToBook")), "PS_ttm": _num(g("priceToSalesTrailing12Months")),
            "EV_EBITDA": _num(g("enterpriseToEbitda")), "PEG": _num(g("pegRatio")),
            "dividend_yield_pct": _yield(i),
        },
        "profitability": {
            "gross_margin_pct": _pct(g("grossMargins")), "op_margin_pct": _pct(g("operatingMargins")),
            "net_margin_pct": _pct(g("profitMargins")), "ROE_pct": _pct(g("returnOnEquity")), "ROA_pct": _pct(g("returnOnAssets")),
        },
        "growth": {"revenue_yoy_pct": _pct(g("revenueGrowth")), "eps_yoy_pct": _pct(g("earningsGrowth"))},
        "balance": {"debt_to_equity": _num(g("debtToEquity")), "current_ratio": _num(g("currentRatio")),
                    "cash_bn": _num((g("totalCash") or 0) / 1e9, 2), "debt_bn": _num((g("totalDebt") or 0) / 1e9, 2),
                    "FCF_bn": _num((g("freeCashflow") or 0) / 1e9, 2)},
        "eps_ttm": _num(g("trailingEps")), "eps_fwd": _num(g("forwardEps")),
        "analyst": {"n": g("numberOfAnalystOpinions"), "target_mean": _num(g("targetMeanPrice")), "rec": g("recommendationKey")},
        "source": "yfinance .info（Yahoo Finance 彙整，非公司原始申報；台股 EPS/ROE 以此為【推估】級，正式數據以 MOPS 為準）",
    }
    if mkt == "TW":
        out["monthly_revenue"] = tw_revenue(tw_code(sym))
    return out


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("tickers", nargs="+")
    ap.add_argument("--json")
    a = ap.parse_args()
    res = [snapshot(t) for t in a.tickers]
    dump(res if len(res) > 1 else res[0], a.json)

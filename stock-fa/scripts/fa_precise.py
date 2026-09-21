#!/usr/bin/env python3
"""精準基本面（單支 + 可選同業）：8 季三表趨勢、DuPont、盈餘品質、本益比河流圖、台股股利與法人級數據。

用法:
  python fa_precise.py 2330 --out ./2330_fa
  python fa_precise.py 2454 --peers 2330 3711 --out ./2454_fa
  python fa_precise.py NVDA --peers AMD AVGO --out ./nvda_fa
輸出: <out>.json、<out>_pe_band.png、<out>_trend.png
"""
import argparse
from datetime import date, timedelta
import numpy as np
import pandas as pd
import yfinance as yf
from data import normalize, tw_code, finmind, fetch_ohlcv, cached, dump
from fa_quick import snapshot as quick_snapshot


def _row(df: pd.DataFrame, *names):
    for n in names:
        if n in df.index:
            return df.loc[n]
    return pd.Series(dtype=float)


def statements(sym: str) -> dict:
    t = yf.Ticker(sym)
    q_is = cached(f"qis:{sym}", 86400, lambda: t.quarterly_financials)
    q_bs = cached(f"qbs:{sym}", 86400, lambda: t.quarterly_balance_sheet)
    q_cf = cached(f"qcf:{sym}", 86400, lambda: t.quarterly_cashflow)
    if q_is is None or q_is.empty:
        return {}
    cols = sorted(q_is.columns)[-8:]
    rev = _row(q_is, "Total Revenue", "Operating Revenue").reindex(cols)
    gp = _row(q_is, "Gross Profit").reindex(cols)
    op = _row(q_is, "Operating Income").reindex(cols)
    ni = _row(q_is, "Net Income", "Net Income Common Stockholders").reindex(cols)
    eps = _row(q_is, "Diluted EPS", "Basic EPS").reindex(cols)
    ocf = _row(q_cf, "Operating Cash Flow").reindex(cols)
    capex = _row(q_cf, "Capital Expenditure").reindex(cols)
    eq = _row(q_bs, "Stockholders Equity", "Total Equity Gross Minority Interest").reindex(cols)
    ta = _row(q_bs, "Total Assets").reindex(cols)
    debt = _row(q_bs, "Total Debt").reindex(cols)
    cash = _row(q_bs, "Cash And Cash Equivalents", "Cash Cash Equivalents And Short Term Investments").reindex(cols)
    inv = _row(q_bs, "Inventory").reindex(cols)
    ar = _row(q_bs, "Accounts Receivable", "Receivables").reindex(cols)

    def s(x, div=1e6, nd=1):
        return [None if pd.isna(v) else round(float(v) / div, nd) for v in x]

    def yoy(x):
        return [None if i < 4 or pd.isna(x.iloc[i]) or pd.isna(x.iloc[i - 4]) or x.iloc[i - 4] == 0
                else round(float(x.iloc[i] / x.iloc[i - 4] - 1) * 100, 1) for i in range(len(x))]

    ttm_ni, ttm_eq, ttm_ta, ttm_rev = ni.tail(4).sum(), eq.tail(4).mean(), ta.tail(4).mean(), rev.tail(4).sum()
    dupont = {
        "net_margin_pct": round(float(ttm_ni / ttm_rev * 100), 2) if ttm_rev else None,
        "asset_turnover": round(float(ttm_rev / ttm_ta), 3) if ttm_ta else None,
        "equity_multiplier": round(float(ttm_ta / ttm_eq), 2) if ttm_eq else None,
        "ROE_ttm_pct": round(float(ttm_ni / ttm_eq * 100), 2) if ttm_eq else None,
    }
    fcf = ocf + capex
    quality = {
        "OCF/NI_ttm": round(float(ocf.tail(4).sum() / ttm_ni), 2) if ttm_ni else None,
        "FCF_ttm_M": round(float(fcf.tail(4).sum() / 1e6), 1) if len(fcf.dropna()) else None,
        "net_cash_M": round(float((cash.iloc[-1] - debt.iloc[-1]) / 1e6), 1) if len(cash.dropna()) and len(debt.dropna()) else None,
        "inventory_days": round(float(inv.iloc[-1] / (rev.tail(4).sum() - gp.tail(4).sum()) * 365), 0) if len(inv.dropna()) and (rev.tail(4).sum() - gp.tail(4).sum()) else None,
        "receivable_days": round(float(ar.iloc[-1] / ttm_rev * 365), 0) if len(ar.dropna()) and ttm_rev else None,
        "inventory_yoy_vs_revenue_yoy": None,
    }
    if len(inv.dropna()) >= 5 and inv.iloc[-5] and rev.iloc[-5]:
        quality["inventory_yoy_vs_revenue_yoy"] = [round(float(inv.iloc[-1] / inv.iloc[-5] - 1) * 100, 1),
                                                   round(float(rev.iloc[-1] / rev.iloc[-5] - 1) * 100, 1)]
    return {
        "quarters": [str(c.date()) for c in cols],
        "revenue_M": s(rev), "revenue_yoy_pct": yoy(rev),
        "gross_margin_pct": [None if pd.isna(a) or pd.isna(b) or not b else round(float(a / b * 100), 1) for a, b in zip(gp, rev)],
        "op_margin_pct": [None if pd.isna(a) or pd.isna(b) or not b else round(float(a / b * 100), 1) for a, b in zip(op, rev)],
        "net_income_M": s(ni), "eps": s(eps, 1, 2), "eps_yoy_pct": yoy(eps),
        "OCF_M": s(ocf), "FCF_M": s(fcf),
        "dupont_ttm": dupont, "earnings_quality": quality,
        "eps_ttm": round(float(eps.tail(4).sum()), 2) if len(eps.dropna()) >= 4 else None,
        "source": "yfinance quarterly statements（Yahoo 整理，幣別為公司申報幣別；台股為新台幣）",
    }


# ---------------------------------------------------------------- 台股：FinMind 取代 yfinance（資料更長、口徑為 MOPS 申報）
def tw_statements(code: str, quarters: int = 8) -> dict:
    """FinMind TaiwanStockFinancialStatements（單季、元）。抓不到回 {}，呼叫端退回 yfinance。"""
    start = (date.today() - timedelta(days=365 * 3 + 30)).isoformat()
    fs = finmind("TaiwanStockFinancialStatements", code, start)
    if fs.empty or "type" not in fs:
        return {}
    piv = fs.pivot_table(index="date", columns="type", values="value", aggfunc="first").sort_index()

    def col(*keys):
        for k in keys:
            if k in piv.columns:
                return piv[k]
        for c in piv.columns:  # 寬鬆比對
            if any(k.lower() in c.lower() for k in keys):
                return piv[c]
        return pd.Series(index=piv.index, dtype=float)

    rev, gp, op = col("Revenue"), col("GrossProfit"), col("OperatingIncome")
    ni, eps = col("IncomeAfterTaxes", "NetIncome", "TotalConsolidatedProfitForThePeriod"), col("EPS")
    if rev.dropna().empty:
        return {}
    idx = rev.dropna().index[-quarters:]
    rev, gp, op, ni, eps = [x.reindex(idx) for x in (rev, gp, op, ni, eps)]

    def s(x, div=1e6, nd=1): return [None if pd.isna(v) else round(float(v) / div, nd) for v in x]
    def yoy(x): return [None if i < 4 or pd.isna(x.iloc[i]) or pd.isna(x.iloc[i - 4]) or x.iloc[i - 4] == 0
                        else round(float(x.iloc[i] / x.iloc[i - 4] - 1) * 100, 1) for i in range(len(x))]
    def ratio(a, b): return [None if pd.isna(x) or pd.isna(y) or not y else round(float(x / y * 100), 1) for x, y in zip(a, b)]
    return {
        "quarters": [str(pd.Timestamp(i).date()) for i in idx],
        "revenue_M": s(rev), "revenue_yoy_pct": yoy(rev),
        "gross_margin_pct": ratio(gp, rev), "op_margin_pct": ratio(op, rev),
        "net_income_M": s(ni), "eps": s(eps, 1, 2), "eps_yoy_pct": yoy(eps),
        "eps_ttm": round(float(eps.dropna().tail(4).sum()), 2) if len(eps.dropna()) >= 4 else None,
        "source": "FinMind TaiwanStockFinancialStatements（MOPS 單季申報，【事實】）",
    }


def tw_pe_band(code: str, out_png: str) -> dict:
    """用交易所每日 PER（FinMind TaiwanStockPER）畫 5 年本益比區間，不需自算 EPS。"""
    start = (date.today() - timedelta(days=365 * 5)).isoformat()
    per = finmind("TaiwanStockPER", code, start)
    if per.empty or "PER" not in per:
        return {}
    per = per.sort_values("date")
    pe = pd.Series(per["PER"].astype(float).values, index=per["date"])
    pe = pe[(pe > 0) & (pe < 200)]
    if pe.empty:
        return {}
    q = pe.quantile([0.1, 0.25, 0.5, 0.75, 0.9]).round(1)
    cur = float(pe.iloc[-1])
    import matplotlib; matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    fig, ax = plt.subplots(figsize=(12, 5))
    ax.plot(pe.index, pe.values, color="k", lw=1, label="PER (TWSE)")
    for k, v in q.items():
        ax.axhline(v, lw=0.8, ls="--", label=f"p{int(k*100)} {v}x")
    ax.set_title(f"{code} PER 5y  current {cur:.1f}x  percentile {int((pe < cur).mean()*100)}%")
    ax.legend(fontsize=8, ncol=3); ax.grid(alpha=0.3)
    fig.savefig(out_png, dpi=130, bbox_inches="tight"); plt.close(fig)
    print(f"[chart] {out_png}")
    return {"current_PE": round(cur, 1), "percentile_pct": int((pe < cur).mean() * 100),
            "quantiles": {f"p{int(k*100)}": float(v) for k, v in q.items()},
            "PBR_current": float(per["PBR"].iloc[-1]) if "PBR" in per else None,
            "note": "交易所公布之 PER（近四季 EPS），【事實】"}


def pe_band(sym: str, eps_ttm: float | None, out_png: str, years="5y") -> dict:
    """本益比河流圖：用歷史 EPS(TTM) 序列近似（以季 EPS 累加），無季資料時退化為固定 EPS。"""
    px, _ = fetch_ohlcv(sym, years)
    t = yf.Ticker(sym)
    q_is = cached(f"qis:{sym}", 86400, lambda: t.quarterly_financials)
    eps_q = _row(q_is, "Diluted EPS", "Basic EPS").sort_index() if q_is is not None and not q_is.empty else pd.Series(dtype=float)
    if len(eps_q) >= 4:
        ttm = eps_q.rolling(4).sum().dropna()
        ttm.index = pd.to_datetime(ttm.index)
        eps_series = ttm.reindex(px.index, method="ffill").bfill()
    elif eps_ttm:
        eps_series = pd.Series(eps_ttm, index=px.index)
    else:
        return {}
    pe = (px["Close"] / eps_series.replace(0, np.nan)).dropna()
    pe = pe[(pe > 0) & (pe < 200)]
    if pe.empty:
        return {}
    q = pe.quantile([0.1, 0.25, 0.5, 0.75, 0.9]).round(1)
    cur = float(pe.iloc[-1])
    import matplotlib; matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    fig, ax = plt.subplots(figsize=(12, 6))
    for lvl in q.values:
        ax.plot(px.index, eps_series.reindex(px.index) * lvl, lw=0.8, label=f"PE {lvl}x")
    ax.plot(px.index, px["Close"], color="k", lw=1.2, label="Close")
    ax.set_title(f"{sym} PE band (current {cur:.1f}x, percentile {int((pe < cur).mean()*100)}%)")
    ax.legend(fontsize=8); ax.grid(alpha=0.3)
    fig.savefig(out_png, dpi=130, bbox_inches="tight"); plt.close(fig)
    print(f"[chart] {out_png}")
    return {"current_PE": round(cur, 1), "percentile_pct": int((pe < cur).mean() * 100),
            "quantiles": {f"p{int(k*100)}": float(v) for k, v in q.items()},
            "note": "EPS 用 Yahoo 季 EPS 滾動 4 季，屬【推估】；正式 PE 以交易所公布為準"}


def trend_chart(st: dict, sym: str, out_png: str):
    if not st:
        return
    import matplotlib; matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    q = st["quarters"]
    fig, ax = plt.subplots(2, 1, figsize=(11, 8), sharex=True)
    ax[0].bar(q, [v or 0 for v in st["revenue_M"]], color="steelblue"); ax[0].set_ylabel("Revenue (M)")
    ax2 = ax[0].twinx(); ax2.plot(q, [v if v is not None else np.nan for v in st["revenue_yoy_pct"]], "r-o", ms=3); ax2.set_ylabel("YoY %")
    ax[1].plot(q, [v if v is not None else np.nan for v in st["gross_margin_pct"]], "-o", ms=3, label="GM%")
    ax[1].plot(q, [v if v is not None else np.nan for v in st["op_margin_pct"]], "-o", ms=3, label="OPM%")
    ax[1].legend(); ax[1].set_ylabel("%"); ax[1].grid(alpha=0.3)
    plt.setp(ax[1].get_xticklabels(), rotation=45); fig.suptitle(f"{sym} 8Q trend")
    fig.savefig(out_png, dpi=130, bbox_inches="tight"); plt.close(fig)
    print(f"[chart] {out_png}")


def tw_extras(code: str) -> dict:
    start = (date.today() - timedelta(days=5 * 365)).isoformat()
    out = {}
    dv = finmind("TaiwanStockDividend", code, start)
    if len(dv):
        dv = dv.sort_values("date")
        cash_col = "CashEarningsDistribution" if "CashEarningsDistribution" in dv else None
        if cash_col:
            out["cash_dividend_by_year"] = dv.groupby("year")[cash_col].sum().round(2).tail(5).to_dict()
    per = finmind("TaiwanStockPER", code, (date.today() - timedelta(days=400)).isoformat())
    if len(per):
        p = per.sort_values("date").iloc[-1]
        out["exchange_PER_PBR"] = {"PER": float(p["PER"]), "PBR": float(p["PBR"]), "殖利率_pct": float(p["dividend_yield"]),
                                   "date": str(p["date"].date()), "source": "TWSE 每日公布（【事實】）"}
    return out


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("ticker")
    ap.add_argument("--peers", nargs="*", default=[])
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    sym, mkt = normalize(a.ticker)
    rep = {"quick": quick_snapshot(a.ticker)}
    st_yf = statements(sym)                       # 資產負債／現金流品質仍用 yfinance
    st = st_yf
    rep["pe_band"] = {}
    if mkt == "TW":
        tw = tw_statements(tw_code(sym))
        if tw:                                    # 8 季損益改用 MOPS 口徑，保留 yfinance 的品質指標
            st = {**tw, "dupont_ttm": st_yf.get("dupont_ttm"), "earnings_quality": st_yf.get("earnings_quality"),
                  "balance_source": st_yf.get("source")}
        rep["pe_band"] = tw_pe_band(tw_code(sym), a.out + "_pe_band.png")
    rep["statements_8q"] = st
    if not rep["pe_band"]:
        rep["pe_band"] = pe_band(sym, st.get("eps_ttm") or rep["quick"].get("eps_ttm"), a.out + "_pe_band.png")
    trend_chart(st, sym, a.out + "_trend.png")
    if mkt == "TW":
        rep["tw"] = tw_extras(tw_code(sym))
    if a.peers:
        rows = [rep["quick"]] + [quick_snapshot(p) for p in a.peers]
        rep["peer_table"] = [{"symbol": r["symbol"], "PE_fwd": r["valuation"]["PE_forward"], "PE_ttm": r["valuation"]["PE_trailing"],
                              "PB": r["valuation"]["PB"], "EV/EBITDA": r["valuation"]["EV_EBITDA"],
                              "GM%": r["profitability"]["gross_margin_pct"], "OPM%": r["profitability"]["op_margin_pct"],
                              "ROE%": r["profitability"]["ROE_pct"], "rev_yoy%": r["growth"]["revenue_yoy_pct"],
                              "mcap_bn": r["market_cap_bn"]} for r in rows]
    dump(rep, a.out + ".json")

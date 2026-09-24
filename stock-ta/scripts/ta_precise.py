#!/usr/bin/env python3
"""精準技術面（單支，含週線、支撐壓力、未回補缺口、籌碼密集區、台股法人／融資、訊號歷史勝率）。

用法:
  python ta_precise.py 2330 --out ./2330_ta          # 產生 2330_ta.json / 2330_ta.png / 2330_ta_weekly.png
  python ta_precise.py NVDA --out ./nvda_ta --period 3y
"""
import argparse
from datetime import date, timedelta
import pandas as pd
from data import fetch_ohlcv, finmind, normalize, tw_code, dump
from indicators import enrich, mpf_style, pivots, cluster_levels, volume_profile, forward_return_stats, gaps
from ta_quick import snapshot


def sr_levels(df: pd.DataFrame, close: float) -> dict:
    highs, lows = pivots(df.tail(250))
    zones = cluster_levels(highs + lows)
    sup = [z for z in zones if z["level"] < close]
    res = [z for z in zones if z["level"] > close]
    return {"support": sorted(sup, key=lambda z: -z["level"])[:3],
            "resistance": sorted(res, key=lambda z: z["level"])[:3]}


def signal_stats(d: pd.DataFrame) -> dict:
    gc = (d["MA5"] > d["MA20"]) & (d["MA5"].shift() <= d["MA20"].shift())
    kd_low = (d["K"] > d["D"]) & (d["K"].shift() <= d["D"].shift()) & (d["K"] < 30)
    brk = d["Close"] > d["High"].rolling(60).max().shift()
    return {"MA5/20 黃金交叉後": forward_return_stats(d, gc),
            "KD 低檔金叉後": forward_return_stats(d, kd_low),
            "突破 60 日新高後": forward_return_stats(d, brk)}


def tw_chips(code: str, days: int = 60) -> dict:
    start = (date.today() - timedelta(days=days + 10)).isoformat()
    out = {}
    inst = finmind("TaiwanStockInstitutionalInvestorsBuySell", code, start)
    if len(inst):
        inst["net"] = (inst["buy"] - inst["sell"]) / 1000  # 張
        piv = inst.pivot_table(index="date", columns="name", values="net", aggfunc="sum").fillna(0)
        cols = {"Foreign_Investor": "外資", "Investment_Trust": "投信", "Dealer_self": "自營商"}
        piv = piv.rename(columns=cols)[[c for c in cols.values() if c in piv.rename(columns=cols)]]
        out["法人買賣超_張"] = {
            "近5日": piv.tail(5).sum().round(0).to_dict(),
            "近20日": piv.tail(20).sum().round(0).to_dict(),
            "近60日": piv.sum().round(0).to_dict(),
            "投信連續買超天數": int((piv["投信"].iloc[::-1] > 0).cumprod().sum()) if "投信" in piv else None,
            "外資連續買超天數": int((piv["外資"].iloc[::-1] > 0).cumprod().sum()) if "外資" in piv else None,
        }
    mg = finmind("TaiwanStockMarginPurchaseShortSale", code, start)
    if len(mg):
        m = mg.sort_values("date")
        out["融資融券"] = {
            "融資餘額_張": int(m["MarginPurchaseTodayBalance"].iloc[-1]),
            "融資20日變化_張": int(m["MarginPurchaseTodayBalance"].iloc[-1] - m["MarginPurchaseTodayBalance"].iloc[-min(20, len(m))]),
            "融券餘額_張": int(m["ShortSaleTodayBalance"].iloc[-1]),
            "券資比_pct": round(float(m["ShortSaleTodayBalance"].iloc[-1] / max(m["MarginPurchaseTodayBalance"].iloc[-1], 1) * 100), 1),
        }
    return out


def annotated_chart(df: pd.DataFrame, symbol: str, path: str, sr: dict, bars=180, title="daily", gp: dict | None = None):
    import mplfinance as mpf
    d = enrich(df).tail(bars)
    lines = [z["level"] for z in sr["support"] + sr["resistance"]]
    colors = ["g"] * len(sr["support"]) + ["r"] * len(sr["resistance"])
    aps = [mpf.make_addplot(d[[f"MA{n}" for n in (5, 20, 60, 120)]]),
           mpf.make_addplot(d["K"], panel=2, ylabel="KD"), mpf.make_addplot(d["D"], panel=2),
           mpf.make_addplot(d["MACD_hist"], panel=3, type="bar", ylabel="MACD")]
    kw = dict(hlines=dict(hlines=lines, colors=colors, linestyle="-.", linewidths=0.8)) if lines else {}
    fills = [dict(y1=g["unfilled"][0], y2=g["unfilled"][1], where=(d.index >= pd.Timestamp(g["date"]).tz_localize(d.index.tz)),
                  alpha=0.25, color="g" if k == "support" else "r")
             for k in ("support", "resistance") for g in (gp or {}).get(k, [])
             if pd.Timestamp(g["date"]).tz_localize(d.index.tz) >= d.index[0]]   # 缺口區：從缺口日起塗色
    if fills: kw["fill_between"] = fills
    mpf.plot(d, type="candle", style=mpf_style(symbol), volume=True, addplot=aps, panel_ratios=(4, 1, 1, 1),
             title=f"{symbol}  {title}", savefig=path, figsize=(13, 10), **kw)
    print(f"[chart] {path}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("ticker")
    ap.add_argument("--period", default="2y")
    ap.add_argument("--out", required=True, help="輸出檔名前綴，例如 ./2330_ta")
    ap.add_argument("--drop-today", action="store_true")
    ap.add_argument("--gap-lookback", type=int, default=60, help="缺口偵測回看 K 線數（預設 60）")
    a = ap.parse_args()

    df, sym = fetch_ohlcv(a.ticker, a.period, drop_today=a.drop_today)
    wk, _ = fetch_ohlcv(a.ticker, "5y", "1wk", drop_today=a.drop_today)
    d = enrich(df)
    close = float(d["Close"].iloc[-1])
    sr = sr_levels(df, close)
    gp = gaps(df, close, lookback=a.gap_lookback)

    report = {
        "daily": snapshot(df, sym),
        "weekly": ({k.replace("_1d", "_1w").replace("_5d", "_5w").replace("_20d", "_20w").replace("52w", "5y"): v for k, v in snapshot(wk, sym).items()} if len(wk) > 30 else None),
        "support_resistance": sr,
        "gaps_unfilled": {"lookback_bars": a.gap_lookback, **gp},
        "volume_profile_top5": volume_profile(df.tail(250)),
        "signal_history": signal_stats(d),
        "volatility": {"ATR14_pct": round(float(d["ATR14"].iloc[-1] / close * 100), 2),
                       "daily_std_20d_pct": round(float(d["Close"].pct_change().tail(20).std() * 100), 2),
                       "max_drawdown_1y_pct": round(float((d["Close"].tail(250) / d["Close"].tail(250).cummax() - 1).min() * 100), 1)},
        "relative_strength": {},
    }
    # 相對強弱 vs 大盤（20/60日）
    _, mkt = normalize(a.ticker)
    bench_t = "^TWII" if mkt == "TW" else "^GSPC"
    try:
        b, _ = fetch_ohlcv(bench_t, a.period)
        j = d["Close"].to_frame("s").join(b["Close"].rename("b"), how="inner")
        for n in (20, 60):
            report["relative_strength"][f"vs_{bench_t}_{n}d_pct"] = round(float(((j.s.iloc[-1] / j.s.iloc[-n - 1]) - (j.b.iloc[-1] / j.b.iloc[-n - 1])) * 100), 2)
    except SystemExit:
        pass
    if mkt == "TW":
        report["tw_chips"] = tw_chips(tw_code(sym))

    annotated_chart(df, sym, a.out + ".png", sr, gp=gp)
    if len(wk) > 30:
        annotated_chart(wk, sym, a.out + "_weekly.png", {"support": [], "resistance": []}, bars=120, title="weekly")
    dump(report, a.out + ".json")

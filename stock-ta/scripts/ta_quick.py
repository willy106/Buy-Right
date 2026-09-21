#!/usr/bin/env python3
"""快速技術面掃描（1 支或多支，~1 秒/支，只用 yfinance）。

用法:
  python ta_quick.py 2330                # 台股
  python ta_quick.py AAPL NVDA 2454      # 多支比較
  python ta_quick.py 2330 --chart out.png --period 6mo
輸出: JSON（可被模型讀取）+ 可選 K 線圖。
"""
import argparse
import pandas as pd
from data import fetch_ohlcv, dump
from indicators import enrich, mpf_style


def snapshot(df: pd.DataFrame, symbol: str) -> dict:
    d = enrich(df)
    x, p = d.iloc[-1], d.iloc[-2]
    c = float(x["Close"])
    hi52, lo52 = float(d["High"].tail(250).max()), float(d["Low"].tail(250).min())
    ma_stack = [n for n in (5, 20, 60, 120) if c > x[f"MA{n}"]]
    flags = []
    if x["MA5"] > x["MA20"] and p["MA5"] <= p["MA20"]: flags.append("MA5/20 黃金交叉")
    if x["MA5"] < x["MA20"] and p["MA5"] >= p["MA20"]: flags.append("MA5/20 死亡交叉")
    if x["MACD_hist"] > 0 and p["MACD_hist"] <= 0: flags.append("MACD 柱翻正")
    if x["MACD_hist"] < 0 and p["MACD_hist"] >= 0: flags.append("MACD 柱翻負")
    if x["K"] > x["D"] and p["K"] <= p["D"] and x["K"] < 30: flags.append("KD 低檔金叉")
    if x["K"] < x["D"] and p["K"] >= p["D"] and x["K"] > 70: flags.append("KD 高檔死叉")
    if x["RSI14"] > 70: flags.append("RSI 超買")
    if x["RSI14"] < 30: flags.append("RSI 超賣")
    if c > x["BB_up"]: flags.append("站上布林上軌")
    if c < x["BB_dn"]: flags.append("跌破布林下軌")
    if x["Volume"] > 2 * x["VOL_MA20"]: flags.append("爆量（>2x 20日均量）")
    if c >= hi52 * 0.99: flags.append("接近 52 週新高")
    trend = "多頭" if len(ma_stack) == 4 else "空頭" if not ma_stack else "整理"
    return {
        "symbol": symbol, "date": str(x.name.date()), "close": round(c, 2),
        "chg_pct_1d": round((c / float(p["Close"]) - 1) * 100, 2),
        "chg_pct_5d": round((c / float(d["Close"].iloc[-6]) - 1) * 100, 2) if len(d) > 6 else None,
        "chg_pct_20d": round((c / float(d["Close"].iloc[-21]) - 1) * 100, 2) if len(d) > 21 else None,
        "trend": trend, "above_MA": ma_stack,
        "MA": {f"MA{n}": round(float(x[f"MA{n}"]), 2) for n in (5, 20, 60, 120) if pd.notna(x[f"MA{n}"])},
        "RSI14": round(float(x["RSI14"]), 1), "K": round(float(x["K"]), 1), "D": round(float(x["D"]), 1),
        "MACD_hist": round(float(x["MACD_hist"]), 3),
        "BB_pos_pct": round(float((c - x["BB_dn"]) / (x["BB_up"] - x["BB_dn"]) * 100), 1),
        "ATR14_pct": round(float(x["ATR14"] / c * 100), 2),
        "vol_ratio_20d": round(float(x["Volume"] / x["VOL_MA20"]), 2),
        "vol_ma20_shares": int(x["VOL_MA20"]),
        "pos_in_52w_range_pct": round((c - lo52) / (hi52 - lo52) * 100, 1),
        "hi52": round(hi52, 2), "lo52": round(lo52, 2),
        "flags": flags,
    }


def chart(df: pd.DataFrame, symbol: str, path: str, bars: int = 120):
    import mplfinance as mpf
    d = enrich(df).tail(bars)
    aps = [mpf.make_addplot(d[[f"MA{n}" for n in (5, 20, 60)]]),
           mpf.make_addplot(d["BB_up"], color="gray", linestyle="--", width=0.7),
           mpf.make_addplot(d["BB_dn"], color="gray", linestyle="--", width=0.7),
           mpf.make_addplot(d["RSI14"], panel=2, ylabel="RSI"),
           mpf.make_addplot(d["MACD_hist"], panel=3, type="bar", ylabel="MACD")]
    mpf.plot(d, type="candle", style=mpf_style(symbol), volume=True, addplot=aps,
             panel_ratios=(4, 1, 1, 1), title=f"{symbol}  quick TA", savefig=path, figsize=(12, 9))
    print(f"[chart] {path}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("tickers", nargs="+")
    ap.add_argument("--period", default="1y")
    ap.add_argument("--chart", help="輸出 PNG 路徑（單支時）")
    ap.add_argument("--json", help="輸出 JSON 路徑")
    ap.add_argument("--drop-today", action="store_true", help="盤中呼叫時剔除今日未完成 K 棒")
    a = ap.parse_args()
    out = []
    for t in a.tickers:
        df, sym = fetch_ohlcv(t, a.period, drop_today=a.drop_today)
        out.append(snapshot(df, sym))
        if a.chart and len(a.tickers) == 1:
            chart(df, sym, a.chart)
    dump(out if len(out) > 1 else out[0], a.json)

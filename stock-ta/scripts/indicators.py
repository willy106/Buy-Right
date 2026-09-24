"""Pure-pandas indicators (no TA-Lib). All functions take an OHLCV frame."""
from __future__ import annotations
import numpy as np
import pandas as pd


def sma(s: pd.Series, n: int) -> pd.Series:
    return s.rolling(n).mean()


def ema(s: pd.Series, n: int) -> pd.Series:
    return s.ewm(span=n, adjust=False).mean()


def rsi(close: pd.Series, n: int = 14) -> pd.Series:
    d = close.diff()
    up = d.clip(lower=0).ewm(alpha=1 / n, adjust=False).mean()
    dn = (-d.clip(upper=0)).ewm(alpha=1 / n, adjust=False).mean()
    return 100 - 100 / (1 + up / dn.replace(0, np.nan))


def macd(close: pd.Series, fast=12, slow=26, sig=9):
    line = ema(close, fast) - ema(close, slow)
    signal = ema(line, sig)
    return line, signal, line - signal


def bollinger(close: pd.Series, n=20, k=2.0):
    m = sma(close, n)
    sd = close.rolling(n).std()
    return m, m + k * sd, m - k * sd


def atr(df: pd.DataFrame, n=14) -> pd.Series:
    h, l, c = df["High"], df["Low"], df["Close"]
    tr = pd.concat([h - l, (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / n, adjust=False).mean()


def kd(df: pd.DataFrame, n=9, m1=3, m2=3):
    """台灣慣用 KD（RSV 9, K/D 各 3 平滑）。"""
    low_n = df["Low"].rolling(n).min()
    high_n = df["High"].rolling(n).max()
    rsv = (df["Close"] - low_n) / (high_n - low_n).replace(0, np.nan) * 100
    k = rsv.ewm(alpha=1 / m1, adjust=False).mean()
    d = k.ewm(alpha=1 / m2, adjust=False).mean()
    return k, d


def obv(df: pd.DataFrame) -> pd.Series:
    sign = np.sign(df["Close"].diff()).fillna(0)
    return (sign * df["Volume"]).cumsum()


def enrich(df: pd.DataFrame) -> pd.DataFrame:
    """Attach the standard indicator set used by both quick and precise modes."""
    out = df.copy()
    c = out["Close"]
    for n in (5, 10, 20, 60, 120, 240):
        out[f"MA{n}"] = sma(c, n)
    out["RSI14"] = rsi(c)
    out["MACD"], out["MACD_sig"], out["MACD_hist"] = macd(c)
    out["BB_mid"], out["BB_up"], out["BB_dn"] = bollinger(c)
    out["ATR14"] = atr(out)
    out["K"], out["D"] = kd(out)
    out["OBV"] = obv(out)
    out["VOL_MA20"] = sma(out["Volume"], 20)
    return out


# ---------------------------------------------------------------- structure
def pivots(df: pd.DataFrame, left=5, right=5) -> tuple[list[float], list[float]]:
    """Swing highs / lows: bar is a pivot if it is the max/min of +-N bars."""
    h, l = df["High"].values, df["Low"].values
    highs, lows = [], []
    for i in range(left, len(df) - right):
        if h[i] == h[i - left:i + right + 1].max():
            highs.append(float(h[i]))
        if l[i] == l[i - left:i + right + 1].min():
            lows.append(float(l[i]))
    return highs, lows


def cluster_levels(levels: list[float], tol=0.015, min_touch=2) -> list[dict]:
    """Merge nearby pivot prices into support/resistance zones."""
    if not levels:
        return []
    levels = sorted(levels)
    zones, cur = [], [levels[0]]
    for p in levels[1:]:
        if abs(p - cur[-1]) / cur[-1] <= tol:
            cur.append(p)
        else:
            zones.append(cur); cur = [p]
    zones.append(cur)
    return [{"level": round(float(np.mean(z)), 2), "touches": len(z)} for z in zones if len(z) >= min_touch]


def volume_profile(df: pd.DataFrame, bins=24) -> list[dict]:
    """Volume-at-price histogram; the top bins are high-volume nodes (籌碼密集區)."""
    lo, hi = df["Low"].min(), df["High"].max()
    edges = np.linspace(lo, hi, bins + 1)
    mid = (df["High"] + df["Low"]) / 2
    idx = np.clip(np.digitize(mid, edges) - 1, 0, bins - 1)
    vol = np.bincount(idx, weights=df["Volume"].values, minlength=bins)
    total = vol.sum() or 1
    rows = [{"low": round(float(edges[i]), 2), "high": round(float(edges[i + 1]), 2),
             "vol_pct": round(float(vol[i] / total * 100), 1)} for i in range(bins)]
    return sorted(rows, key=lambda r: -r["vol_pct"])[:5]


def forward_return_stats(df: pd.DataFrame, cond: pd.Series, horizons=(5, 20, 60)) -> dict:
    """Historical forward returns after a boolean event (e.g. golden cross)."""
    c = df["Close"]
    ev = df.index[cond.fillna(False)]
    res = {"events": int(len(ev))}
    for h in horizons:
        r = []
        for t in ev:
            i = df.index.get_loc(t)
            if i + h < len(df):
                r.append(c.iloc[i + h] / c.iloc[i] - 1)
        if r:
            r = np.array(r)
            res[f"{h}d"] = {"n": int(len(r)), "mean_pct": round(float(r.mean() * 100), 2),
                            "win_rate": round(float((r > 0).mean() * 100), 1)}
    return res


def mpf_style(symbol: str):
    """台股紅漲綠跌；美股綠漲紅跌。"""
    import mplfinance as mpf
    if symbol.endswith((".TW", ".TWO")):
        mc = mpf.make_marketcolors(up="#d62728", down="#2ca02c", edge="inherit", wick="inherit", volume="inherit")
        return mpf.make_mpf_style(base_mpf_style="yahoo", marketcolors=mc)
    return "yahoo"


def gaps(df: pd.DataFrame, close: float, lookback=60, min_pct=0.25, n=3) -> dict:
    """未回補的跳空缺口：向上缺口＝前日高～當日低，向下缺口＝當日高～前日低。
    之後 K 線若部分回補，只留下未回補的區間；完全回補就剔除。缺口在現價下方為支撐、上方為壓力。"""
    d = df.tail(lookback + 1)
    h, l, v, idx = d["High"].values, d["Low"].values, d["Volume"].values, d.index
    vma = df["Volume"].rolling(20).mean().shift().reindex(idx).values
    out = []
    for i in range(1, len(d)):
        up = l[i] > h[i - 1]
        if not (up or h[i] < l[i - 1]):
            continue
        lo, hi = (h[i - 1], l[i]) if up else (h[i], l[i - 1])
        if (hi - lo) / lo * 100 < min_pct:
            continue
        rest_lo, rest_hi = lo, hi
        if i + 1 < len(d):
            if up: rest_hi = min(hi, l[i + 1:].min())      # 之後最低點往下吃進缺口
            else:  rest_lo = max(lo, h[i + 1:].max())      # 之後最高點往上吃進缺口
        if rest_hi <= rest_lo:
            continue                                        # 已完全回補
        vr = round(float(v[i] / vma[i]), 2) if vma[i] and not np.isnan(vma[i]) else None
        out.append({"date": str(idx[i].date()), "dir": "向上" if up else "向下",
                    "zone": [round(float(lo), 2), round(float(hi), 2)],
                    "unfilled": [round(float(rest_lo), 2), round(float(rest_hi), 2)],
                    "filled_pct": round(float(1 - (rest_hi - rest_lo) / (hi - lo)) * 100, 0),
                    "vol_ratio": vr})
    sup = [g for g in out if g["unfilled"][0] < close]      # 現價在缺口內也算支撐（正在回測）
    res = [g for g in out if g["unfilled"][0] >= close]
    return {"support": sorted(sup, key=lambda g: -g["unfilled"][1])[:n],
            "resistance": sorted(res, key=lambda g: g["unfilled"][0])[:n]}

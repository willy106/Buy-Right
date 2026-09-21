#!/usr/bin/env python3
"""盤中短窗量比：抓「此刻」正在放量的個股，而不是全日累計量換算。

  短窗量比 = 最近 N 分鐘成交量 ÷ 過去 20 日「同一時段」的平均成交量
  * 分子：mis 即時累計量快照相減（交易所數字、無延遲）。每次執行都會存一筆快照到快取。
  * 分母：yfinance 5 分 K 算出各時段佔全日量的比例（20 日平均）× 20 日均量。
          只取「形狀」——yf 5 分 K 缺 13:25 後與收盤集合競價，總量約少 13%，但每天比例穩定。
  * 沒有 N 分鐘前的快照時（當天第一次跑），退回 yfinance 5 分 K 直接比（延遲約 20 分，會標註）。
  台股量能是 U 型（開收盤大、午盤小），同時段比較可消掉這個偏差；全日換算量比做不到。

用法:
  python flow_surge.py                     # 掃 groups.yaml 全部標的，列短窗量比前 10
  python flow_surge.py 2330 3017 --top 15  # 額外加幾檔
  python flow_surge.py --watch --interval 60   # 盤中取樣器：每 60 秒存一筆快照，13:30 自動結束
  python flow_surge.py --json /tmp/surge.json

欄位：win_min 實際窗口分鐘、win_lots 窗口成交張數、ratio 短窗量比、ret_pct 窗口內漲跌%、
      src = snap（即時快照）或 yf（延遲）、label = 爆量/放量 + 流入/流出/價平
"""
from __future__ import annotations
import argparse, json, logging, time
from datetime import datetime, timedelta
import numpy as np
import pandas as pd
from flowlib import load_groups, all_codes, market_map, realtime_quotes, CACHE

SLOT = 5  # 分鐘
AUCTION = "13:30*"  # 收盤集合競價（13:30 一次撮合）的虛擬時段


def _yf_symbol(code: str, mkt: dict) -> str:
    return f"{code}.TWO" if mkt.get(code) == "otc" else f"{code}.TW"


def _yf_download_raw(symbols: list[str], **kw) -> dict[str, pd.DataFrame]:
    import yfinance as yf
    logging.getLogger("yfinance").setLevel(logging.CRITICAL)
    if not symbols:
        return {}
    df = yf.download(symbols, group_by="ticker", progress=False, threads=True, auto_adjust=False, **kw)
    if df is None or df.empty:
        return {}
    if not isinstance(df.columns, pd.MultiIndex):
        return {symbols[0]: df.dropna(how="all")}
    out = {s: df[s].dropna(how="all") for s in symbols if s in df.columns.get_level_values(0)}
    return {s: d for s, d in out.items() if len(d)}


def _yf_download(sym: dict[str, str], **kw) -> dict[str, pd.DataFrame]:
    """sym: yf 代號 → 台股代號。回傳以「台股代號」為 key；上市/上櫃判斷不到的 .TW 抓不到會改試 .TWO。"""
    got = {sym[s]: d for s, d in _yf_download_raw(list(sym), **kw).items()}
    retry = {s.replace(".TW", ".TWO"): c for s, c in sym.items() if c not in got and s.endswith(".TW")}
    got.update({retry[s]: d for s, d in _yf_download_raw(list(retry), **kw).items()})
    return got


# ---------------------------------------------------------------- 同時段基準（每日快取）
def load_profile(codes: list[str], mkt: dict, days: int = 20) -> pd.DataFrame:
    """回傳 code, slot(HH:MM), frac(該 5 分鐘佔全日量比例), vol_mean(yf 5 分 K 平均股數), vol_ma20(日均量股數)。
       只用今天以前的資料，所以同一天內可快取重用。"""
    path = CACHE / f"vol_profile_{datetime.now():%Y%m%d}.csv"
    have = pd.read_csv(path, dtype={"code": str}) if path.exists() else pd.DataFrame(columns=["code"])
    todo = [c for c in codes if c not in set(have.code)]
    if todo:
        today = datetime.now().date()
        sym = {_yf_symbol(c, mkt): c for c in todo}
        m5 = _yf_download(sym, period="30d", interval="5m")
        d1 = _yf_download(sym, period="3mo", interval="1d")
        rows = []
        for c in todo:
            if c not in m5 or c not in d1:
                continue
            v5 = m5[c]["Volume"].dropna()
            v5 = v5[v5.index.date < today]
            vd = d1[c]["Volume"].dropna()
            vd.index = vd.index.date
            vd = vd[vd.index < today]
            dates = sorted(set(v5.index.date) & set(vd[vd > 0].index))[-days:]
            if len(dates) < 5:
                continue
            v5 = v5[pd.Index(v5.index.date).isin(dates)]
            piv = pd.DataFrame({"d": v5.index.date, "slot": v5.index.strftime("%H:%M"), "v": v5.values}) \
                    .pivot_table(index="d", columns="slot", values="v", aggfunc="sum").fillna(0)
            frac = piv.div(vd.reindex(piv.index), axis=0)
            for slot in piv.columns:
                rows.append({"code": c, "slot": slot, "frac": frac[slot].mean(), "vol_mean": piv[slot].mean(),
                             "vol_ma20": float(vd.tail(20).mean())})
            # yf 5 分 K 不含 13:30 收盤集合競價（一次撮合、約佔日量一成）。把日量扣掉各時段後的剩餘比例
            # 記成 AUCTION 這個「13:30 瞬間」的量；expected_frac 只在窗口跨過 13:30 時整筆計入。
            rest = 1 - frac.sum(axis=1).mean()
            if rest > 0:
                rows.append({"code": c, "slot": AUCTION, "frac": rest, "vol_mean": np.nan, "vol_ma20": float(vd.tail(20).mean())})
        if rows:
            have = pd.concat([have, pd.DataFrame(rows)], ignore_index=True)
            have.to_csv(path, index=False)
    return have[have.code.isin(codes)]


def expected_frac(prof: dict[str, float], t0: datetime, t1: datetime) -> float:
    """[t0, t1] 這段時間平常佔全日量的比例（依與各 5 分鐘時段重疊的分鐘數攤）。"""
    tot = 0.0
    for slot, f in prof.items():
        if slot == AUCTION:
            # 收盤競價的量要到 13:30 後才出現在 mis 累計量（實測約 13:30–13:32），當作 13:30 瞬間的整筆量
            if t0 < t0.replace(hour=13, minute=30, second=0, microsecond=0) <= t1:
                tot += f
            continue
        h, m = map(int, slot.split(":"))
        s = t0.replace(hour=h, minute=m, second=0, microsecond=0)
        e = s + timedelta(minutes=SLOT)
        ov = (min(e, t1) - max(s, t0)).total_seconds() / 60
        if ov > 0:
            tot += f * ov / SLOT
    return tot


# ---------------------------------------------------------------- 快照
def snap_path() -> "Path":
    return CACHE / f"vol_snap_{datetime.now():%Y%m%d}.csv"


def save_snapshot(q: pd.DataFrame, now: datetime):
    s = q[q.vol_lots.notna()][["code", "vol_lots", "last"]].assign(ts=now.isoformat(timespec="seconds"))
    p = snap_path()
    s.to_csv(p, mode="a", header=not p.exists(), index=False)


def load_snapshots() -> pd.DataFrame:
    p = snap_path()
    if not p.exists():
        return pd.DataFrame(columns=["code", "vol_lots", "last", "ts"])
    s = pd.read_csv(p, dtype={"code": str})
    s["ts"] = pd.to_datetime(s["ts"])
    return s


# ---------------------------------------------------------------- 計算
def label(ratio: float, ret: float, warm: float, hot: float, min_ret: float) -> str:
    if ratio is None or np.isnan(ratio) or ratio < warm:
        return ""
    lvl = "爆量" if ratio >= hot else "放量"
    d = "流入" if ret > min_ret else "流出" if ret < -min_ret else "價平"
    return lvl + d


def compute(codes: list[str], window: int = 15, warm=2.0, hot=3.0, min_ret=0.2, now: datetime | None = None,
            q: pd.DataFrame | None = None, save=True) -> tuple[pd.DataFrame, pd.DataFrame]:
    """回傳 (結果表, 即時報價)。"""
    now = now or datetime.now()
    mkt = market_map(codes)
    if q is None:
        q = realtime_quotes(codes, mkt)
    if q.empty:
        return pd.DataFrame(), q
    snaps = load_snapshots()
    if save:
        save_snapshot(q, now)
    prof = load_profile(codes, mkt)
    pf = {c: g.set_index("slot")["frac"].to_dict() for c, g in prof.groupby("code")}
    pv = {c: g.set_index("slot")["vol_mean"].to_dict() for c, g in prof.groupby("code")}
    ma20 = prof.groupby("code")["vol_ma20"].first().to_dict()

    rows, need_yf = [], []
    for x in q.itertuples():
        c = x.code
        r = {"code": c, "name": x.name, "last": x.last, "chg_pct": round(x.chg_pct, 2) if pd.notna(x.chg_pct) else None,
             "src": None, "win_min": None, "win_lots": None, "exp_lots": None, "ratio": np.nan, "ret_pct": np.nan}
        s = snaps[snaps.code == c]
        if len(s) and pd.notna(x.vol_lots) and c in pf:
            age = (now - s["ts"]).dt.total_seconds() / 60
            ok = s[(age >= window * 0.5) & (age <= window * 2)]
            if len(ok):
                ref = ok.loc[(age[ok.index] - window).abs().idxmin()]
                t0 = ref["ts"].to_pydatetime()
                t0 = max(t0, now.replace(hour=9, minute=0, second=0, microsecond=0))
                exp = expected_frac(pf[c], t0, now) * ma20[c] / 1000
                vol = float(x.vol_lots) - float(ref["vol_lots"])
                r.update({"src": "snap", "win_min": round((now - t0).total_seconds() / 60), "win_lots": int(vol),
                          "exp_lots": round(exp), "ratio": round(vol / exp, 2) if exp > 0 else np.nan,
                          "ret_pct": round((x.last / ref["last"] - 1) * 100, 2) if ref["last"] else np.nan})
        if r["src"] is None:
            need_yf.append(c)
        rows.append(r)
    out = pd.DataFrame(rows)

    # 當天第一次跑：退回 yf 5 分 K（延遲約 20 分）
    if need_yf:
        sym = {_yf_symbol(c, mkt): c for c in need_yf if c in pv}
        m5 = _yf_download(sym, period="1d", interval="5m")
        k = max(window // SLOT, 1)
        for c in sym.values():
            if c not in m5:
                continue
            b = m5[c]
            b = b[b.index.date == now.date()].iloc[:-1]  # 最後一根尚未走完
            if len(b) < k:
                continue
            w = b.tail(k)
            base = sum(pv[c].get(t, 0) for t in w.index.strftime("%H:%M"))
            i = out.index[out.code == c][0]
            out.loc[i, ["src", "win_min", "win_lots", "exp_lots", "ratio", "ret_pct"]] = [
                f"yf~{(w.index[-1] + timedelta(minutes=SLOT)):%H:%M}", k * SLOT, int(w["Volume"].sum() / 1000), round(base / 1000),
                round(w["Volume"].sum() / base, 2) if base else np.nan,
                round((w["Close"].iloc[-1] / w["Open"].iloc[0] - 1) * 100, 2)]
    out["label"] = [label(r, t, warm, hot, min_ret) for r, t in zip(out["ratio"].astype(float), out["ret_pct"].astype(float))]
    return out, q


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("codes", nargs="*")
    ap.add_argument("--window", type=int, default=15, help="窗口分鐘")
    ap.add_argument("--warm", type=float, default=2.0); ap.add_argument("--hot", type=float, default=3.0)
    ap.add_argument("--min-ret", type=float, default=0.2, help="窗口漲跌超過此 %% 才算流入/流出")
    ap.add_argument("--min-lots", type=int, default=50, help="排行只列窗口成交 ≥ 此張數（濾掉冷門股雜訊）")
    ap.add_argument("--top", type=int, default=10)
    ap.add_argument("--watch", action="store_true"); ap.add_argument("--interval", type=int, default=60)
    ap.add_argument("--json")
    a = ap.parse_args()
    codes = sorted(set(all_codes(load_groups())) | {str(c).zfill(4) for c in a.codes})
    while True:
        now = datetime.now()
        out, _ = compute(codes, a.window, a.warm, a.hot, a.min_ret, now)
        if out.empty:
            print("[warn] 即時報價取得失敗")
        else:
            top = out[(out.win_lots.fillna(0) >= a.min_lots)].sort_values("ratio", ascending=False).head(a.top)
            print(f"\n[{now:%H:%M:%S}] 短窗量比 TOP {a.top}（窗口 {a.window} 分；src=yf 表示延遲資料）")
            print(top[["code", "name", "chg_pct", "src", "win_min", "win_lots", "exp_lots", "ratio", "ret_pct", "label"]].to_string(index=False))
            if a.json:
                json.dump({"time": now.isoformat(timespec="seconds"), "window": a.window, "rows": out.to_dict("records")},
                          open(a.json, "w"), ensure_ascii=False, indent=2, default=str)
        if not a.watch or now.time() >= datetime.strptime("13:31", "%H:%M").time():
            break
        time.sleep(a.interval)


if __name__ == "__main__":
    main()

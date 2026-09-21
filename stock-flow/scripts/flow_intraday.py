#!/usr/bin/env python3
"""盤中族群資金流向（免費即時報價，建議 --interval ≥ 60 秒）。

用法:
  python flow_intraday.py                       # 跑一次，印族群排行
  python flow_intraday.py --watch --interval 90 # 持續刷新並累積快照（可畫盤中資金輪動）
  python flow_intraday.py --top 8 --json /tmp/flow.json
  python flow_intraday.py --groups my_groups.yaml --industry   # 加自訂族群 + 官方產業別

指標（每族群）:
  turnover_M      族群成交值估計（百萬）
  share_pct       佔大盤成交值 %          ← 資金在哪
  share_vs_5d     佔比 − 近 5 日盤後平均佔比（百分點） ← 資金「移入/移出」
  avg_chg / med_chg 平均／中位漲跌 %
  up/down         漲跌家數
  active_buy_pct  外盤估計比例（最新成交貼近賣價的家數比）
  leaders         族群內成交值前 3 名 + 漲幅
"""
import argparse, json, time
from datetime import datetime
import numpy as np
import pandas as pd
from flowlib import load_groups, all_codes, market_map, realtime_quotes, market_total_turnover_M, eod_prices, snapshot_path, CACHE


def baseline_share(groups: dict) -> dict[str, float]:
    """近 5 個交易日各族群成交值佔比平均（用盤後快取 CSV；沒有就抓今日盤後資料當唯一樣本）。"""
    files = sorted(CACHE.glob("eod_[0-9]*.csv"))[-5:]
    frames = [pd.read_csv(f, dtype={"code": str}) for f in files]
    if not frames:
        px = eod_prices()
        if px.empty:
            return {}
        frames = [px]
    res = {}
    for g, codes in groups.items():
        shares = []
        for df in frames:
            tot = df["turnover_M"].sum()
            if tot:
                shares.append(df[df.code.isin(codes)]["turnover_M"].sum() / tot * 100)
        res[g] = float(np.mean(shares)) if shares else np.nan
    return res


def aggregate(q: pd.DataFrame, groups: dict, mkt_turnover: float | None, base: dict) -> pd.DataFrame:
    rows = []
    for g, codes in groups.items():
        d = q[q.code.isin(codes)]
        if d.empty:
            continue
        t = d["turnover_M"].sum()
        share = t / mkt_turnover * 100 if mkt_turnover else np.nan
        lead = d.sort_values("turnover_M", ascending=False).head(3)
        rows.append({"group": g, "n": len(d), "turnover_M": round(t, 0), "share_pct": round(share, 2),
                     "share_vs_5d": round(share - base.get(g, np.nan), 2) if base else np.nan,
                     "avg_chg": round(d["chg_pct"].mean(), 2), "med_chg": round(d["chg_pct"].median(), 2),
                     "up": int((d["chg_pct"] > 0).sum()), "down": int((d["chg_pct"] < 0).sum()),
                     "active_buy_pct": round((d["side"] == 1).mean() * 100, 0),
                     "leaders": ", ".join(f"{r.name}({r.code}) {r.chg_pct:+.1f}%" for r in lead.itertuples())})
    out = pd.DataFrame(rows)
    # 資金流向分數：佔比變化 z-score + 漲幅 z-score，用來排序
    for c in ("share_vs_5d", "avg_chg"):
        s = out[c]
        out[c + "_z"] = (s - s.mean()) / (s.std() or 1)
    out["flow_score"] = (out["share_vs_5d_z"].fillna(0) + out["avg_chg_z"]).round(2)
    return out.sort_values("flow_score", ascending=False).reset_index(drop=True)


def render(out: pd.DataFrame, top: int, mkt_turnover, ts: str):
    print(f"\n=== 族群資金流向 {ts}  大盤成交值≈{(mkt_turnover or 0)/100:.0f} 億 ===")
    cols = ["group", "share_pct", "share_vs_5d", "avg_chg", "up", "down", "active_buy_pct", "flow_score", "leaders"]
    print("【流入】"); print(out.head(top)[cols].to_string(index=False))
    print("【流出】"); print(out.tail(top)[cols].iloc[::-1].to_string(index=False))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--groups"); ap.add_argument("--industry", action="store_true")
    ap.add_argument("--watch", action="store_true"); ap.add_argument("--interval", type=int, default=90)
    ap.add_argument("--top", type=int, default=6); ap.add_argument("--json")
    a = ap.parse_args()
    groups = load_groups(a.groups, a.industry)
    codes = all_codes(groups)
    mkt = market_map(codes)
    base = baseline_share(groups)
    snap = snapshot_path("intraday")
    while True:
        q = realtime_quotes(codes, mkt)
        if q.empty:
            raise SystemExit("即時報價取得失敗（非交易時段或被限流；請拉長 --interval）")
        tot = market_total_turnover_M()
        out = aggregate(q, groups, tot, base)
        ts = q["time"].dropna().max() or datetime.now().strftime("%H:%M:%S")
        render(out, a.top, tot, ts)
        out.assign(time=ts).to_csv(snap, mode="a", header=not snap.exists(), index=False)
        if a.json:
            json.dump({"time": ts, "market_turnover_M": tot, "groups": out.to_dict("records")}, open(a.json, "w"), ensure_ascii=False, indent=2)
        if not a.watch:
            break
        time.sleep(a.interval)

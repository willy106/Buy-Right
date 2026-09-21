#!/usr/bin/env python3
"""盤後族群資金流向：三大法人淨買賣金額 + 成交值變化，按族群加總（資料約 15:30–16:30 後齊全）。

用法:
  python flow_eod.py                        # 今日
  python flow_eod.py --top 8 --chart /tmp/flow_eod.png --json /tmp/flow_eod.json
  python flow_eod.py --history 5            # 用快取比較近 5 日族群法人淨額趨勢

指標（每族群，單位百萬 NT$）:
  foreign_M / trust_M / dealer_M / inst_M   外資／投信／自營／合計淨買超金額（張×收盤價）
  turnover_M, share_pct, share_vs_5d        成交值、佔比、佔比相對近 5 日
  inst_vs_turnover_pct                      法人淨額 / 族群成交值 %  ← 法人主導程度
  avg_chg, up/down, top_buy / top_sell      族群內法人買最多／賣最多的個股
"""
import argparse, json
import numpy as np
import pandas as pd
from flowlib import load_groups, eod_prices, eod_institutional, snapshot_path, data_date, CACHE


def build(groups: dict, px: pd.DataFrame, inst: pd.DataFrame) -> pd.DataFrame:
    df = px.merge(inst, on="code", how="left").fillna({"foreign_lots": 0, "trust_lots": 0, "dealer_lots": 0})
    for k in ("foreign", "trust", "dealer"):
        df[f"{k}_M"] = df[f"{k}_lots"] * df["close"] * 1000 / 1e6
    df["inst_M"] = df[["foreign_M", "trust_M", "dealer_M"]].sum(axis=1)
    tot = px["turnover_M"].sum()
    # 近 5 日基準佔比
    today = f"eod_{data_date(px)}.csv"  # 排除同一交易日的快取（避免跟自己比）
    hist = [pd.read_csv(f, dtype={"code": str}) for f in sorted(CACHE.glob("eod_[0-9]*.csv")) if f.name != today][-5:]
    rows = []
    for g, codes in groups.items():
        d = df[df.code.isin(codes)]
        if d.empty:
            continue
        t = d["turnover_M"].sum(); share = t / tot * 100 if tot else np.nan
        bshare = np.mean([h[h.code.isin(codes)]["turnover_M"].sum() / h["turnover_M"].sum() * 100 for h in hist if h["turnover_M"].sum()]) if hist else np.nan
        tb = d.sort_values("inst_M", ascending=False).iloc[0]; ts = d.sort_values("inst_M").iloc[0]
        rows.append({"group": g, "n": len(d), "turnover_M": round(t), "share_pct": round(share, 2),
                     "share_vs_5d": round(share - bshare, 2) if not np.isnan(bshare) else np.nan,
                     "foreign_M": round(d["foreign_M"].sum()), "trust_M": round(d["trust_M"].sum()),
                     "dealer_M": round(d["dealer_M"].sum()), "inst_M": round(d["inst_M"].sum()),
                     "inst_vs_turnover_pct": round(d["inst_M"].sum() / t * 100, 1) if t else np.nan,
                     "avg_chg": round(d["chg_pct"].mean(), 2), "up": int((d.chg_pct > 0).sum()), "down": int((d.chg_pct < 0).sum()),
                     "top_buy": f"{tb['name']}({tb.code}) {tb.inst_M:+.0f}M", "top_sell": f"{ts['name']}({ts.code}) {ts.inst_M:+.0f}M"})
    out = pd.DataFrame(rows).sort_values("inst_M", ascending=False).reset_index(drop=True)
    return out, df


def chart(out: pd.DataFrame, path: str):
    import matplotlib; matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib import font_manager
    for f in ("Noto Sans CJK TC", "Microsoft JhengHei", "PingFang TC", "Noto Sans CJK JP"):
        if any(f in x.name for x in font_manager.fontManager.ttflist):
            plt.rcParams["font.family"] = f; break
    plt.rcParams["axes.unicode_minus"] = False
    d = out.sort_values("inst_M")
    fig, ax = plt.subplots(figsize=(10, max(5, 0.4 * len(d))))
    ax.barh(d["group"], d["foreign_M"], color="#1f77b4", label="外資")
    ax.barh(d["group"], d["trust_M"], left=d["foreign_M"].clip(lower=0), color="#ff7f0e", label="投信")
    ax.barh(d["group"], d["dealer_M"], left=(d["foreign_M"].clip(lower=0) + d["trust_M"].clip(lower=0)), color="#7f7f7f", label="自營")
    ax.axvline(0, color="k", lw=0.8); ax.set_xlabel("法人淨買超 (百萬)"); ax.legend(); ax.grid(alpha=0.3, axis="x")
    fig.savefig(path, dpi=130, bbox_inches="tight"); plt.close(fig); print(f"[chart] {path}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--groups"); ap.add_argument("--industry", action="store_true")
    ap.add_argument("--top", type=int, default=8); ap.add_argument("--chart"); ap.add_argument("--json")
    ap.add_argument("--history", type=int, default=0, help="顯示近 N 日族群法人淨額（需有快取）")
    a = ap.parse_args()
    groups = load_groups(a.groups, a.industry)
    px = eod_prices()
    if px.empty:
        raise SystemExit("盤後收盤資料取得失敗")
    inst = eod_institutional(data_date(px))            # 與收盤價同一交易日
    if inst.empty:
        print("[warn] 法人資料尚未公布或取得失敗，僅顯示成交值流向")
        inst = pd.DataFrame(columns=["code", "foreign_lots", "trust_lots", "dealer_lots"])
    out, merged = build(groups, px, inst)
    d = data_date(px)                                    # 以資料交易日命名，盤中跑也不會標錯日期
    px.to_csv(snapshot_path("eod", d), index=False)      # 供盤中/盤後 5 日基準
    out.to_csv(snapshot_path("eod_groups", d), index=False)
    cols = ["group", "inst_M", "foreign_M", "trust_M", "share_pct", "share_vs_5d", "inst_vs_turnover_pct", "avg_chg", "up", "down", "top_buy", "top_sell"]
    print("\n【法人淨流入 TOP】"); print(out.head(a.top)[cols].to_string(index=False))
    print("\n【法人淨流出 TOP】"); print(out.tail(a.top)[cols].iloc[::-1].to_string(index=False))
    if a.history:
        files = sorted(CACHE.glob("eod_groups_*.csv"))[-a.history:]
        h = pd.concat([pd.read_csv(f).assign(date=f.stem[-8:]) for f in files])
        print("\n【近日族群法人淨額 (M)】"); print(h.pivot_table(index="group", columns="date", values="inst_M").sort_values(h.date.max(), ascending=False).head(15).to_string())
    if a.chart:
        chart(out, a.chart)
    if a.json:
        json.dump({"groups": out.to_dict("records")}, open(a.json, "w"), ensure_ascii=False, indent=2, default=str)

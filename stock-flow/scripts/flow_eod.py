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
from flowlib import load_groups, eod_prices, eod_problems, eod_institutional, snapshot_path, data_date, CACHE


def save_cache(df: pd.DataFrame, path, min_ratio: float = 0.9) -> bool:
    """寫快取，但不讓明顯較少筆的資料蓋掉同一天已存在的快取（例如某市場抓到一半斷線）。"""
    if path.exists():
        try:
            old = len(pd.read_csv(path))
        except Exception:
            old = 0
        if old and len(df) < old * min_ratio:
            print(f"[warn] 不覆蓋 {path.name}：新資料 {len(df)} 筆 < 既有 {old} 筆 × {min_ratio:g}")
            return False
    df.to_csv(path, index=False)
    return True


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


def print_history(n: int):
    """只讀快取，不需要當日資料。"""
    files = sorted(CACHE.glob("eod_groups_*.csv"))[-n:]
    if not files:
        print("[warn] 沒有族群法人快取"); return
    h = pd.concat([pd.read_csv(f).assign(date=f.stem[-8:]) for f in files])
    print("\n【近日族群法人淨額 (M)】"); print(h.pivot_table(index="group", columns="date", values="inst_M").sort_values(h.date.max(), ascending=False).head(15).to_string())


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--groups"); ap.add_argument("--industry", action="store_true")
    ap.add_argument("--top", type=int, default=8); ap.add_argument("--chart"); ap.add_argument("--json")
    ap.add_argument("--history", type=int, default=0, help="顯示近 N 日族群法人淨額（需有快取）")
    ap.add_argument("--allow-partial", action="store_true",
                    help="收盤資料不完整或兩市場日期不一致時仍輸出（不寫快取、結果標示為不完整）")
    a = ap.parse_args()
    groups = load_groups(a.groups, a.industry)
    px = eod_prices()
    if px.empty:
        if a.history: print_history(a.history)
        raise SystemExit("盤後收盤資料取得失敗")
    probs = eod_problems(px)
    if probs:
        msg = "；".join(probs) + f"。各市場資料日：{px.attrs.get('market_dates')}"
        if not a.allow_partial:
            if a.history: print_history(a.history)  # 歷史只讀快取，照樣顯示
            raise SystemExit(f"[error] 收盤資料不可用：{msg}\n稍後重跑（上市 OpenAPI 常到晚上才更新；MI_INDEX 備援也失敗時）"
                             "，或加 --allow-partial 看不完整結果（不寫快取）")
        print(f"[warn] 收盤資料不完整，以下結果僅供參考、不寫快取：{msg}")
    inst = eod_institutional(data_date(px))            # 與收盤價同一交易日
    im = inst.attrs.get("inst_markets", {})
    inst_missing = [n for m, n in (("twse", "上市"), ("tpex", "上櫃")) if not im.get(m)]
    if inst.empty:
        print("[warn] 法人資料尚未公布或取得失敗，僅顯示成交值流向")
        inst = pd.DataFrame(columns=["code", "foreign_lots", "trust_lots", "dealer_lots"])
    if inst_missing:
        print(f"[warn] {'、'.join(inst_missing)}法人資料缺，族群法人數字不完整、不寫族群快取（稍後重跑）")
    out, merged = build(groups, px, inst)
    d = data_date(px)                                    # 以資料交易日命名，盤中跑也不會標錯日期
    if not probs:                                        # 不完整的資料不進快取（會污染 5 日基準與 --history）
        if save_cache(px, snapshot_path("eod", d)) and not inst_missing:  # 價格供 5 日基準；法人不全就不寫族群快取
            out.to_csv(snapshot_path("eod_groups", d), index=False)
    cols = ["group", "inst_M", "foreign_M", "trust_M", "share_pct", "share_vs_5d", "inst_vs_turnover_pct", "avg_chg", "up", "down", "top_buy", "top_sell"]
    print("\n【法人淨流入 TOP】"); print(out.head(a.top)[cols].to_string(index=False))
    print("\n【法人淨流出 TOP】"); print(out.tail(a.top)[cols].iloc[::-1].to_string(index=False))
    if a.history:
        print_history(a.history)
    if a.chart:
        chart(out, a.chart)
    if a.json:
        json.dump({"groups": out.to_dict("records")}, open(a.json, "w"), ensure_ascii=False, indent=2, default=str)

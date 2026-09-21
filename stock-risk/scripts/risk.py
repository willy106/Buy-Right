#!/usr/bin/env python3
"""風控三件事：大盤環境 → 部位大小 → 移動停利。

  python risk.py regime                          # 大盤環境：多頭／震盪／空頭、建議總持股、每筆風險係數
  python risk.py size 2303 --entry 155 --stop 153 # 建議張數（依 1R、單檔上限、流動性、大盤係數）
  python risk.py size 2303 --entry 155 --stop 153 --no-regime
  python risk.py trail                           # 日誌未平倉部位的 R 倍數與移動停利建議（只讀；寫入由 journal check 負責）
  python risk.py exposure                        # 目前總持股 vs 大盤環境建議上限
  加 --json out.json 輸出 JSON。
"""
from __future__ import annotations
import argparse, json
import pandas as pd
import risklib as rl


def cmd_regime(a):
    r = rl.regime()
    print(rl.regime_line(r)); return r


def cmd_size(a):
    s = rl.size(a.code, a.entry, a.stop, a.capital, a.risk_pct, use_regime=not a.no_regime)
    print(rl.size_text(s)); return s


def _journal() -> pd.DataFrame:
    import yaml, os
    from pathlib import Path
    jcfg = rl._jcfg()
    return pd.read_csv(Path(os.path.expanduser(jcfg["local_dir"])) / "trades.csv", dtype={"code": str})


def cmd_trail(a):
    df = _journal(); op = df[df["status"] == "open"]
    lt = set(rl._jcfg().get("longterm_types", ["長期持有"]))
    op = op[~op["thesis_type"].isin(lt)]           # 長期持有不做移動停利
    rows = []
    for _, r in op.iterrows():
        stop0 = r.get("stop_init")
        if pd.isna(stop0):
            stop0 = r["falsify_price"] if pd.notna(r["falsify_price"]) else r.get("falsify_level")
        if pd.isna(stop0):
            print(f"[skip] #{r['id']} {r['code']} 沒有初始停損價，無法算 R"); continue
        t = rl.trail(float(r["entry_price"]), float(stop0), r["entry_date"], rl.ohlcv(r["code"], "1y"))
        prev = r.get("trail_stop")
        rows.append({"id": int(r["id"]), "code": r["code"], "name": r["name"], "entry": r["entry_price"], "stop_init": stop0,
                     "R": t["R"], "R_now": t["r_now"], "R_max": t["r_max"], "啟動價": t.get("activate_at"), "啟動": t["activated"],
                     **{f"規則_{k}": v for k, v in t["levels"].items()}, "建議停利": t["trail"],
                     "已記錄停利": None if pd.isna(prev) else prev})
    out = pd.DataFrame(rows)
    print(out.to_string(index=False) if len(out) else "無持倉")
    return rows


def cmd_exposure(a):
    r = rl.regime(); tot, rows = rl.open_exposure(); cap = rl.CFG["capital"]
    for x in rows:
        print(f"#{x['id']} {x['code']} {x['name']}：{x['value']:,.0f} 元（{x['value'] / cap * 100:.1f}%）")
    print(f"總持股 {tot:,.0f} 元＝{tot / cap * 100:.1f}%；大盤【{r['regime']}】建議 ≤{r['exposure_pct']}%")
    return {"total": tot, "pct": tot / cap * 100, "regime": r, "positions": rows}


if __name__ == "__main__":
    ap = argparse.ArgumentParser(); sp = ap.add_subparsers(dest="cmd", required=True)
    for n in ("regime", "trail", "exposure"):
        sp.add_parser(n).add_argument("--json")
    p = sp.add_parser("size"); p.add_argument("code"); p.add_argument("--entry", type=float, required=True); p.add_argument("--stop", type=float, required=True)
    p.add_argument("--capital", type=float); p.add_argument("--risk-pct", type=float); p.add_argument("--no-regime", action="store_true"); p.add_argument("--json")
    a = ap.parse_args()
    res = {"regime": cmd_regime, "size": cmd_size, "trail": cmd_trail, "exposure": cmd_exposure}[a.cmd](a)
    if a.json:
        json.dump(res, open(a.json, "w"), ensure_ascii=False, indent=2, default=str)

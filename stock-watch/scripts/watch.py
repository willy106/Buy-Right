#!/usr/bin/env python3
"""盯盤總指揮：呼叫 stock-flow / stock-ta / stock-fa 的腳本，組成一頁盤中（或盤後）報告。

用法:
  python watch.py                         # 用 assets/watchlist.yaml
  python watch.py 2330 3017 6669          # 額外加幾檔
  python watch.py --eod                   # 強制盤後模式（13:30 後自動切）
  python watch.py --out /tmp/watch.md --json /tmp/watch.json

尋找工具箱的順序：$STOCK_TOOLBOX → 本 skill 的上層目錄 → ~/.claude/skills
"""
from __future__ import annotations
import argparse, json, os, subprocess, sys, tempfile
from datetime import datetime, time as dtime
from pathlib import Path
import pandas as pd
import yaml

HERE = Path(__file__).resolve().parent
SKILL = HERE.parent
ASSETS = SKILL / "assets"


# ---------------------------------------------------------------- locate toolbox
def find_skill(name: str) -> Path:
    for base in (os.environ.get("STOCK_TOOLBOX"), SKILL.parent, Path.home() / ".claude" / "skills", Path.cwd() / ".claude" / "skills"):
        if base and (Path(base) / name / "scripts").exists():
            return Path(base) / name / "scripts"
    raise SystemExit(f"找不到 {name} skill；請設定 STOCK_TOOLBOX 指向工具箱目錄")


def run_json(script_dir: Path, script: str, args: list[str], json_flag="--json") -> dict | list | None:
    """執行子腳本並讀回其 JSON 輸出；失敗回 None 並印 warn。"""
    tmp = tempfile.NamedTemporaryFile(suffix=".json", delete=False).name
    cmd = [sys.executable, str(script_dir / script), *args, json_flag, tmp]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    if r.returncode != 0:
        print(f"[warn] {script} 失敗: {r.stderr.strip()[-300:]}")
        return None
    try:
        return json.load(open(tmp))
    except Exception:
        return None


# ---------------------------------------------------------------- watchlist
def load_journal() -> dict[str, dict]:
    """讀 stock-journal 的未平倉部位（若有安裝），讓盯盤表標出持倉與證偽狀態。"""
    import yaml as _y
    for base in (os.environ.get("STOCK_TOOLBOX"), SKILL.parent, Path.home() / ".claude" / "skills"):
        cfg = Path(base) / "stock-journal" / "assets" / "config.yaml" if base else None
        if cfg and not cfg.exists():
            cfg = cfg.with_name("config.example.yaml")
        if cfg and cfg.exists():
            csvp = Path(os.path.expanduser(_y.safe_load(cfg.read_text(encoding="utf-8"))["local_dir"])) / "trades.csv"
            if csvp.exists():
                df = pd.read_csv(csvp, dtype={"code": str})
                op = df[df["status"] == "open"]
                return {r["code"]: r for _, r in op.iterrows()}
    return {}


def load_watchlist(extra: list[str]) -> dict[str, list[str]]:
    p = ASSETS / "watchlist.yaml"   # 個人清單（不進 git）；沒有就用範本
    wl = yaml.safe_load((p if p.exists() else ASSETS / "watchlist.example.yaml").read_text(encoding="utf-8")) or {}
    wl = {k: [str(x).zfill(4) for x in v] for k, v in wl.items()}
    if extra:
        wl["臨時"] = [str(x).zfill(4) for x in extra]
    return wl


def group_of(code: str, groups: dict) -> list[str]:
    return [g for g, cs in groups.items() if code in cs]


def is_trading_now(now: datetime) -> bool:
    return now.weekday() < 5 and dtime(8, 45) <= now.time() <= dtime(13, 35)


def session_progress(now: datetime) -> float:
    """開盤已過比例（用來把盤中量換算成全日量）。"""
    start, end = now.replace(hour=9, minute=0, second=0), now.replace(hour=13, minute=30, second=0)
    return min(max((now - start).total_seconds() / (end - start).total_seconds(), 0.05), 1.0)


# ---------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("codes", nargs="*")
    ap.add_argument("--eod", action="store_true")
    ap.add_argument("--out"); ap.add_argument("--json")
    a = ap.parse_args()
    now = datetime.now()
    mode = "eod" if a.eod or not is_trading_now(now) else "intraday"
    alerts = yaml.safe_load((ASSETS / "alerts.yaml").read_text(encoding="utf-8"))
    wl = load_watchlist(a.codes)
    jr = load_journal()
    for c in jr:
        wl.setdefault("持倉(日誌)", []).append(c)
    codes = sorted({c for v in wl.values() for c in v})

    flow_dir, ta_dir = find_skill("stock-flow"), find_skill("stock-ta")
    sys.path.insert(0, str(flow_dir))
    import flowlib  # noqa: E402
    groups = flowlib.load_groups()

    # 1) 族群資金流向
    flow = run_json(flow_dir, "flow_eod.py" if mode == "eod" else "flow_intraday.py", ["--top", str(alerts["group_top_n"])])
    fg = pd.DataFrame(flow["groups"]) if flow else pd.DataFrame()

    # 2) 自選標的即時報價 + 技術面快照（ta_quick 用昨日為止的日線）
    mkt = flowlib.market_map(codes)
    q = flowlib.realtime_quotes(codes, mkt) if mode == "intraday" else pd.DataFrame()
    if q.empty and mode == "intraday":
        print("[warn] 即時報價失敗，改用盤後模式"); mode = "eod"
    ta = run_json(ta_dir, "ta_quick.py", codes + (["--drop-today"] if mode == "intraday" else [])) or []
    # 2b) 短窗量比（盤中）：掃族群清單 ∪ 自選，同時存即時量快照
    sg = pd.DataFrame()
    if mode == "intraday":
        surge = run_json(flow_dir, "flow_surge.py", codes + ["--window", str(alerts["surge_window_min"]),
                         "--warm", str(alerts["surge_warm"]), "--hot", str(alerts["surge_hot"]), "--min-ret", str(alerts["surge_min_ret_pct"])])
        sg = pd.DataFrame(surge["rows"]) if surge else pd.DataFrame()
    sgd = {r["code"]: r for r in sg.to_dict("records")} if len(sg) else {}
    ta = {t["symbol"].split(".")[0]: t for t in (ta if isinstance(ta, list) else [ta])}
    prog = session_progress(now)

    rows, alert_lines, seen = [], [], set()
    for cat, cs in wl.items():
        for c in cs:
            if c in seen:
                continue
            seen.add(c)
            t = ta.get(c, {})
            # 盤中 ta 用 --drop-today，其「爆量」是上一根完整日 K 的量；改由下方換算量比判斷，盤後才沿用日 K
            flags = [f for f in t.get("flags", []) if mode != "intraday" or "爆量" not in f]
            gl = group_of(c, groups)  # 族群名稱本身可能含 "/"，比對用清單而非 join 後的字串
            r = {"分類": cat, "代號": c, "名稱": None, "現價": None, "漲跌%": None, "量比(換算)": None, "短窗量比": "-",
                 "趨勢": t.get("trend"), "RSI": t.get("RSI14"), "距MA20%": None, "52週位置%": t.get("pos_in_52w_range_pct"),
                 "族群": "、".join(gl) or "-", "族群流向": "-", "日誌": "-", "旗標": ""}
            if not q.empty and c in set(q.code):
                x = q[q.code == c].iloc[0]
                r.update({"名稱": x["name"], "現價": round(float(x["last"]), 2), "漲跌%": round(float(x["chg_pct"]), 2)})
                ma20 = t.get("MA", {}).get("MA20")
                if ma20: r["距MA20%"] = round((x["last"] / ma20 - 1) * 100, 2)
                vma = t.get("vol_ma20_shares")
                if vma and x["vol_lots"]:
                    r["量比(換算)"] = round(float(x["vol_lots"]) * 1000 / prog / vma, 2)  # 全日換算，僅供參考、不觸發警示
                sv = sgd.get(c)
                if sv and pd.notna(sv.get("ratio")):
                    r["短窗量比"] = f"{sv['ratio']:.2f}x {sv['ret_pct']:+.1f}%" + ("" if sv["src"] == "snap" else "*")
                    if sv.get("label"):
                        tag = f"{sv['label']}（{int(sv['win_min'])}分 {sv['ratio']:.1f}x，窗口 {sv['ret_pct']:+.1f}%{'' if sv['src'] == 'snap' else '，延遲資料'}）"
                        flags.append(tag); alert_lines.append(f"{c} {x['name']} {tag}")
                if abs(r["漲跌%"] or 0) >= alerts["chg_pct_abs"]:
                    alert_lines.append(f"{c} {x['name']} 漲跌 {r['漲跌%']:+.2f}%")
                if r["距MA20%"] is not None and abs(r["距MA20%"]) <= alerts["near_level_pct"]:
                    alert_lines.append(f"{c} {x['name']} 貼近 MA20（{r['距MA20%']:+.2f}%）")
            elif t:
                r.update({"名稱": None, "現價": t.get("close"), "漲跌%": t.get("chg_pct_1d"), "量比(換算)": t.get("vol_ratio_20d")})
                if t.get("MA", {}).get("MA20"): r["距MA20%"] = round((t["close"] / t["MA"]["MA20"] - 1) * 100, 2)
            if len(fg) and gl:
                gs = fg[fg.group.isin(gl)]
                if len(gs):
                    key = "inst_M" if mode == "eod" else "flow_score"
                    r["族群流向"] = f"{gs.iloc[0]['group']} rank {int(fg.index[fg.group == gs.iloc[0]['group']][0]) + 1}/{len(fg)} ({key}={gs.iloc[0][key]})"
            j = jr.get(c)
            if j is not None:
                ent = float(j["entry_price"]); cur_p = r["現價"]
                pnl = f"{(cur_p/ent-1)*100:+.1f}%" if cur_p else "-"
                # 防守價：journal check 算好的 falsify_level（固定價、動態 MA、移動停利取最高），沒有才退回 falsify_price
                fp = j.get("falsify_level") if pd.notna(j.get("falsify_level")) else j.get("falsify_price")
                ts = j.get("trail_stop")
                lab = "移動停利" if pd.notna(ts) and pd.notna(fp) and float(ts) >= float(fp) else "證偽價"
                near = ""
                if pd.notna(fp) and fp and cur_p:
                    gap = (cur_p / float(fp) - 1) * 100
                    near = f"，距{lab} {fp} {gap:+.1f}%"
                    if cur_p < float(fp):
                        near = f"，**已跌破{lab}**"; alert_lines.append(f"{c} 跌破{lab} {fp}（#{int(j['id'])}）")
                    elif gap < 2:
                        alert_lines.append(f"{c} 距{lab}僅 {gap:+.1f}%（#{int(j['id'])}）")
                r["日誌"] = f"#{int(j['id'])} 持倉 @{ent} {pnl}{near}"
            r["旗標"] = ", ".join(flags)
            for f in flags:
                surge_tag = f[:2] in ("爆量", "放量") and "20日均量" not in f  # 短窗量比旗標，上面已提醒
                if any(k in f for k in ("交叉", "爆量", "新高", "跌破")) and not surge_tag:
                    alert_lines.append(f"{c} {f}")
            rows.append(r)
    wt = pd.DataFrame(rows)

    # 3) 組報告
    ts = now.strftime("%Y-%m-%d %H:%M")
    md = [f"# 盯盤報告 {ts}（{'盤中' if mode == 'intraday' else '盤後'}）"]
    try:  # 大盤環境（stock-risk，有裝才顯示）
        rk = find_skill("stock-risk"); sys.path.insert(0, str(rk)); import risklib
        md.append(risklib.regime_line(risklib.regime()))
    except SystemExit:
        pass
    except Exception as e:
        print(f"[warn] 大盤環境取得失敗：{e}")
    if flow and flow.get("market_turnover_M"):
        md.append(f"大盤成交值≈{flow['market_turnover_M']/100:.0f} 億（進度 {prog*100:.0f}%，換算全日≈{flow['market_turnover_M']/100/prog:.0f} 億）")
    n = alerts["group_top_n"]
    if len(fg):
        key_cols = (["group", "inst_M", "foreign_M", "trust_M", "share_vs_5d", "avg_chg", "top_buy"] if mode == "eod"
                    else ["group", "share_pct", "share_vs_5d", "avg_chg", "up", "down", "leaders"])
        md += ["\n## 族群流入", fg.head(n)[key_cols].to_markdown(index=False),
               "\n## 族群流出", fg.tail(n)[key_cols].iloc[::-1].to_markdown(index=False)]
    if len(sg):
        sn = alerts["surge_top_n"]
        top = sg[(sg.win_lots.fillna(0) >= alerts["surge_min_lots"])].sort_values("ratio", ascending=False).head(sn).copy()
        top["win_min"] = top["win_min"].astype("Int64")
        top["族群"] = ["、".join(group_of(c, groups)) or "-" for c in top.code]
        top = top.rename(columns={"code": "代號", "name": "名稱", "chg_pct": "今日%", "win_min": "窗口分", "win_lots": "窗口張",
                                  "exp_lots": "同時段均張", "ratio": "短窗量比", "ret_pct": "窗口%", "label": "標籤", "src": "來源"})
        delayed = (~sg.src.fillna("").eq("snap")).any()
        md += [f"\n## 盤中異動（族群清單+自選，短窗量比前 {sn}）",
               "短窗量比 = 最近 N 分鐘量 ÷ 過去 20 日同時段均量；≥{:g} 放量、≥{:g} 爆量，窗口漲跌決定流入/流出。".format(alerts["surge_warm"], alerts["surge_hot"])
               + ("\n來源 yf~HH:MM = 當天首次執行，用 yfinance 5 分 K（延遲約 20 分，資料到該時間）；15 分鐘後再跑即改用即時快照。" if delayed else ""),
               top[["代號", "名稱", "族群", "今日%", "窗口分", "窗口張", "同時段均張", "短窗量比", "窗口%", "標籤", "來源"]].to_markdown(index=False)]
    md += ["\n## 自選標的", wt.to_markdown(index=False)]
    if mode == "intraday":
        md.append("短窗量比：最近 N 分鐘量 ÷ 20 日同時段均量，後附窗口漲跌；* = 延遲資料。量比(換算) 為全日換算，僅供參考。")
    md += ["\n## 警示", *(f"- {l}" for l in alert_lines)] if alert_lines else ["\n## 警示\n- 無"]
    text = "\n".join(md)
    print(text)
    if a.out: Path(a.out).write_text(text, encoding="utf-8")
    if a.json: json.dump({"mode": mode, "time": ts, "groups": flow, "watchlist": rows, "alerts": alert_lines}, open(a.json, "w"), ensure_ascii=False, indent=2, default=str)


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""交易日誌：記進場論點與證偽條件 → 每日自動對答案 → 檢討統計 → rclone 同步到 Google Drive。

  python journal.py add 6239 --price 150 --qty 5 --type 族群流入 \
      --thesis "封測族群連 3 日資金流入，站回 MA20" --falsify "收盤跌破 142 或投信轉賣超 3 日" \
      --falsify-price 142 --days 20
  python journal.py close 3 --price 158 --reason 達標      # 3 = 交易編號；reason: 達標/證偽/情緒/其他
  python journal.py check                                   # 盤後：持倉現價、是否觸發證偽、寫當日筆記
  python journal.py review [--months 3]                     # 檢討報告 → review_YYYYMM.md
  python journal.py list [--all]
  python journal.py sync
本機檔案：trades.csv（統計用）、journal_YYYYMM.md（可讀版，按月分檔，含每日筆記）、review_*.md
"""
from __future__ import annotations
import argparse, math, csv, os, subprocess, sys
from datetime import date
from pathlib import Path
import pandas as pd
import yaml

HERE = Path(__file__).resolve().parent
_CFGP = HERE.parent / "assets" / "config.yaml"   # 個人設定（不進 git）；沒有就用範本
CFG = yaml.safe_load((_CFGP if _CFGP.exists() else _CFGP.with_name("config.example.yaml")).read_text(encoding="utf-8"))
LOCAL = Path(os.path.expanduser(CFG["local_dir"])); LOCAL.mkdir(parents=True, exist_ok=True)
CSV = LOCAL / "trades.csv"
COLS = ["id", "code", "name", "entry_date", "entry_price", "qty", "thesis_type", "thesis", "falsify", "falsify_price", "falsify_ma", "falsify_level",
        "plan_days", "status", "exit_date", "exit_price", "exit_reason", "pnl_pct", "held_days",
        "falsify_hit_date", "max_dd_pct", "max_up_pct", "last_price", "last_check", "bench_pct", "excess_pct", "base_price",
        "stop_init", "trail_stop", "cost_amt", "net_pnl_amt", "net_pnl_pct", "post5_pct", "post10_pct", "post_max_pct"]
# post5_pct／post10_pct＝出場後第 5／10 個交易日收盤相對出場價 %；post_max_pct＝出場後 10 日內最高收盤相對出場價 %（看是否賣飛）
# stop_init＝進場時的停損（算 R 用，之後不改）；trail_stop＝移動停利（只上移）
# cost_amt／net_pnl_amt／net_pnl_pct＝含手續費與證交稅的成本與淨損益（close 時依 config fees 計算，股價幣別）
TEXT_COLS = ["code", "name", "entry_date", "thesis_type", "thesis", "falsify", "status",
             "exit_date", "exit_reason", "falsify_hit_date", "last_check"]
NUM_COLS = ["entry_price", "qty", "falsify_price", "falsify_ma", "falsify_level", "plan_days", "exit_price", "pnl_pct",
            "held_days", "max_dd_pct", "max_up_pct", "last_price", "bench_pct", "excess_pct", "base_price", "stop_init", "trail_stop",
            "cost_amt", "net_pnl_amt", "net_pnl_pct", "post5_pct", "post10_pct", "post_max_pct"]


# ---------------------------------------------------------------- storage
def load() -> pd.DataFrame:
    if not CSV.exists():
        return pd.DataFrame(columns=COLS)
    df = pd.read_csv(CSV, dtype={"code": str})
    for c in COLS:
        if c not in df: df[c] = None
    df = df[COLS]
    for c in TEXT_COLS:                       # 文字欄強制 object，否則全空欄會被推成 float64
        df[c] = df[c].astype("object")
    for c in NUM_COLS:                        # 數值欄強制 float，避免 int64 欄位寫入小數時報錯
        df[c] = pd.to_numeric(df[c], errors="coerce")
    return df


def save(df: pd.DataFrame):
    df.to_csv(CSV, index=False, quoting=csv.QUOTE_MINIMAL)


def md_path(d: date | None = None) -> Path:
    return LOCAL / f"journal_{(d or date.today()):%Y%m}.md"


def md_append(text: str):
    """寫入當月日誌；新月份的檔案開頭先列出從上月帶過來的持倉，讓每個月的檔案可獨立閱讀。"""
    md = md_path()
    if not md.exists():
        head = [f"# 交易日誌 {date.today():%Y-%m}", ""]
        op = load(); op = op[op["status"] == "open"]
        if len(op):
            head += ["## 月初持倉（承上月）"] + [
                f"- #{r['id']} {r['code']} {r['name']}｜{r['thesis_type']}｜進場 {r['entry_date']} @ {r['entry_price']}｜證偽：{r['falsify']}"
                for _, r in op.iterrows()] + [""]
        md.write_text("\n".join(head) + "\n", encoding="utf-8")
    with md.open("a", encoding="utf-8") as f:
        f.write(text.rstrip() + "\n\n")


def sync(force=False):
    if not (force or CFG.get("auto_sync")):
        return
    if subprocess.run(["which", "rclone"], capture_output=True).returncode != 0:
        print("[warn] 未安裝 rclone，略過同步（見 references/rclone-setup.md）"); return
    dest = f"{CFG['rclone_remote']}:{CFG['rclone_path']}"
    cmd = ["rclone", "bisync", str(LOCAL), dest] if CFG.get("sync_mode") == "bisync" else ["rclone", "sync", str(LOCAL), dest, "--exclude", ".*"]
    r = subprocess.run(cmd, capture_output=True, text=True)
    print(f"[sync] {'ok → ' + dest if r.returncode == 0 else '失敗: ' + r.stderr.strip()[-200:]}")


# ---------------------------------------------------------------- price lookup（借 stock-ta 的資料層）
def _ta_dir() -> Path | None:
    for base in (os.environ.get("STOCK_TOOLBOX"), HERE.parent.parent, Path.home() / ".claude" / "skills"):
        if base and (Path(base) / "stock-ta" / "scripts" / "data.py").exists():
            return Path(base) / "stock-ta" / "scripts"
    return None


def prices(codes: list[str]) -> dict[str, dict]:
    d = _ta_dir()
    if not d:
        print("[warn] 找不到 stock-ta，無法自動取價"); return {}
    sys.path.insert(0, str(d))
    from data import fetch_ohlcv  # noqa
    out = {}
    for c in codes:
        try:
            df, sym = fetch_ohlcv(c, "3mo")
            out[c] = {"close": float(df["Close"].iloc[-1]), "date": str(df.index[-1].date()), "hist": df["Close"]}
        except SystemExit as e:
            print(f"[warn] {c}: {e}")
    return out


def _risk():
    """stock-risk 的 risklib（部位大小、移動停利）；沒裝就回 None，日誌照常運作。"""
    for base in (os.environ.get("STOCK_TOOLBOX"), HERE.parent.parent, Path.home() / ".claude" / "skills"):
        d = Path(base) / "stock-risk" / "scripts" if base else None
        if d and (d / "risklib.py").exists():
            if str(d) not in sys.path: sys.path.insert(0, str(d))
            import risklib  # noqa
            return risklib
    return None


def _shares(r) -> float:
    """台股 qty 以張計（×1000 股），美股以股計。"""
    return float(r["qty"] or 0) * (1000 if str(r["code"]).isdigit() else 1)


def trade_cost(r, exit_price: float, exit_date: str | None = None) -> dict:
    """一買一賣的交易成本與淨損益（股價幣別）。台股：手續費×折扣（每筆有最低），賣方證交稅，金額捨去到元。"""
    fc = CFG.get("fees", {}) or {}
    sh = _shares(r); buy, sell = float(r["entry_price"]) * sh, exit_price * sh
    if str(r["code"]).isdigit():
        fee = lambda amt: max(math.floor(amt * fc.get("tw_fee_rate", 0.001425) * fc.get("tw_fee_discount", 1.0)), fc.get("tw_fee_min", 20)) if amt else 0
        daytrade = exit_date is not None and str(exit_date) == str(r["entry_date"])
        rate = fc.get("tw_tax_etf", 0.001) if str(r["code"]).startswith("00") else \
            fc.get("tw_tax_daytrade", 0.0015) if daytrade else fc.get("tw_tax_stock", 0.003)
        cost = fee(buy) + fee(sell) + math.floor(sell * rate)
    else:
        cost = 2 * fc.get("us_fee_per_trade", 0) + (buy + sell) * fc.get("us_fee_rate", 0)
    net = sell - buy - cost
    return {"cost": round(cost, 2), "net": round(net, 2), "net_pct": round(net / buy * 100, 2) if buy else None}


def _net_r(r, net_amt: float) -> float | None:
    """含成本的 R 倍數：淨損益 ÷（每股 R × 股數）。"""
    s0 = r.get("stop_init")
    if pd.isna(s0) or float(r["entry_price"]) <= float(s0) or not _shares(r):
        return None
    return round(net_amt / ((float(r["entry_price"]) - float(s0)) * _shares(r)), 2)


def _r_mult(r, price: float) -> float | None:
    """以進場時的停損算 R 倍數。"""
    s0 = r.get("stop_init")
    if pd.isna(s0) or float(r["entry_price"]) <= float(s0):
        return None
    return round((price - float(r["entry_price"])) / (float(r["entry_price"]) - float(s0)), 2)


_BENCH: dict[str, pd.Series] = {}
LONGTERM = set(CFG.get("longterm_types", ["長期持有"]))   # 只追蹤、不進勝率/期望值統計、不做持有期提醒


def _base(r) -> float:
    """超額報酬的起算價：有 base_price（開始追蹤當日收盤，用於成本日期不明的長期持股）用它，否則用進場價。"""
    return float(r["base_price"]) if pd.notna(r["base_price"]) and r["base_price"] else float(r["entry_price"])


def _bench_ret(code: str, start: str, end: str) -> float | None:
    """同期大盤漲幅 %：進場日收盤 → end 收盤（台股 ^TWII、其他 ^GSPC，可在 config 改）。當日進出為 0。"""
    sym = CFG.get("benchmark_tw", "^TWII") if str(code).isdigit() else CFG.get("benchmark_us", "^GSPC")
    if sym not in _BENCH:
        try:
            import yfinance as yf
            h = yf.Ticker(sym).history(period="2y")["Close"]; h.index = pd.to_datetime(h.index).tz_localize(None)
            _BENCH[sym] = h
        except Exception as e:
            print(f"[warn] 大盤 {sym} 取價失敗：{e}"); _BENCH[sym] = pd.Series(dtype=float)
    h = _BENCH[sym]; a, b = h[h.index <= pd.Timestamp(start)], h[h.index <= pd.Timestamp(end)]
    if a.empty or b.empty: return None
    return round((b.iloc[-1] / a.iloc[-1] - 1) * 100, 2)


# ---------------------------------------------------------------- commands
def cmd_add(a):
    df = load()
    tid = int(df["id"].max()) + 1 if len(df) else 1
    row = {"id": tid, "code": a.code.zfill(4) if a.code.isdigit() else a.code.upper(), "name": a.name or "", "entry_date": a.date or date.today().isoformat(),
           "entry_price": a.price, "qty": a.qty, "thesis_type": a.type, "thesis": a.thesis, "falsify": a.falsify,
           "falsify_price": a.falsify_price, "falsify_ma": a.falsify_ma, "base_price": a.base_price, "plan_days": a.days, "status": "open", "last_price": a.price, "max_dd_pct": 0.0, "max_up_pct": 0.0,
           "stop_init": a.stop_init or a.falsify_price}   # 只有 MA 證偽時，stop_init 在第一次 check 以進場日 MA 補上
    df = pd.concat([df, pd.DataFrame([row])], ignore_index=True); save(df)
    unit = f"{a.qty:g} 張" if row["code"].isdigit() else f"{a.qty:g} 股"
    md_append(f"## #{tid} {row['code']} {row['name']} 進場 {row['entry_date']} @ {a.price} × {unit}\n"
              f"- 論點類型：{a.type}\n- 論點：{a.thesis}\n- 證偽條件：{a.falsify}" + (f"（價位 {a.falsify_price}）" if a.falsify_price else "") +
              (f"（動態 MA{a.falsify_ma}" + ("，取與價位較高者" if a.falsify_price else "") + "）" if a.falsify_ma else "") +
              (f"\n- 預計持有：長期" if a.type in LONGTERM else f"\n- 預計持有：{a.days} 個交易日"))
    print(f"[add] #{tid} {row['code']} 已記錄")
    rl = _risk(); s0 = row["stop_init"]
    if rl and s0 and a.type not in LONGTERM and s0 < a.price:
        try:
            s = rl.size(row["code"], a.price, float(s0))
            mult = 1000 if row["code"].isdigit() else 1
            actual = a.qty * mult; risk_amt = actual * (a.price - s0) * rl.fx(row["code"])
            print(rl.size_text(s))
            msg = (f"- 部位檢查：實際 {actual:,.0f} 股、風險 {risk_amt:,.0f} 元（{risk_amt / s['risk_amt_1R']:.2f}R）；建議 {s['shares']:,} 股"
                   + ("；⚠️ 超過建議部位" if actual > s["shares"] * 1.1 else ""))
            print(msg); md_append(msg)
        except Exception as e:
            print(f"[warn] 部位檢查失敗：{e}")
    sync()


def cmd_close(a):
    df = load(); i = df.index[df["id"] == a.id]
    if not len(i): raise SystemExit(f"找不到 #{a.id}")
    i = i[0]; r = df.loc[i]
    if r["status"] == "closed": raise SystemExit(f"#{a.id} 已平倉")
    exit_date = a.date or date.today().isoformat()
    pnl = (a.price / float(r["entry_price"]) - 1) * 100
    held = len(pd.bdate_range(r["entry_date"], exit_date)) - 1
    bench = _bench_ret(r["code"], r["entry_date"], exit_date)
    excess = round((a.price / _base(r) - 1) * 100 - bench, 2) if bench is not None else None
    tc = trade_cost(r, a.price, exit_date)
    for k, v in zip(["status", "exit_date", "exit_price", "exit_reason", "pnl_pct", "held_days", "bench_pct", "excess_pct",
                     "cost_amt", "net_pnl_amt", "net_pnl_pct"],
                    ["closed", exit_date, a.price, a.reason, round(pnl, 2), held, bench, excess, tc["cost"], tc["net"], tc["net_pct"]]):
        df.at[i, k] = v
    if a.reason in ("證偽", "停利") and not (pd.notna(r["falsify_hit_date"]) and r["falsify_hit_date"]):
        df.at[i, "falsify_hit_date"] = exit_date; r = df.loc[i]   # 盤中觸發當天出場、check 還沒跑：以出場日為觸發日（lag 0）
    save(df)
    disc = ""
    if pd.notna(r["falsify_hit_date"]) and r["falsify_hit_date"]:
        lag = len(pd.bdate_range(r["falsify_hit_date"], exit_date)) - 1
        disc = f"\n- 證偽條件於 {r['falsify_hit_date']} 觸發，{lag} 個交易日後出場（{'守紀律' if lag <= CFG['falsify_grace_days'] else '拖延'}）"
    rm = _r_mult(r, a.price); nr = _net_r(r, tc["net"])
    cost_line = (f"\n- 含成本：淨損益 {tc['net']:+,.0f}（{tc['net_pct']:+.2f}%" + (f"，{nr:+.2f}R" if nr is not None else "")
                 + f"），成本 {tc['cost']:,.0f}（手續費＋證交稅）")
    md_append(f"## #{a.id} {r['code']} {r['name']} 出場 {exit_date} @ {a.price}（{a.reason}）\n"
              f"- 損益 {pnl:+.2f}%" + (f"（{rm:+.2f}R）" if rm is not None else "") + (f"（同期大盤 {bench:+.2f}%，超額 {excess:+.2f}）" if bench is not None else "") + f"，持有 {held} 日（預計 {r['plan_days']}）{disc}{cost_line}\n- 備註：{a.note or '-'}")
    print(f"[close] #{a.id} {pnl:+.2f}%（含成本 {tc['net']:+,.0f}，{tc['net_pct']:+.2f}%）"); sync()


def _falsify_level(r, hist: pd.Series) -> float | None:
    """當日有效證偽價：固定價位、MA(n)，兩者都有取較高者（例：守 153，MA5 上來後改守 MA5）。"""
    fixed = float(r["falsify_price"]) if pd.notna(r["falsify_price"]) and r["falsify_price"] else None
    n = int(r["falsify_ma"]) if pd.notna(r["falsify_ma"]) and r["falsify_ma"] else 0
    ma = round(float(hist.rolling(n).mean().iloc[-1]), 2) if n and len(hist) >= n else None
    levels = [x for x in (fixed, ma) if x]
    return max(levels) if levels else None


POST_DAYS = (5, 10)


def _post_exit(df: pd.DataFrame) -> list[str]:
    """已平倉單出場後追蹤：第 5／10 個交易日收盤與 10 日內最高收盤相對出場價。填滿第 10 日後不再追。"""
    cl = df[(df["status"] == "closed") & df["exit_date"].notna() & df["exit_price"].notna() & df["post10_pct"].isna()]
    cl = cl[pd.to_datetime(cl["exit_date"]) >= pd.Timestamp.today() - pd.DateOffset(days=80)]   # prices() 只抓 3 個月日線，更舊的單不回補
    if cl.empty: return []
    px = prices(cl["code"].unique().tolist()); lines = []
    for i, r in cl.iterrows():
        p = px.get(r["code"])
        if not p: continue
        h = p["hist"].copy(); h.index = pd.to_datetime(h.index).tz_localize(None)
        after = h[h.index > pd.Timestamp(r["exit_date"])].iloc[:POST_DAYS[-1]]
        if after.empty: continue
        ex = float(r["exit_price"]); pct = lambda v: round((float(v) / ex - 1) * 100, 2)
        df.at[i, "post_max_pct"] = pct(after.max())
        for n, col in zip(POST_DAYS, ("post5_pct", "post10_pct")):
            if len(after) >= n and pd.isna(r[col]):
                df.at[i, col] = pct(after.iloc[n - 1])
                lines.append(f"- 出場後追蹤 #{r['id']} {r['code']} {r['name']}（{r['exit_date']} @ {ex}，{r['exit_reason']}）：第 {n} 日 {round(float(after.iloc[n - 1]), 2)}（{pct(after.iloc[n - 1]):+.2f}%），期間最高收盤 {pct(after.max()):+.2f}%")
    return lines


def cmd_check(a):
    df = load(); op = df[df["status"] == "open"]
    post = _post_exit(df)
    if op.empty:
        if post:
            save(df); md_append("\n".join([f"## 每日檢查 {date.today().isoformat()}"] + post)); print("\n".join(post)); sync()
        print("無持倉"); return
    px = prices(op["code"].unique().tolist())
    rl = _risk()
    today = date.today().isoformat(); lines = [f"## 每日檢查 {today}"]
    if rl:
        try:
            lines.append("- " + rl.regime_line(rl.regime()))
        except Exception as e:
            print(f"[warn] 大盤環境取得失敗：{e}")
    for i, r in op.iterrows():
        p = px.get(r["code"])
        if not p: continue
        entry = float(r["entry_price"]); cur = round(p["close"], 2)
        hist = p["hist"][p["hist"].index >= pd.Timestamp(r["entry_date"])]
        up = (hist.max() / entry - 1) * 100 if len(hist) else 0
        dd = (hist.min() / entry - 1) * 100 if len(hist) else 0
        held = len(pd.bdate_range(r["entry_date"], today)) - 1
        df.at[i, "last_price"] = cur; df.at[i, "last_check"] = p["date"]
        df.at[i, "max_up_pct"] = round(up, 2); df.at[i, "max_dd_pct"] = round(dd, 2)
        bench = _bench_ret(r["code"], r["entry_date"], p["date"]); pnl = (cur / _base(r) - 1) * 100
        df.at[i, "bench_pct"] = bench; df.at[i, "excess_pct"] = round(pnl - bench, 2) if bench is not None else None
        bstr = f"，大盤 {bench:+.2f}% / 超額 {pnl - bench:+.2f}" if bench is not None else ""
        flag = ""
        level = _falsify_level(r, p["hist"])
        trail_txt, kind = "", "證偽"
        if rl and r["thesis_type"] not in LONGTERM:
            try:
                o = rl.ohlcv(r["code"], "1y")
                if pd.isna(r["stop_init"]):              # 只有 MA 證偽：用進場日的 MA 當初始停損
                    n = int(r["falsify_ma"]) if pd.notna(r["falsify_ma"]) else 0
                    ma = o["Close"].rolling(n).mean() if n else None
                    s0 = float(ma[ma.index <= pd.Timestamp(r["entry_date"])].iloc[-1]) if ma is not None else level
                    if s0 and s0 < entry: df.at[i, "stop_init"] = round(s0, 2); r = df.loc[i]
                if pd.notna(r["stop_init"]):
                    t = rl.trail(entry, float(r["stop_init"]), r["entry_date"], o)
                    prev = float(r["trail_stop"]) if pd.notna(r["trail_stop"]) else None
                    if t["trail"] and (prev is None or t["trail"] > prev):
                        df.at[i, "trail_stop"] = t["trail"]
                        lv = "、".join(f"{k} {v}" for k, v in t["levels"].items())
                        md_append(f"- #{r['id']} {r['code']} 移動停利 {'啟動' if prev is None else '上調'} → {t['trail']}（{lv}；最高已達 {t['r_max']:+.2f}R）")
                    ts = df.at[i, "trail_stop"]
                    trail_txt = f"，{t['r_now']:+.2f}R" + (f"，移動停利 {ts}" if pd.notna(ts) else f"，停利啟動價 {t.get('activate_at')}")
                    if pd.notna(ts) and (level is None or ts > level):
                        level, kind = float(ts), "移動停利"
            except Exception as e:
                print(f"[warn] #{r['id']} 移動停利計算失敗：{e}")
        df.at[i, "falsify_level"] = level
        if level and cur < level and not (pd.notna(r["falsify_hit_date"]) and r["falsify_hit_date"]):
            df.loc[i, "falsify_hit_date"] = today; flag = f" ⚠️ **{'跌破移動停利' if kind == '移動停利' else '證偽價位觸發'}**（{level}）"
        elif pd.notna(r["falsify_hit_date"]) and r["falsify_hit_date"]:
            flag = f" ⚠️ 證偽已於 {r['falsify_hit_date']} 觸發，尚未出場"
        if r["thesis_type"] not in LONGTERM and r["plan_days"] and held > int(r["plan_days"]):
            flag += f" ⏱ 超過預計持有期（{held}/{r['plan_days']}）"
        lt = "【長期】" if r["thesis_type"] in LONGTERM else ""
        lines.append(f"- {lt}#{r['id']} {r['code']} {r['name']}：{cur}（{(cur/entry-1)*100:+.2f}%{bstr}，最高 {up:+.1f}% / 最低 {dd:+.1f}%，持有 {held} 日{('，防守 ' + str(level)) if level else ''}{trail_txt}）{flag}")
    lines += post
    save(df); md_append("\n".join(lines)); print("\n".join(lines)); sync()


def _acct_1r() -> float | None:
    """帳戶 1R 金額（台幣）＝ stock-risk 的 capital × risk_pct%，不含大盤係數，讓不同時期的單可比。"""
    rl = _risk()
    c = getattr(rl, "CFG", None) if rl else None
    return float(c["capital"]) * float(c["risk_pct"]) / 100 if c and c.get("capital") and c.get("risk_pct") else None


def _acct_stats(cl: pd.DataFrame, one_r: float | None) -> str:
    """帳戶 R：含成本淨損益（換台幣）÷ 帳戶 1R。看實際傷害；單筆 R 看紀律，部位不足額時兩者差很多。"""
    if not one_r or cl.acct_r.isna().all():
        return "- 帳戶 R：無（未安裝 stock-risk 或未設定 capital／risk_pct）"
    ar = cl.acct_r.dropna(); los = ar[ar < 0]
    return (f"- 帳戶 R（1R＝{one_r:,.0f} 元，含成本）：合計 {ar.sum():+.2f}R，平均 {ar.mean():+.2f}R／筆，"
            f"最大單筆虧損 {los.min() if len(los) else 0:+.2f}R；虧損超過 1 帳戶 R 的 {int((ar < -1).sum())} 筆")


def _acct_r_at(r, price: float, one_r: float, base: float | None = None) -> float:
    """假設在 price 出場的帳戶 R（含一買一賣成本、換台幣）。base 給長期持股的起算價用。"""
    rr = r.copy()
    if base is not None: rr["entry_price"] = base
    rl = _risk()
    return round(trade_cost(rr, price)["net"] * (rl.fx(r["code"]) if rl else 1.0) / one_r, 2)


def _open_stats(df: pd.DataFrame, one_r: float | None, realized: float | None) -> list[str]:
    """未平倉交易單（不含長期持有）：現在、停損（證偽價）、停利（移動停利）三個價位出場時的帳戶 R，皆含成本。
    價格用最近一次 check 的 last_price。證偽價＝固定價；只有 MA 證偽時用 check 存的 falsify_level（若它不是移動停利）。"""
    op = df[(df["status"] == "open") & ~df["thesis_type"].isin(LONGTERM) & df["last_price"].notna()]
    out = ["", "## 未平倉交易單（以最近一次 check 收盤計，含估計出場成本）"]
    if op.empty or not one_r:
        return out + ["- 無" if op.empty else "- 帳戶 R：無（未設定 capital／risk_pct）"]
    rows = []
    for _, r in op.iterrows():
        last = float(r["last_price"]); lv = r["falsify_level"]; ts = r["trail_stop"]
        stop = float(r["falsify_price"]) if pd.notna(r["falsify_price"]) else \
            (float(lv) if pd.notna(lv) and not (pd.notna(ts) and float(lv) == float(ts)) else None)
        trail = float(ts) if pd.notna(ts) else None
        act = next((x for x in (stop, trail) if x is not None and x == max(y for y in (stop, trail) if y is not None)), None)
        rows.append({"id": r["id"], "code": r["code"], "name": r["name"], "entry": float(r["entry_price"]), "last": last,
                     "現在R": _acct_r_at(r, last, one_r),
                     "停損價": stop, "停損R": _acct_r_at(r, stop, one_r) if stop else None,
                     "停利價": trail, "停利R": _acct_r_at(r, trail, one_r) if trail else None,
                     "先觸發": ("停利" if act == trail and trail is not None else "停損") if act else "—",
                     "單筆R": _r_mult(r, last)})
    t = pd.DataFrame(rows)
    t["防守R"] = t.apply(lambda x: x["停利R"] if x["先觸發"] == "停利" else x["停損R"], axis=1)
    ur = t["現在R"].sum()
    out += [f"- 現在出場合計 {ur:+.2f} 帳戶 R" + (f"；加上已實現 {realized:+.2f}R，合計 {realized + ur:+.2f}R" if realized is not None else ""),
            f"- 全部在目前防守價（停損／停利較高者）出場：合計 {t['防守R'].dropna().sum():+.2f}R（相對現在 {t['防守R'].dropna().sum() - t.loc[t['防守R'].notna(), '現在R'].sum():+.2f}R）"
            + ("；無防守價：" + "、".join(f"#{i}" for i in t[t['防守R'].isna()].id) if t["防守R"].isna().any() else ""),
            t.drop(columns=["防守R"]).to_markdown(index=False)]
    return out


def _longterm_stats(df: pd.DataFrame, one_r: float | None, months: int | None) -> list[str]:
    """長期持有（review --longterm 才列）：未平倉以 base_price（沒有就用進場價）起算的未實現，加上期間內已平倉的已實現，皆含成本。"""
    lt = df[df["thesis_type"].isin(LONGTERM)]
    cl = lt[lt["status"] == "closed"]
    if months: cl = cl[pd.to_datetime(cl["exit_date"]) >= pd.Timestamp.today() - pd.DateOffset(months=months)]
    op = lt[(lt["status"] == "open") & lt["last_price"].notna()]
    out = ["", "## 長期持有（未平倉自開始追蹤日起算，含估計出場成本）"]
    if not one_r or (op.empty and cl.empty):
        return out + ["- 無"]
    rows = [{"id": r["id"], "code": r["code"], "name": r["name"], "狀態": "持有", "起算": _base(r), "價格": float(r["last_price"]),
             "帳戶R": _acct_r_at(r, float(r["last_price"]), one_r, _base(r))} for _, r in op.iterrows()]
    rows += [{"id": r["id"], "code": r["code"], "name": r["name"], "狀態": f"{r['exit_date']} 出場", "起算": float(r["entry_price"]),
              "價格": float(r["exit_price"]), "帳戶R": _acct_r_at(r, float(r["exit_price"]), one_r)} for _, r in cl.iterrows()]
    t = pd.DataFrame(rows)
    return out + [f"- 合計 {t['帳戶R'].sum():+.2f} 帳戶 R（開始追蹤前的漲跌不含在內）", t.to_markdown(index=False)]


def _r_stats(cl: pd.DataFrame) -> str:
    """R 倍數統計：虧損有沒有控制在 1R 內、平均賺幾 R。"""
    rm = cl.apply(lambda r: _r_mult(r, float(r["exit_price"])), axis=1).dropna()
    if rm.empty:
        return "- R 倍數：無（沒有記錄初始停損的交易）"
    over = rm[rm < -1.2]
    acct = ""
    if len(over) and "acct_r" in cl and cl.loc[over.index, "acct_r"].notna().any():   # 單筆 R 超標，但部位小時帳戶傷害有限
        acct = "；超標單的帳戶 R：" + "、".join(f"#{cl.at[k, 'id']} {cl.at[k, 'acct_r']:+.2f}" for k in over.index if pd.notna(cl.at[k, "acct_r"]))
    return (f"- R 倍數（{len(rm)} 筆有初始停損）：平均 {rm.mean():+.2f}R，獲利單平均 {rm[rm > 0].mean() if (rm > 0).any() else 0:+.2f}R，"
            f"虧損單平均 {rm[rm <= 0].mean() if (rm <= 0).any() else 0:+.2f}R；虧損超過 1.2R 的 {len(over)} 筆" + ("（停損失守，看是跳空還是拖延）" if len(over) else "（停損有守住）") + acct)


def _post_stats(cl: pd.DataFrame) -> list[str]:
    """出場後走勢：出場後漲＝可能賣早，跌＝出對。只統計已滿 N 日的單，依出場原因分開。"""
    out = ["", "## 出場後走勢（相對出場價）"]
    p5, p10 = cl[cl.post5_pct.notna()], cl[cl.post10_pct.notna()]
    if p5.empty:
        return out + ["- 尚無出場滿 5 個交易日的追蹤資料（check 會自動補）"]
    f = lambda s: f"平均 {s.mean():+.2f}%，續漲 {(s > 0).mean()*100:.0f}%（{int((s > 0).sum())}/{len(s)}）"
    out.append(f"- 第 5 日：{f(p5.post5_pct)}" + (f"；第 10 日：{f(p10.post10_pct)}" if len(p10) else "；第 10 日：尚無資料"))
    out.append(f"- 10 日內最高收盤平均高於出場價 {p5.post_max_pct.mean():+.2f}%")
    g = p5.groupby("exit_reason").agg(n=("id", "count"), post5=("post5_pct", "mean"), post10=("post10_pct", "mean"), post_max=("post_max_pct", "mean")).round(2)
    out.append(g.to_markdown())
    out.append("- n < 5 的原因別不下結論；「證偽／停利」出場後續漲多，檢查防守是否太緊，「情緒」出場後續漲多，就是賣早")
    return out


def cmd_review(a):
    df = load(); cl = df[(df["status"] == "closed") & ~df["thesis_type"].isin(LONGTERM)].copy()   # 長期持股不進交易統計
    if a.months:
        cl = cl[pd.to_datetime(cl["exit_date"]) >= pd.Timestamp.today() - pd.DateOffset(months=a.months)]
    if cl.empty:
        print("沒有已平倉交易"); return
    cl["pnl_pct"] = cl["pnl_pct"].astype(float); cl["win"] = cl["pnl_pct"] > 0
    for i, r in cl[cl["net_pnl_amt"].isna()].iterrows():   # 舊資料沒存成本：依現行費率補算
        tc = trade_cost(r, float(r["exit_price"]), r["exit_date"])
        cl.loc[i, ["cost_amt", "net_pnl_amt", "net_pnl_pct"]] = [tc["cost"], tc["net"], tc["net_pct"]]
    cl["net_r"] = cl.apply(lambda r: _net_r(r, float(r["net_pnl_amt"])), axis=1)
    one_r = _acct_1r(); rl = _risk()
    cl["acct_r"] = cl.apply(lambda r: round(float(r["net_pnl_amt"]) * (rl.fx(r["code"]) if rl else 1.0) / one_r, 2)
                            if one_r and pd.notna(r["net_pnl_amt"]) else None, axis=1)
    tw = cl[cl["code"].astype(str).str.isdigit()]
    w, l = cl[cl.win], cl[~cl.win]
    def _lag(r):
        return (len(pd.bdate_range(r["falsify_hit_date"], r["exit_date"])) - 1) if pd.notna(r["falsify_hit_date"]) and r["falsify_hit_date"] else None
    cl["falsify_lag"] = cl.apply(_lag, axis=1)
    hit = cl[cl["falsify_lag"].notna()]
    out = [f"# 操作檢討 {date.today()}（{len(cl)} 筆已平倉）", "",
           "## 總覽",
           f"- 勝率 {cl.win.mean()*100:.0f}%，平均獲利 {w.pnl_pct.mean() if len(w) else 0:+.2f}%，平均虧損 {l.pnl_pct.mean() if len(l) else 0:+.2f}%，"
           f"賺賠比 {abs(w.pnl_pct.mean()/l.pnl_pct.mean()) if len(w) and len(l) else float('nan'):.2f}，期望值 {cl.pnl_pct.mean():+.2f}%/筆",
           (f"- 同期大盤平均 {cl.bench_pct.mean():+.2f}%，平均超額報酬 {cl.excess_pct.mean():+.2f} 個百分點，贏大盤 {(cl.excess_pct > 0).mean()*100:.0f}%（{int((cl.excess_pct > 0).sum())}/{int(cl.excess_pct.notna().sum())}）"
            if cl.excess_pct.notna().any() else "- 同期大盤：無資料"),
           f"- 平均持有 {cl.held_days.mean():.1f} 日 vs 預計 {cl.plan_days.astype(float).mean():.1f} 日",
           (f"- 證偽條件觸發 {len(hit)} 筆，{int((hit.falsify_lag <= CFG['falsify_grace_days']).sum())} 筆在 {CFG['falsify_grace_days']} 日內出場（紀律率 {(hit.falsify_lag <= CFG['falsify_grace_days']).mean()*100:.0f}%）"
            + (f"；拖延出場者平均多虧 {hit[hit.falsify_lag > CFG['falsify_grace_days']].pnl_pct.mean() - hit[hit.falsify_lag <= CFG['falsify_grace_days']].pnl_pct.mean():+.2f} 個百分點"
               if (hit.falsify_lag > CFG['falsify_grace_days']).any() and (hit.falsify_lag <= CFG['falsify_grace_days']).any() else "")
            if len(hit) else "- 本期無交易觸發證偽條件（若持有期間從未接近證偽價位，檢查條件是否訂得太寬）"),
           _r_stats(cl),
           _acct_stats(cl, one_r),
           f"- 含成本：期望值 {cl.net_pnl_pct.mean():+.2f}%/筆（毛 {cl.pnl_pct.mean():+.2f}%），淨勝率 {(cl.net_pnl_pct > 0).mean()*100:.0f}%"
           + (f"，淨 R 平均 {cl.net_r.dropna().mean():+.2f}R" if cl.net_r.notna().any() else "")
           + (f"；台股合計淨損益 {tw.net_pnl_amt.sum():+,.0f} 元、成本 {tw.cost_amt.sum():,.0f} 元" if len(tw) else ""),
           f"- 出場原因：{cl.exit_reason.value_counts().to_dict()}"
           + (f"；「情緒」出場 {int((cl.exit_reason=='情緒').sum())} 筆，平均損益 {cl[cl.exit_reason=='情緒'].pnl_pct.mean():+.2f}%" if (cl.exit_reason == '情緒').any() else ""),
           "", "## 依論點類型"]
    g = cl.groupby("thesis_type").agg(n=("id", "count"), win=("win", "mean"), avg=("pnl_pct", "mean"), excess=("excess_pct", "mean"), held=("held_days", "mean"))
    g["win"] = (g["win"] * 100).round(0); g["avg"] = g["avg"].round(2); g["excess"] = g["excess"].round(2); g["held"] = g["held"].round(1)
    out.append(g.sort_values("avg", ascending=False).to_markdown())
    # 賣飛／抱過頭：最高浮盈 vs 實際出場
    cl["left_on_table"] = cl["max_up_pct"].astype(float) - cl["pnl_pct"]
    out += ["", "## 賣飛與抱過頭",
            f"- 平均「最高浮盈 − 實際獲利」 {cl.left_on_table.mean():+.2f} 個百分點（高＝常從獲利抱回來）",
            f"- 最高浮盈曾 >5% 但最終虧損的：{int(((cl.max_up_pct.astype(float) > 5) & ~cl.win).sum())} 筆"]
    out += _post_stats(cl)
    out += _open_stats(df, one_r, round(float(cl.acct_r.sum()), 2) if cl.acct_r.notna().any() else None)
    if getattr(a, "longterm", False):
        out += _longterm_stats(df, one_r, a.months)
    out += ["", "## 逐筆", cl[["id", "code", "name", "thesis_type", "entry_date", "exit_date", "pnl_pct", "net_pnl_pct", "net_pnl_amt", "acct_r", "bench_pct", "excess_pct", "held_days", "plan_days", "exit_reason", "falsify_lag", "post5_pct", "post10_pct", "post_max_pct"]].to_markdown(index=False)]
    text = "\n".join(out); path = LOCAL / f"review_{date.today():%Y%m}.md"
    path.write_text(text, encoding="utf-8"); print(text); print(f"\n[review] {path}"); sync()


def cmd_list(a):
    df = load()
    if not a.all: df = df[df["status"] == "open"]
    print(df[["id", "code", "name", "entry_date", "entry_price", "last_price", "bench_pct", "excess_pct", "thesis_type", "falsify_price", "falsify_ma", "stop_init", "trail_stop", "falsify_level", "falsify_hit_date", "status", "pnl_pct"]].to_string(index=False) if len(df) else "無資料")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(); sp = ap.add_subparsers(dest="cmd", required=True)
    p = sp.add_parser("add"); p.add_argument("code"); p.add_argument("--price", type=float, required=True); p.add_argument("--qty", type=float, default=1)
    p.add_argument("--type", default="其他", choices=CFG["thesis_types"]); p.add_argument("--thesis", required=True); p.add_argument("--falsify", required=True)
    p.add_argument("--falsify-price", type=float); p.add_argument("--falsify-ma", type=int); p.add_argument("--stop-init", type=float, help="算 R 用的初始停損（預設＝--falsify-price）"); p.add_argument("--base-price", type=float); p.add_argument("--days", type=int, default=20); p.add_argument("--name", default=""); p.add_argument("--date")
    p = sp.add_parser("close"); p.add_argument("id", type=int); p.add_argument("--price", type=float, required=True)
    p.add_argument("--reason", default="其他", choices=["達標", "停利", "證偽", "情緒", "其他"]); p.add_argument("--note"); p.add_argument("--date")
    sp.add_parser("check"); p = sp.add_parser("review"); p.add_argument("--months", type=int); p.add_argument("--longterm", action="store_true", help="另列長期持有")
    p = sp.add_parser("list"); p.add_argument("--all", action="store_true"); sp.add_parser("sync")
    a = ap.parse_args()
    {"add": cmd_add, "close": cmd_close, "check": cmd_check, "review": cmd_review, "list": cmd_list, "sync": lambda a: sync(True)}[a.cmd](a)

"""stock-risk 共用層：大盤環境（regime）、部位大小（size）、移動停利（trail）。
資料全免費：yfinance（指數、個股日線，經 stock-ta 資料層）、證交所 FMTQIK／MI_INDEX、櫃買每日彙總。
stock-journal 與 stock-watch 會 import 這個檔。
"""
from __future__ import annotations
import json, math, os, re, sys, time
from datetime import date, datetime
from pathlib import Path
import numpy as np
import pandas as pd
import requests
import yaml

HERE = Path(__file__).resolve().parent
def _load_cfg(assets: Path) -> dict:
    """個人設定 config.yaml（不進 git）；沒有就用 config.example.yaml。"""
    p = assets / "config.yaml"
    return yaml.safe_load((p if p.exists() else assets / "config.example.yaml").read_text(encoding="utf-8"))


CFG = _load_cfg(HERE.parent / "assets")
CACHE = Path(os.environ.get("STOCK_CACHE", Path.home() / ".cache" / "stock-toolbox")) / "risk"
CACHE.mkdir(parents=True, exist_ok=True)
UA = {"User-Agent": "Mozilla/5.0", "Accept": "application/json"}


def _skill(name: str, sub: str = "scripts") -> Path:
    for base in (os.environ.get("STOCK_TOOLBOX"), HERE.parent.parent, Path.home() / ".claude" / "skills"):
        if base and (Path(base) / name / sub).exists():
            return Path(base) / name / sub
    raise SystemExit(f"找不到 {name} skill；請設定 STOCK_TOOLBOX")


def ohlcv(code: str, period: str = "1y") -> pd.DataFrame:
    """日線（stock-common 共用資料層：自動 .TW/.TWO、快取、剔除 NaN 棒）。"""
    d = str(_skill("stock-common", sub=""))
    if d not in sys.path:
        sys.path.insert(0, d)
    import logging
    logging.getLogger("yfinance").setLevel(logging.CRITICAL)
    from stockdata import fetch_ohlcv  # noqa: E402
    return fetch_ohlcv(code, period)[0]


_FX: dict[str, float] = {}


def fx(code: str) -> float:
    """部位市值換成台幣的匯率：台股 1；其他視為美股，用 yfinance TWD=X。"""
    if str(code).isdigit():
        return 1.0
    if "USD" not in _FX:
        try:
            _FX["USD"] = float(ohlcv("TWD=X", "1mo")["Close"].iloc[-1])
        except SystemExit:
            print("[warn] 取不到美元匯率，暫用 32"); _FX["USD"] = 32.0
    return _FX["USD"]


def _jcfg() -> dict:
    return _load_cfg(_skill("stock-journal").parent / "assets")


def atr(df: pd.DataFrame, n: int = 14) -> pd.Series:
    pc = df["Close"].shift()
    tr = pd.concat([df["High"] - df["Low"], (df["High"] - pc).abs(), (df["Low"] - pc).abs()], axis=1).max(axis=1)
    return tr.rolling(n).mean()


def _roc(s: str) -> date:
    y, m, d = map(int, s.strip().split("/"))
    return date(y + 1911, m, d)


def _num(s) -> float:
    return float(str(s).replace(",", "").strip() or 0)


# ---------------------------------------------------------------- 大盤環境
def fmtqik(months: int = 3) -> pd.DataFrame:
    """證交所每日市場成交資訊：date, amount_bn(億), index, chg。過去月份永久快取，當月 30 分鐘。"""
    rows = []
    first = date.today().replace(day=1)
    for k in range(months - 1, -1, -1):
        m = (pd.Timestamp(first) - pd.DateOffset(months=k)).date()
        p = CACHE / f"fmtqik_{m:%Y%m}.json"
        cur = m.year == date.today().year and m.month == date.today().month
        if p.exists() and (not cur or time.time() - p.stat().st_mtime < 1800):
            data = json.loads(p.read_text())
        else:
            try:
                js = requests.get("https://www.twse.com.tw/rwd/zh/afterTrading/FMTQIK",
                                  params={"date": f"{m:%Y%m}01", "response": "json"}, headers=UA, timeout=20).json()
                data = js.get("data", []) if js.get("stat") == "OK" else []
                if data:
                    p.write_text(json.dumps(data, ensure_ascii=False))
            except Exception as e:
                print(f"[warn] FMTQIK {m:%Y%m}: {e}"); data = []
            time.sleep(0.5)
        for r in data:  # 日期, 成交股數, 成交金額, 成交筆數, 加權指數, 漲跌點數
            rows.append({"date": _roc(r[0]), "amount_bn": _num(r[2]) / 1e8, "index": _num(r[4]), "chg": _num(r[5])})
    return pd.DataFrame(rows).drop_duplicates("date").sort_values("date").reset_index(drop=True)


def breadth(d: date) -> tuple[int, int] | None:
    """某日上市＋上櫃「股票」上漲／下跌家數。有資料才永久快取。"""
    p = CACHE / f"breadth_{d:%Y%m%d}.json"
    if p.exists():
        return tuple(json.loads(p.read_text()))
    up = dn = 0
    try:
        js = requests.get("https://www.twse.com.tw/rwd/zh/afterTrading/MI_INDEX",
                          params={"date": f"{d:%Y%m%d}", "type": "MS", "response": "json"}, headers=UA, timeout=20).json()
        t = next(t for t in js.get("tables", []) if "漲跌證券數" in t.get("title", ""))
        col = t["fields"].index("股票")
        get = lambda lab: int(re.sub(r"\(.*\)", "", next(r for r in t["data"] if r[0].startswith(lab))[col]).replace(",", ""))
        up, dn = get("上漲"), get("下跌")
        time.sleep(0.5)
        js = requests.get("https://www.tpex.org.tw/web/stock/aftertrading/market_highlight/highlight_result.php",
                          params={"l": "zh-tw", "d": f"{d.year - 1911}/{d:%m/%d}", "o": "json"}, headers=UA, timeout=20).json()
        t = js["tables"][0]; f = t["fields"]; r = t["data"][0]
        up += int(_num(r[f.index("上漲家數")])); dn += int(_num(r[f.index("下跌家數")]))
    except Exception:
        return None
    if up + dn:
        p.write_text(json.dumps([up, dn]))
        return up, dn
    return None


def regime(verbose: bool = False) -> dict:
    """大盤環境：趨勢（加權指數 vs MA50/MA200）、出貨日、漲跌家數 → 多頭／震盪／空頭 + 建議總持股與風險係數。"""
    rc = CFG["regime"]
    p = CACHE / f"regime_{date.today():%Y%m%d}.json"
    now = datetime.now()
    final = now.weekday() >= 5 or now.hour >= 15
    if p.exists() and (final or time.time() - p.stat().st_mtime < 1800):
        return json.loads(p.read_text())
    tw = ohlcv("^TWII", "2y")
    c = tw["Close"]
    ma20, ma50, ma200 = (c.rolling(n).mean() for n in (20, 50, 200))
    last = float(c.iloc[-1])
    slope50 = float(ma50.iloc[-1] / ma50.iloc[-21] - 1) * 100
    slope200 = float(ma200.iloc[-1] / ma200.iloc[-21] - 1) * 100
    trend = [("站上 MA50", last > ma50.iloc[-1]), ("MA50 上升", slope50 > 0),
             ("站上 MA200", last > ma200.iloc[-1]), ("MA50 > MA200", ma50.iloc[-1] > ma200.iloc[-1])]
    score = sum(ok for _, ok in trend)
    # 出貨日：指數跌 ≥ dist_drop_pct% 且成交金額 > 前一日
    fq = fmtqik(3)
    dist = None
    if len(fq) > rc["dist_window"]:
        fq["pct"] = fq["chg"] / (fq["index"] - fq["chg"]) * 100
        fq["dist"] = (fq["pct"] <= -rc["dist_drop_pct"]) & (fq["amount_bn"] > fq["amount_bn"].shift())
        w = fq.tail(rc["dist_window"])
        dist = int(w["dist"].sum())
        score += -2 if dist >= 6 else -1 if dist >= 4 else 0
    # 漲跌家數（最近 N 個交易日，上市＋上櫃股票）
    days = [d.date() for d in tw.index[-rc["breadth_days"]:]]
    bs = [b for b in (breadth(d) for d in days) if b]
    up, dn = sum(b[0] for b in bs), sum(b[1] for b in bs)
    adr = up / (up + dn) if up + dn else None
    if adr is not None:
        score += 1 if adr > 0.55 else -1 if adr < 0.45 else 0
    lvl = next(l for l in rc["levels"] if score >= l["min_score"])
    out = {"date": str(tw.index[-1].date()), "index": round(last, 2),
           "ma20": round(float(ma20.iloc[-1]), 2), "ma50": round(float(ma50.iloc[-1]), 2), "ma200": round(float(ma200.iloc[-1]), 2),
           "slope50_20d_pct": round(slope50, 2), "slope200_20d_pct": round(slope200, 2),
           "trend_checks": {k: bool(v) for k, v in trend},
           "dist_days": dist, "dist_window": rc["dist_window"],
           "breadth_days": len(bs), "adv": up, "dec": dn, "adv_ratio": round(adr, 3) if adr is not None else None,
           "score": int(score), "regime": lvl["name"], "exposure_pct": lvl["exposure_pct"], "risk_mult": lvl["risk_mult"]}
    p.write_text(json.dumps(out, ensure_ascii=False))
    return out


def regime_line(r: dict) -> str:
    ck = "、".join(k if v else f"~~{k}~~" for k, v in r["trend_checks"].items())
    dd = f"出貨日 {r['dist_days']}/{r['dist_window']}" if r["dist_days"] is not None else "出貨日 無資料"
    br = f"近 {r['breadth_days']} 日漲跌家數 {r['adv']}:{r['dec']}（{r['adv_ratio']*100:.0f}% 上漲）" if r["adv_ratio"] is not None else "漲跌家數 無資料"
    return (f"大盤環境【{r['regime']}】分數 {r['score']}｜加權 {r['index']:,.0f}（MA50 {r['ma50']:,.0f}、MA200 {r['ma200']:,.0f}）"
            f"｜{ck}｜{dd}｜{br}｜建議總持股 ≤{r['exposure_pct']}%、每筆風險 ×{r['risk_mult']}")


# ---------------------------------------------------------------- 部位大小
def open_exposure() -> tuple[float, list[dict]]:
    """讀 stock-journal 未平倉部位，回傳（總市值, 明細）。台股 qty 單位為張。"""
    try:
        csvp = Path(os.path.expanduser(_jcfg()["local_dir"])) / "trades.csv"
        df = pd.read_csv(csvp, dtype={"code": str})
    except Exception:
        return 0.0, []
    op = df[df["status"] == "open"]
    rows = []
    for _, r in op.iterrows():
        px = r["last_price"] if pd.notna(r["last_price"]) else r["entry_price"]
        mult = 1000 if str(r["code"]).isdigit() else 1
        rows.append({"id": int(r["id"]), "code": r["code"], "name": r["name"], "type": r["thesis_type"],
                     "value": float(px) * float(r["qty"]) * mult * fx(r["code"])})
    return sum(x["value"] for x in rows), rows


def size(code: str, entry: float, stop: float, capital: float | None = None, risk_pct: float | None = None,
         use_regime: bool = True) -> dict:
    capital = capital or CFG["capital"]
    risk_pct = risk_pct or CFG["risk_pct"]
    if stop >= entry:
        raise ValueError("停損價必須低於進場價（本工具只處理做多）")
    reg = regime() if use_regime else {"regime": "未套用", "risk_mult": 1.0, "exposure_pct": 100}
    df = ohlcv(code, "6mo")
    a14 = float(atr(df).iloc[-1])
    adv = float(df["Volume"].tail(20).mean())
    per = entry - stop
    risk_amt = capital * risk_pct / 100 * reg["risk_mult"]
    lim = {"風險": math.floor(risk_amt / per),
           "單檔上限": math.floor(capital * CFG["max_position_pct"] / 100 / entry),
           "流動性": math.floor(adv * CFG["max_adv_pct"] / 100)}
    k = fx(code)                                     # 美股：價格為美元，資金與上限換算成美元股數
    lim["單檔上限"] = math.floor(capital * CFG["max_position_pct"] / 100 / (entry * k))
    lim["風險"] = math.floor(risk_amt / (per * k))
    bind = min(lim, key=lim.get)
    sh = max(lim[bind], 0)
    tw = str(code).isdigit()
    exp_now, _ = open_exposure()
    val = sh * entry * k
    warns = []
    if per / a14 < CFG["min_stop_atr"]:
        warns.append(f"停損距離 {per:g} 只有 {per / a14:.2f} 倍 ATR14（{a14:.2f}），正常波動就可能掃到；"
                     f"要嘛接受較高的被洗機率，要嘛把停損放到 ≥{CFG['min_stop_atr']} ATR（{entry - CFG['min_stop_atr'] * a14:.2f} 以下）並自動縮小部位")
    if bind != "風險":
        warns.append(f"部位被「{bind}」限制，實際風險只有 {sh * per:,.0f} 元（< 1R {risk_amt:,.0f} 元）")
    cap_exp = capital * reg["exposure_pct"] / 100
    if exp_now + val > cap_exp:
        warns.append(f"加上這筆後總持股 {(exp_now + val) / capital * 100:.0f}% 超過大盤環境建議的 {reg['exposure_pct']}%（目前 {exp_now / capital * 100:.0f}%）")
    return {"code": code, "entry": entry, "stop": stop, "stop_pct": round(per / entry * 100, 2), "atr14": round(a14, 2),
            "stop_atr": round(per / a14, 2), "regime": reg["regime"], "risk_mult": reg["risk_mult"],
            "risk_amt_1R": round(risk_amt), "limits_shares": lim, "binding": bind, "shares": sh,
            "lots": sh // 1000 if tw else None, "odd": sh % 1000 if tw else None,
            "value": round(val), "value_pct": round(val / capital * 100, 1), "actual_risk": round(sh * per * k),
            "exposure_now_pct": round(exp_now / capital * 100, 1), "warnings": warns}


def size_text(s: dict) -> str:
    q = f"{s['lots']} 張 {s['odd']} 股" if s["lots"] is not None else f"{s['shares']} 股"
    lines = [f"建議部位 {s['code']}：{q}（{s['shares']:,} 股，市值 {s['value']:,} 元＝總資金 {s['value_pct']}%）",
             f"- 進場 {s['entry']} / 停損 {s['stop']}（−{s['stop_pct']}%，{s['stop_atr']} ATR）；大盤【{s['regime']}】×{s['risk_mult']} → 1R＝{s['risk_amt_1R']:,} 元",
             f"- 各限制可買股數：{s['limits_shares']}，受「{s['binding']}」限制；實際風險 {s['actual_risk']:,} 元"]
    lines += [f"- ⚠️ {w}" for w in s["warnings"]]
    return "\n".join(lines)


# ---------------------------------------------------------------- 移動停利
def _rule_level(rule: str, df: pd.DataFrame, hi_close: float) -> float | None:
    m = re.fullmatch(r"ma(\d+)", rule)
    if m:
        n = int(m.group(1))
        return float(df["Close"].rolling(n).mean().iloc[-1]) if len(df) >= n else None
    m = re.fullmatch(r"atr(\d+(?:\.\d+)?)", rule)
    if m:
        a = atr(df).iloc[-1]
        return float(hi_close - float(m.group(1)) * a) if pd.notna(a) else None
    raise ValueError(f"未知移動停利規則 {rule}（用 ma10、atr3 這種寫法）")


def trail(entry: float, stop_init: float, entry_date: str, df: pd.DataFrame, rules: list[str] | None = None) -> dict:
    """單筆持倉的移動停利建議。df = 個股日線（需含進場日之後）。回傳的 trail 為 None 表示尚未啟動。"""
    tc = CFG["trail"]
    rules = rules or tc["rules"]
    R = entry - stop_init
    since = df[df.index >= pd.Timestamp(entry_date)]
    close = float(df["Close"].iloc[-1])
    hi = float(since["Close"].max()) if len(since) else close
    out = {"R": round(R, 4), "hi_close": hi, "r_now": round((close - entry) / R, 2) if R > 0 else None,
           "r_max": round((hi - entry) / R, 2) if R > 0 else None, "activated": False, "levels": {}, "trail": None}
    if R <= 0:
        return out
    lv = {r: _rule_level(r, df, hi) for r in rules}
    out["levels"] = {k: round(v, 2) for k, v in lv.items() if v is not None}
    # 啟動門檻：activate_R × max(R, min_stop_atr × ATR)。停損設得極近（R 遠小於 ATR）時，
    # 避免一點正常波動就把防守價拉到成本、被洗出場。
    a14 = atr(df).iloc[-1]
    r_eff = max(R, CFG["min_stop_atr"] * float(a14)) if pd.notna(a14) else R
    out["activate_at"] = round(entry + tc["activate_R"] * r_eff, 2)
    if hi >= out["activate_at"] and out["levels"]:
        vals = list(out["levels"].values())
        t = max(vals) if tc["combine"] == "max" else min(vals)
        if tc["breakeven"]:
            t = max(t, entry)
        out.update(activated=True, trail=round(t, 2))
    return out

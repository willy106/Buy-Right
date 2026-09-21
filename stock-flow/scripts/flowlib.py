"""stock-flow 共用層。資料源全免費：
  * mis.twse.com.tw  即時報價（上市 tse_ / 上櫃 otc_），一次最多約 100 檔，建議間隔 ≥ 5 秒
  * openapi.twse.com.tw / tpex.org.tw OpenAPI  盤後成交與法人
  * FinMind  法人買賣超備援
"""
from __future__ import annotations
import json, os, re, time
from pathlib import Path
import pandas as pd
import requests
import yaml

ROOT = Path(__file__).resolve().parent.parent
ASSETS = ROOT / "assets"
CACHE = Path(os.environ.get("STOCK_CACHE", Path.home() / ".cache" / "stock-toolbox")) / "flow"
CACHE.mkdir(parents=True, exist_ok=True)
UA = {"User-Agent": "Mozilla/5.0", "Accept": "application/json"}


def load_groups(extra: str | None = None, include_industry=False) -> dict[str, list[str]]:
    g = yaml.safe_load((ASSETS / "groups.yaml").read_text(encoding="utf-8")) or {}
    if include_industry and (ASSETS / "groups_industry.yaml").exists():
        g.update(yaml.safe_load((ASSETS / "groups_industry.yaml").read_text(encoding="utf-8")) or {})
    if extra:
        g.update(yaml.safe_load(Path(extra).read_text(encoding="utf-8")) or {})
    return {k: [str(x).zfill(4) for x in v] for k, v in g.items()}


def all_codes(groups: dict) -> list[str]:
    return sorted({c for v in groups.values() for c in v})


# ---------------------------------------------------------------- 上市/上櫃判定（快取 30 天）
def market_map(codes: list[str]) -> dict[str, str]:
    p = CACHE / "market_map.json"
    m = json.loads(p.read_text()) if p.exists() and time.time() - p.stat().st_mtime < 30 * 86400 else {}
    missing = [c for c in codes if c not in m]
    if missing:
        tse, otc = set(), set()
        try:
            tse = {str(r["Code"]) for r in requests.get("https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL", headers=UA, timeout=30).json()}
        except Exception as e:
            print(f"[warn] TWSE 清單: {e}")
        try:
            otc = {str(r["SecuritiesCompanyCode"]) for r in requests.get("https://www.tpex.org.tw/openapi/v1/tpex_mainboard_daily_close_quotes", headers=UA, timeout=30).json()}
        except Exception as e:
            print(f"[warn] TPEx 清單: {e}")
        for c in missing:
            m[c] = "tse" if c in tse else "otc" if c in otc else "unknown"
        if tse or otc:
            p.write_text(json.dumps({k: v for k, v in m.items() if v != "unknown"}))
    return {c: m[c] for c in codes}


# ---------------------------------------------------------------- 即時報價
def realtime_quotes(codes: list[str], mkt: dict[str, str], batch=90, pause=3.0) -> pd.DataFrame:
    """回傳 DataFrame: code, name, last, prev, open, high, low, vol_lots, bid, ask, time, tick_vol"""
    rows = []
    for i in range(0, len(codes), batch):
        chunk = codes[i:i + batch]
        parts = []
        for c in chunk:
            m = mkt.get(c, "unknown")
            parts.append(f"tse_{c}.tw|otc_{c}.tw" if m == "unknown" else f"{m}_{c}.tw")
        ex = "|".join(parts)
        url = f"https://mis.twse.com.tw/stock/api/getStockInfo.jsp?ex_ch={ex}&json=1&delay=0&_={int(time.time()*1000)}"
        try:
            js = requests.get(url, headers=UA, timeout=15).json()
        except Exception as e:
            print(f"[warn] realtime batch {i}: {e}"); continue
        for r in js.get("msgArray", []):
            def f(k):
                v = r.get(k, "-")
                try: return float(v) if v not in ("-", "", None) else None
                except ValueError: return None
            ask = r.get("a", "").split("_")[0]; bid = r.get("b", "").split("_")[0]
            rows.append({"code": r.get("c"), "name": r.get("n"), "last": f("z"), "prev": f("y"), "open": f("o"),
                         "high": f("h"), "low": f("l"), "vol_lots": f("v"), "tick_vol": f("tv"),
                         "ask": float(ask) if ask not in ("", "-") else None, "bid": float(bid) if bid not in ("", "-") else None,
                         "time": r.get("t")})
        if i + batch < len(codes):
            time.sleep(pause)
    df = pd.DataFrame(rows)
    if df.empty:
        return df
    # 盤中未成交時 z 為 '-'，退回用 (bid+ask)/2 或昨收
    df["last"] = df["last"].fillna((df["bid"] + df["ask"]) / 2).fillna(df["prev"])
    df["chg_pct"] = (df["last"] / df["prev"] - 1) * 100
    # 成交金額估計：VWAP 用 (o+h+l+last)/4 近似，誤差通常 <2%
    vwap = df[["open", "high", "low", "last"]].mean(axis=1).fillna(df["last"])
    df["turnover_M"] = vwap * df["vol_lots"].fillna(0) * 1000 / 1e6
    # 內外盤估計：最新成交價貼近 ask=外盤(主動買)，貼近 bid=內盤
    df["side"] = 0
    df.loc[(df["ask"].notna()) & (df["last"] >= df["ask"]), "side"] = 1
    df.loc[(df["bid"].notna()) & (df["last"] <= df["bid"]), "side"] = -1
    return df


# mis 指數回應已無 "v" 欄，成交金額在 "m"，單位十萬元（÷10 → 百萬）。
# 驗證：2026-09-21 12:30 o00 m=1,894,448 → 1,894 億，對照櫃買日成交約 2,000–2,500 億；若當成張數會是全日量的 2 倍，不合理。
MIS_INDEX_M_TO_MILLION = 0.1


def market_total_turnover_M() -> float | None:
    """大盤即時成交值（百萬）。上市 t00 + 上櫃 o00。"""
    url = "https://mis.twse.com.tw/stock/api/getStockInfo.jsp?ex_ch=tse_t00.tw|otc_o00.tw&json=1&delay=0"
    for attempt in range(3):  # mis 偶爾回空或逾時，重試兩次
        try:
            js = requests.get(url, headers=UA, timeout=15).json()
            tot = 0.0
            for r in js.get("msgArray", []):
                m = r.get("m")
                if m and m != "-":
                    tot += float(m) * MIS_INDEX_M_TO_MILLION
                elif r.get("v") not in (None, "", "-"):  # 舊格式：v 為成交金額（億）
                    tot += float(r["v"]) * 100
            if tot:
                return tot
        except Exception:
            pass
        time.sleep(2)
    return None


# ---------------------------------------------------------------- 盤後
def _roc_date(d) -> str | None:
    """民國日期 1150918 → 20260918"""
    d = str(d or "").strip()
    return f"{int(d[:-4]) + 1911}{d[-4:]}" if len(d) >= 6 and d.isdigit() else None


def eod_prices() -> pd.DataFrame:
    """全市場最近一個交易日收盤：code, name, close, chg_pct, turnover_M, date（上市 OpenAPI + 上櫃 OpenAPI）
       date 為資料本身的交易日（YYYYMMDD），盤中呼叫時會是前一交易日。"""
    out = []
    try:
        for r in requests.get("https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL", headers=UA, timeout=30).json():
            try:
                out.append({"code": r["Code"], "name": r["Name"], "close": float(r["ClosingPrice"].replace(",", "")),
                            "chg": float(r["Change"].replace(",", "")), "turnover_M": float(r["TradeValue"].replace(",", "")) / 1e6,
                            "date": _roc_date(r.get("Date"))})
            except (ValueError, KeyError):
                pass
    except Exception as e:
        print(f"[warn] TWSE STOCK_DAY_ALL: {e}")
    try:
        for r in requests.get("https://www.tpex.org.tw/openapi/v1/tpex_mainboard_daily_close_quotes", headers=UA, timeout=30).json():
            try:
                out.append({"code": r["SecuritiesCompanyCode"], "name": r["CompanyName"], "close": float(r["Close"].replace(",", "")),
                            "chg": float(r["Change"].replace(",", "")),
                            "turnover_M": float((r.get("TransactionAmount") or r["TradingAmount"]).replace(",", "")) / 1e6,
                            "date": _roc_date(r.get("Date"))})
            except (ValueError, KeyError):
                pass
    except Exception as e:
        print(f"[warn] TPEx close quotes: {e}")
    df = pd.DataFrame(out)
    if len(df):
        df["chg_pct"] = df["chg"] / (df["close"] - df["chg"]) * 100
    return df


def _num(x) -> float:
    try:
        return float(str(x).replace(",", "").strip() or 0)
    except ValueError:
        return 0.0


def _norm_keys(r: dict) -> dict:
    """TPEx OpenAPI 欄位名稱夾雜空白、大小寫不一，比對前先正規化。"""
    return {re.sub(r"[^a-z]", "", k.lower()): v for k, v in r.items()}


def eod_institutional(date_str: str | None = None) -> pd.DataFrame:
    """三大法人買賣超（張 → 用收盤價換算金額在呼叫端做）。
       上市：twse.com.tw rwd T86（舊 OpenAPI /v1/fund/T86 已下線）；上櫃：TPEx OpenAPI。
       任一市場取不到或全為 0 → 用 FinMind 補該市場缺的部分。
       date_str：YYYYMMDD 或 YYYY-MM-DD，應傳 eod_prices() 的資料日，避免價格與法人錯日。
       回傳 code, foreign_lots, trust_lots, dealer_lots"""
    d8 = (date_str or f"{pd.Timestamp.today():%Y%m%d}").replace("-", "")
    twse, tpex = [], []
    try:
        js = requests.get("https://www.twse.com.tw/rwd/zh/fund/T86", params={"date": d8, "selectType": "ALLBUT0999", "response": "json"},
                          headers=UA, timeout=30).json()
        if js.get("stat") == "OK" and js.get("date") == d8:
            f = {k: i for i, k in enumerate(js["fields"])}
            col = lambda row, name: _num(row[f[name]]) / 1000
            for row in js.get("data", []):
                twse.append({"code": row[f["證券代號"]].strip(),
                             "foreign_lots": col(row, "外陸資買賣超股數(不含外資自營商)") + col(row, "外資自營商買賣超股數"),
                             "trust_lots": col(row, "投信買賣超股數"), "dealer_lots": col(row, "自營商買賣超股數")})
        else:
            print(f"[warn] TWSE T86 {d8}: {js.get('stat')}（可能尚未公布，約 15:00 後）")
    except Exception as e:
        print(f"[warn] TWSE T86: {e}")
    try:
        for r in requests.get("https://www.tpex.org.tw/openapi/v1/tpex_3insti_daily_trading", headers=UA, timeout=30).json():
            if _roc_date(r.get("Date")) != d8:  # OpenAPI 只給最新一日，日期不符就不用
                continue
            n = _norm_keys(r)
            tpex.append({"code": str(r["SecuritiesCompanyCode"]).strip(),
                         "foreign_lots": _num(n.get("foreigninvestorsincludemainlandareainvestorsdifference")) / 1000,
                         "trust_lots": _num(n.get("securitiesinvestmenttrustcompaniesdifference")) / 1000,
                         "dealer_lots": _num(n.get("dealersdifference")) / 1000})
        if not tpex:
            print(f"[warn] TPEx 3insti：無 {d8} 資料")
    except Exception as e:
        print(f"[warn] TPEx 3insti: {e}")
    alive = lambda rows: rows and any(r["foreign_lots"] or r["trust_lots"] or r["dealer_lots"] for r in rows)
    if not alive(twse) or not alive(tpex):  # FinMind 備援（整市場單日），只補缺的市場
        d = f"{d8[:4]}-{d8[4:6]}-{d8[6:]}"
        try:
            js = requests.get("https://api.finmindtrade.com/api/v4/data", params={"dataset": "TaiwanStockInstitutionalInvestorsBuySell", "start_date": d, "end_date": d, "token": os.environ.get("FINMIND_TOKEN", "")}, timeout=60).json()
            fm = pd.DataFrame(js.get("data", []))
            if len(fm):
                fm["net"] = (fm["buy"] - fm["sell"]) / 1000
                p = fm.pivot_table(index="stock_id", columns="name", values="net", aggfunc="sum").fillna(0)
                fmd = pd.DataFrame({"code": p.index, "foreign_lots": p.get("Foreign_Investor", 0) + p.get("Foreign_Dealer_Self", 0),
                                    "trust_lots": p.get("Investment_Trust", 0), "dealer_lots": p.get("Dealer_self", 0) + p.get("Dealer_Hedging", 0)}).reset_index(drop=True)
                keep = [r for r in (twse if alive(twse) else []) + (tpex if alive(tpex) else [])]
                have = {r["code"] for r in keep}
                print(f"[info] FinMind 補 {'上市' if not alive(twse) else ''}{'上櫃' if not alive(tpex) else ''} 法人資料")
                return pd.concat([pd.DataFrame(keep), fmd[~fmd.code.isin(have)]], ignore_index=True)
            print(f"[warn] FinMind 無 {d} 法人資料")
        except Exception as e:
            print(f"[warn] FinMind fallback: {e}")
    return pd.DataFrame([r for r in twse + tpex], columns=["code", "foreign_lots", "trust_lots", "dealer_lots"])


def data_date(px: pd.DataFrame) -> str:
    """eod_prices() 的資料交易日；取不到才退回今天。"""
    d = px["date"].dropna() if "date" in px else pd.Series(dtype=str)
    return d.mode().iloc[0] if len(d) else f"{pd.Timestamp.today():%Y%m%d}"


def snapshot_path(tag: str, date: str | None = None) -> Path:
    return CACHE / f"{tag}_{date or f'{pd.Timestamp.today():%Y%m%d}'}.csv"

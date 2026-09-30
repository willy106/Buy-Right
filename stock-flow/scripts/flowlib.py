"""stock-flow 共用層。資料源全免費：
  * mis.twse.com.tw  即時報價（上市 tse_ / 上櫃 otc_），一次最多約 100 檔，建議間隔 ≥ 5 秒
  * openapi.twse.com.tw / tpex.org.tw OpenAPI  盤後成交與法人
  * FinMind  法人買賣超備援
"""
from __future__ import annotations
import json, os, re, time
from pathlib import Path
import numpy as np
import pandas as pd
import requests
import yaml

ROOT = Path(__file__).resolve().parent.parent
ASSETS = ROOT / "assets"
CACHE = Path(os.environ.get("STOCK_CACHE", Path.home() / ".cache" / "stock-toolbox")) / "flow"
CACHE.mkdir(parents=True, exist_ok=True)
UA = {"User-Agent": "Mozilla/5.0", "Accept": "application/json"}


def get_json(url: str, tries: int = 3, **kw):
    """requests.get(...).json()，連線中斷（TPEx OpenAPI 常見 IncompleteRead）時重試。"""
    kw.setdefault("headers", UA); kw.setdefault("timeout", 30)
    for i in range(tries):
        try:
            return requests.get(url, **kw).json()
        except (requests.RequestException, ValueError):
            if i == tries - 1:
                raise
            time.sleep(2 * (i + 1))


def load_groups(extra: str | None = None, include_industry=False) -> dict[str, list[str]]:
    g = yaml.safe_load((ASSETS / "groups.yaml").read_text(encoding="utf-8")) or {}
    if include_industry and (ASSETS / "groups_industry.yaml").exists():
        g.update(yaml.safe_load((ASSETS / "groups_industry.yaml").read_text(encoding="utf-8")) or {})
    if extra:
        g.update(yaml.safe_load(Path(extra).read_text(encoding="utf-8")) or {})
    return {k: [str(x).zfill(4) for x in v] for k, v in g.items()}


def all_codes(groups: dict) -> list[str]:
    return sorted({c for v in groups.values() for c in v})


def theme_map() -> tuple[dict[str, list[str]], dict[str, int], int | None]:
    """MoneyDJ 細題材（themes_sync.py 產生）。回傳 (代號 → 題材清單，小題材在前), 題材 → 檔數, 資料天數；沒檔案回傳空。"""
    p = CACHE / "themes_moneydj.json"
    if not p.exists():
        return {}, {}, None
    js = json.loads(p.read_text())
    size = {k: len(v["codes"]) for k, v in js["themes"].items()}
    m: dict[str, list[str]] = {}
    for k in sorted(size, key=size.get):
        for c in js["themes"][k]["codes"]:
            m.setdefault(c, []).append(k)
    age = (pd.Timestamp.now() - pd.Timestamp(js["updated"])).days
    return m, size, age


def market_universe(min_turnover_m: float = 50.0) -> list[str]:
    """全市場普通股（4 碼、非 0 開頭，排除 ETF／權證）中，最近一份盤後快取成交值 ≥ min_turnover_m 百萬的代號。
       用來把掃描範圍擴大到族群清單以外；沒有盤後快取時回傳空（先跑一次 flow_eod.py）。"""
    files = sorted(CACHE.glob("eod_2*.csv"))
    if not files:
        return []
    d = pd.read_csv(files[-1], dtype={"code": str})
    return sorted(d[d.code.str.fullmatch(r"[1-9]\d{3}") & (d.turnover_M >= min_turnover_m)].code)


# ---------------------------------------------------------------- 上市/上櫃判定（快取 30 天）
def market_map(codes: list[str]) -> dict[str, str]:
    p = CACHE / "market_map.json"
    m = json.loads(p.read_text()) if p.exists() and time.time() - p.stat().st_mtime < 30 * 86400 else {}
    missing = [c for c in codes if c not in m]
    if missing:   # 先用最近一份盤後快取的 mkt 欄（twse/tpex）補，OpenAPI 常斷線
        files = sorted(CACHE.glob("eod_2*.csv"))
        if files:
            e = pd.read_csv(files[-1], dtype={"code": str}, usecols=["code", "mkt"]).set_index("code")["mkt"].to_dict()
            m.update({c: {"twse": "tse", "tpex": "otc"}[e[c]] for c in missing if e.get(c) in ("twse", "tpex")})
            missing = [c for c in codes if c not in m]
    if missing:
        tse, otc = set(), set()
        try:
            tse = {str(r["Code"]) for r in get_json("https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL")}
        except Exception as e:
            print(f"[warn] TWSE 清單: {e}")
        try:
            otc = {str(r["SecuritiesCompanyCode"]) for r in get_json("https://www.tpex.org.tw/openapi/v1/tpex_mainboard_daily_close_quotes")}
        except Exception as e:
            print(f"[warn] TPEx 清單: {e}")
        for c in missing:
            m[c] = "tse" if c in tse else "otc" if c in otc else "unknown"
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
            if not r.get("c"):   # 上市/上櫃都查時，查不到的那邊會回空殼
                continue
            def f(k):
                v = r.get(k, "-")
                try: return float(v) if v not in ("-", "", None) else None
                except ValueError: return None
            def best(k):
                # 五檔以 "_" 分隔；第一檔可能是 0.0000（市價單），取第一個正價
                for p in r.get(k, "").split("_"):
                    try:
                        if float(p) > 0: return float(p)
                    except ValueError:
                        pass
                return None
            rows.append({"code": r.get("c"), "name": r.get("n"), "last": f("z"), "prev": f("y"), "open": f("o"),
                         "high": f("h"), "low": f("l"), "vol_lots": f("v"), "tick_vol": f("tv"),
                         "ask": best("a"), "bid": best("b"), "limit_up": f("u"), "limit_dn": f("w"),
                         "time": r.get("t")})
        if i + batch < len(codes):
            time.sleep(pause)
    df = pd.DataFrame(rows)
    if df.empty:
        return df
    # 盤中未成交時 z 為 '-'。漲停鎖死（無賣單、買一＝漲停價）／跌停鎖死（無買單、賣一＝跌停價）
    # 時 (bid+ask)/2 算不出來，舊寫法會退回昨收、把漲跌停股誤判成 0%，這裡先補上漲跌停價。
    lu = df["last"].isna() & df["ask"].isna() & df["bid"].notna() & (df["bid"] >= df["limit_up"])
    ld = df["last"].isna() & df["bid"].isna() & df["ask"].notna() & (df["ask"] <= df["limit_dn"])
    df.loc[lu, "last"] = df.loc[lu, "limit_up"]
    df.loc[ld, "last"] = df.loc[ld, "limit_dn"]
    # 其餘：(bid+ask)/2 → 單邊報價 → 昨收
    df["last"] = df["last"].fillna((df["bid"] + df["ask"]) / 2).fillna(df["bid"]).fillna(df["ask"]).fillna(df["prev"])
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


def _twse_mi_index(d8: str) -> list[dict]:
    """上市每日收盤行情（MI_INDEX，收盤後約 14:30 即有；STOCK_DAY_ALL OpenAPI 常到晚上才更新）。"""
    js = get_json("https://www.twse.com.tw/rwd/zh/afterTrading/MI_INDEX",
                  params={"date": d8, "type": "ALLBUT0999", "response": "json"})
    if js.get("stat") != "OK" or js.get("date") != d8:
        return []
    tbl = next((t for t in js.get("tables", []) if "每日收盤行情" in (t.get("title") or "")), None)
    if not tbl:
        return []
    f = {k: i for i, k in enumerate(tbl["fields"])}
    out = []
    for row in tbl["data"]:
        try:
            sign = -1 if "-" in re.sub(r"<[^>]+>", "", row[f["漲跌(+/-)"]]) else 1
            out.append({"code": row[f["證券代號"]].strip(), "name": row[f["證券名稱"]].strip(),
                        "close": float(row[f["收盤價"]].replace(",", "")),
                        "chg": sign * _num(row[f["漲跌價差"]]), "turnover_M": _num(row[f["成交金額"]]) / 1e6,
                        "date": d8, "mkt": "twse"})
        except (ValueError, KeyError):
            pass  # 無成交（收盤價 "--"）
    return out


def _tpex_date(d8: str) -> str:
    return f"{d8[:4]}/{d8[4:6]}/{d8[6:]}"


def _tpex_quotes(d8: str) -> list[dict]:
    """上櫃指定日收盤行情（www.tpex.org.tw afterTrading/dailyQuotes，可查歷史；OpenAPI 只給最新一日）。
       成交金額與 OpenAPI 一致（含盤後定價等；afterTrading/otc 只有一般交易時段，會少約 5%）。"""
    js = get_json("https://www.tpex.org.tw/www/zh-tw/afterTrading/dailyQuotes",
                  params={"date": _tpex_date(d8), "id": "", "response": "json"})
    if js.get("stat") != "ok" or js.get("date") != d8 or not js.get("tables"):
        return []
    tbl = js["tables"][0]
    f = {k.strip(): i for i, k in enumerate(tbl["fields"])}
    out = []
    for row in tbl.get("data", []):
        try:
            out.append({"code": row[f["代號"]].strip(), "name": row[f["名稱"]].strip(),
                        "close": float(row[f["收盤"]].replace(",", "")), "chg": _num(row[f["漲跌"]]),
                        "turnover_M": _num(row[f["成交金額(元)"]]) / 1e6, "date": d8, "mkt": "tpex"})
        except (ValueError, KeyError):
            pass  # 無成交（收盤價 "----"）
    return out


def _tpex_insti(d8: str) -> list[dict]:
    """上櫃指定日三大法人（insti/dailyTrade，可查歷史）。欄位每 3 欄一組：外資(不含自營)、外資自營、外資合計、
       投信、自營自行、自營避險、自營合計，最後一欄三大合計；取各組的「買賣超股數」。"""
    js = get_json("https://www.tpex.org.tw/www/zh-tw/insti/dailyTrade",
                  params={"type": "Daily", "sect": "EW", "date": _tpex_date(d8), "response": "json"})
    if js.get("stat") != "ok" or js.get("date") != d8 or not js.get("tables"):
        return []
    out = []
    for row in js["tables"][0].get("data", []):
        if len(row) < 24:
            continue
        out.append({"code": row[0].strip(), "foreign_lots": _num(row[10]) / 1000,
                    "trust_lots": _num(row[13]) / 1000, "dealer_lots": _num(row[22]) / 1000})
    return out


def eod_prices_on(d8: str) -> pd.DataFrame:
    """指定交易日的全市場收盤（上市 MI_INDEX + 上櫃 afterTrading/dailyQuotes），欄位同 eod_prices()。
       休市日兩邊都回空；只有一邊有資料時 attrs["market_dates"] 會標出缺哪邊，呼叫端用 eod_problems() 檢查。"""
    twse, tpex = [], []
    try:
        twse = _twse_mi_index(d8)
    except Exception as e:
        print(f"[warn] TWSE MI_INDEX {d8}: {e}")
    try:
        tpex = _tpex_quotes(d8)
    except Exception as e:
        print(f"[warn] TPEx dailyQuotes {d8}: {e}")
    df = pd.DataFrame(twse + tpex)
    if len(df):
        df["chg_pct"] = df["chg"] / (df["close"] - df["chg"]) * 100
    df.attrs["market_dates"] = {"twse": d8 if twse else None, "tpex": d8 if tpex else None}
    df.attrs["target"] = d8
    return df


def eod_prices(expect: str | None = None) -> pd.DataFrame:
    """全市場最近一個交易日收盤：code, name, close, chg_pct, turnover_M, date, mkt（上市 + 上櫃）
       date 為資料本身的交易日（YYYYMMDD），盤中呼叫時會是前一交易日。
       兩市場日期必須一致：目標日 = expect 或兩市場較新的那天；上市 OpenAPI 落後時改抓 MI_INDEX 補。
       各市場實際日期放在 df.attrs["market_dates"]，呼叫端用 eod_problems() 檢查。"""
    twse, tpex = [], []
    try:
        for r in get_json("https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL"):
            try:
                twse.append({"code": r["Code"], "name": r["Name"], "close": float(r["ClosingPrice"].replace(",", "")),
                             "chg": float(r["Change"].replace(",", "")), "turnover_M": float(r["TradeValue"].replace(",", "")) / 1e6,
                             "date": _roc_date(r.get("Date")), "mkt": "twse"})
            except (ValueError, KeyError):
                pass
    except Exception as e:
        print(f"[warn] TWSE STOCK_DAY_ALL: {e}")
    try:
        for r in get_json("https://www.tpex.org.tw/openapi/v1/tpex_mainboard_daily_close_quotes"):
            try:
                tpex.append({"code": r["SecuritiesCompanyCode"], "name": r["CompanyName"], "close": float(r["Close"].replace(",", "")),
                             "chg": float(r["Change"].replace(",", "")),
                             "turnover_M": float((r.get("TransactionAmount") or r["TradingAmount"]).replace(",", "")) / 1e6,
                             "date": _roc_date(r.get("Date")), "mkt": "tpex"})
            except (ValueError, KeyError):
                pass
    except Exception as e:
        print(f"[warn] TPEx close quotes: {e}")

    def mdate(rows):
        s = pd.Series([r["date"] for r in rows]).dropna()
        return s.mode().iloc[0] if len(s) else None
    target = expect or max(filter(None, [mdate(twse), mdate(tpex)]), default=None)
    now = pd.Timestamp.now(tz="Asia/Taipei")
    today = now.strftime("%Y%m%d")
    if not expect and target != today and now.weekday() < 5 and now.hour * 60 + now.minute >= 14 * 60 + 30:
        target = today   # 兩個 OpenAPI 都落後（或上櫃斷線）時，仍試抓今天的 MI_INDEX；休市日 MI_INDEX 回空，會退回原目標日
    if target and mdate(twse) != target:
        try:
            alt = _twse_mi_index(target)
        except Exception as e:
            alt = []; print(f"[warn] TWSE MI_INDEX: {e}")
        if alt:
            print(f"[info] 上市 OpenAPI 資料日 {mdate(twse)} ≠ {target}，改用 MI_INDEX {target}")
            twse = alt
        elif target == today and not expect:
            target = max(filter(None, [mdate(twse), mdate(tpex)]), default=None)
    df = pd.DataFrame(twse + tpex)
    if len(df):
        df["chg_pct"] = df["chg"] / (df["close"] - df["chg"]) * 100
    df.attrs["market_dates"] = {"twse": mdate(twse), "tpex": mdate(tpex)}
    df.attrs["target"] = target
    return df


def eod_problems(px: pd.DataFrame) -> list[str]:
    """收盤資料是否可用：兩市場都要有、且日期一致。回傳問題清單（空 = 可用）。"""
    md, target = px.attrs.get("market_dates", {}), px.attrs.get("target")
    name = {"twse": "上市", "tpex": "上櫃"}
    probs = [f"{name[m]}收盤資料缺" for m in ("twse", "tpex") if not md.get(m)]
    probs += [f"{name[m]}資料日 {d} ≠ 目標日 {target}（來源尚未更新）" for m, d in md.items() if d and target and d != target]
    return probs


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
       回傳 code, foreign_lots, trust_lots, dealer_lots；attrs["inst_markets"] = 各市場是否有資料"""
    d8 = (date_str or f"{pd.Timestamp.today():%Y%m%d}").replace("-", "")
    twse, tpex = [], []
    try:
        js = get_json("https://www.twse.com.tw/rwd/zh/fund/T86", params={"date": d8, "selectType": "ALLBUT0999", "response": "json"})
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
        for r in get_json("https://www.tpex.org.tw/openapi/v1/tpex_3insti_daily_trading"):
            if _roc_date(r.get("Date")) != d8:  # OpenAPI 只給最新一日，日期不符就不用
                continue
            n = _norm_keys(r)
            tpex.append({"code": str(r["SecuritiesCompanyCode"]).strip(),
                         "foreign_lots": _num(n.get("foreigninvestorsincludemainlandareainvestorsdifference")) / 1000,
                         "trust_lots": _num(n.get("securitiesinvestmenttrustcompaniesdifference")) / 1000,
                         "dealer_lots": _num(n.get("dealersdifference")) / 1000})
    except Exception as e:
        print(f"[warn] TPEx 3insti: {e}")
    if not tpex:  # OpenAPI 只給最新一日；查歷史或 OpenAPI 還沒更新時改用可指定日期的 dailyTrade
        try:
            tpex = _tpex_insti(d8)
        except Exception as e:
            print(f"[warn] TPEx dailyTrade {d8}: {e}")
        if not tpex:
            print(f"[warn] TPEx 3insti：無 {d8} 資料")
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
                out = pd.concat([pd.DataFrame(keep), fmd[~fmd.code.isin(have)]], ignore_index=True)
                out.attrs["inst_markets"] = {"twse": True, "tpex": True}  # FinMind 為全市場
                return out
            print(f"[warn] FinMind 無 {d} 法人資料")
        except Exception as e:
            print(f"[warn] FinMind fallback: {e}")
    out = pd.DataFrame([r for r in twse + tpex], columns=["code", "foreign_lots", "trust_lots", "dealer_lots"])
    out.attrs["inst_markets"] = {"twse": bool(alive(twse)), "tpex": bool(alive(tpex))}  # 呼叫端據此判斷是否完整
    return out


def data_date(px: pd.DataFrame) -> str:
    """eod_prices() 的資料交易日；取不到才退回今天。"""
    d = px["date"].dropna() if "date" in px else pd.Series(dtype=str)
    return d.mode().iloc[0] if len(d) else f"{pd.Timestamp.today():%Y%m%d}"


def snapshot_path(tag: str, date: str | None = None) -> Path:
    return CACHE / f"{tag}_{date or f'{pd.Timestamp.today():%Y%m%d}'}.csv"


# ---------------------------------------------------------------- 多週期族群流向
def inst_path(date: str) -> Path:
    """個股三大法人快取（張數），多週期流向用現行族群清單重算，所以存個股而不是族群加總。"""
    return CACHE / f"eod_inst_{date}.csv"


def cached_eod_dates(upto: str | None = None) -> list[str]:
    """有收盤快取（eod_YYYYMMDD.csv）的交易日，由舊到新；upto 之後的不算。"""
    ds = sorted(f.stem[4:] for f in CACHE.glob("eod_[0-9]*.csv") if len(f.stem) == 12)
    return [d for d in ds if not upto or d <= upto]


def _flow_label(share: float, inst: float) -> str:
    if pd.isna(share) or pd.isna(inst):
        return "-"
    if share > 0 and inst > 0:
        return "流入"
    if share < 0 and inst < 0:
        return "流出"
    return "佔比↑法人賣" if share > 0 else "佔比↓法人買"


def group_windows(groups: dict[str, list[str]], windows=(3, 5, 10, 20), base: int = 20,
                  upto: str | None = None, min_base: int = 5) -> pd.DataFrame:
    """族群 N 日資金流向（只讀盤後快取；缺的日子先跑 flow_backfill.py 補）。
       share_Nd = 近 N 日平均成交佔比 − 再往前 base 日的平均佔比（百分點）；前段不足 min_base 日 → NaN
       inst_Nd  = 近 N 日三大法人淨額合計（百萬）；N 日內有任一天缺法人資料 → NaN
       flow_Nd  = 流入（佔比↑且法人買）／流出（佔比↓且法人賣）／佔比↑法人賣／佔比↓法人買
       attrs：upto（最後一天）、days（可用天數）"""
    dates = cached_eod_dates(upto)
    need = max(windows) + base
    dates = dates[-need:]
    share = {g: {} for g in groups}
    inst = {g: {} for g in groups}
    for d in dates:
        px = pd.read_csv(CACHE / f"eod_{d}.csv", dtype={"code": str})
        tot = px["turnover_M"].sum()
        ip = inst_path(d)
        il = pd.read_csv(ip, dtype={"code": str}) if ip.exists() else None
        if il is not None:
            il = il.merge(px[["code", "close"]], on="code", how="left")
            il["inst_M"] = (il[["foreign_lots", "trust_lots", "dealer_lots"]].sum(axis=1) * il["close"] * 1000 / 1e6)
        gp = CACHE / f"eod_groups_{d}.csv"
        gfile = pd.read_csv(gp).set_index("group")["inst_M"] if il is None and gp.exists() else None
        for g, codes in groups.items():
            if tot:
                share[g][d] = px.loc[px.code.isin(codes), "turnover_M"].sum() / tot * 100
            if il is not None:
                inst[g][d] = il.loc[il.code.isin(codes), "inst_M"].sum()
            elif gfile is not None and g in gfile.index:
                inst[g][d] = float(gfile[g])
    rows = []
    for g in groups:
        s = pd.Series(share[g], dtype=float).reindex(dates)
        i = pd.Series(inst[g], dtype=float).reindex(dates)
        r = {"group": g}
        for n in windows:
            if len(dates) < n:
                sc = ic = np.nan
            else:
                prior = s.iloc[max(0, len(dates) - n - base):len(dates) - n].dropna()
                sc = s.iloc[-n:].mean() - prior.mean() if len(prior) >= min_base else np.nan
                ic = i.iloc[-n:].sum(min_count=n) if i.iloc[-n:].notna().all() else np.nan
            r[f"share_{n}d"] = round(sc, 2) if pd.notna(sc) else np.nan
            r[f"inst_{n}d"] = round(ic) if pd.notna(ic) else np.nan
            r[f"flow_{n}d"] = _flow_label(sc, ic)
        rows.append(r)
    out = pd.DataFrame(rows)
    out.attrs["upto"] = dates[-1] if dates else None
    out.attrs["days"] = len(dates)
    return out


def windows_cell(r: dict | pd.Series, n: int) -> str:
    """報告用的一格：佔比變化｜法人淨額 標籤，例如「+0.42｜+1,230M 流入」。"""
    s, i, lab = r.get(f"share_{n}d"), r.get(f"inst_{n}d"), r.get(f"flow_{n}d", "-")
    if pd.isna(s) and pd.isna(i):
        return "資料不足"
    ss = f"{s:+.2f}" if pd.notna(s) else "?"
    ii = f"{i:+,.0f}M" if pd.notna(i) else "?"
    return f"{ss}｜{ii} {lab}"

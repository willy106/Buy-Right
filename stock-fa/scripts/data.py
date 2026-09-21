"""Shared data layer — free sources only.

Sources (all $0):
  * yfinance          : OHLCV + fundamentals for TW (.TW/.TWO) and US tickers
  * FinMind free tier : TW 法人/融資/月營收/財報   (600 req/hr; set FINMIND_TOKEN to raise limit)
  * TWSE/TPEx OpenAPI : fallback for daily TW quotes

Every fetch is cached on disk (~/.cache/stock-toolbox) so repeated runs
cost zero calls. TTL is per dataset.
"""
from __future__ import annotations
import logging
import hashlib, json, os, re, time
from pathlib import Path
import pandas as pd
import requests

CACHE_DIR = Path(os.environ.get("STOCK_CACHE", Path.home() / ".cache" / "stock-toolbox"))
CACHE_DIR.mkdir(parents=True, exist_ok=True)
FINMIND_URL = "https://api.finmindtrade.com/api/v4/data"


# ---------------------------------------------------------------- cache
def _cache_path(key: str) -> Path:
    return CACHE_DIR / (hashlib.md5(key.encode()).hexdigest() + ".pkl")


def cached(key: str, ttl_sec: int, fn):
    p = _cache_path(key)
    if p.exists() and time.time() - p.stat().st_mtime < ttl_sec:
        return pd.read_pickle(p)
    df = fn()
    if df is not None and len(df):
        pd.to_pickle(df, p)
    return df


# ---------------------------------------------------------------- ticker
def normalize(ticker: str) -> tuple[str, str]:
    """Return (yfinance_symbol, market). '2330' -> ('2330.TW','TW'); 'AAPL' -> ('AAPL','US')."""
    t = ticker.strip().upper()
    if re.fullmatch(r"\d{4,6}[A-Z]?", t):
        return _tw_suffix(t), "TW"
    if t.endswith(".TW") or t.endswith(".TWO"):
        return t, "TW"
    return t, "US"


def _tw_suffix(code: str) -> str:
    """上市 .TW／上櫃 .TWO：用 yfinance 近 5 日行情試，結果快取 30 天。都抓不到就回 .TW。"""
    import yfinance as yf
    logging.getLogger("yfinance").setLevel(logging.CRITICAL)
    def _probe():
        for sym in (f"{code}.TW", f"{code}.TWO"):
            try:
                if len(yf.Ticker(sym).history(period="5d")):
                    return pd.Series([sym])
            except Exception:
                pass
        return None
    s = cached(f"suffix:{code}", 60 * 60 * 24 * 30, _probe)
    return s.iloc[0] if s is not None and len(s) else f"{code}.TW"


def tw_code(symbol: str) -> str:
    return symbol.split(".")[0]


# ---------------------------------------------------------------- OHLCV
def drop_partial_bar(df: pd.DataFrame) -> pd.DataFrame:
    """盤中時 yfinance 會回傳今天「未完成」的 K 棒；技術指標要用完整棒，把它去掉。"""
    if len(df) and df.index[-1].date() == pd.Timestamp.today().date():
        return df.iloc[:-1]
    return df


def fetch_ohlcv(ticker: str, period: str = "1y", interval: str = "1d", drop_today: bool = False) -> tuple[pd.DataFrame, str]:
    """OHLCV DataFrame (Open High Low Close Volume, DatetimeIndex). Tries .TW then .TWO.
       drop_today=True 時剔除今天未收盤的 K 棒（盤中呼叫必開）。"""
    import yfinance as yf
    sym, mkt = normalize(ticker)
    candidates = [sym] + ([sym.replace(".TW", ".TWO")] if sym.endswith(".TW") else [])
    for s in candidates:
        def _get(s=s):
            df = yf.Ticker(s).history(period=period, interval=interval, auto_adjust=False)
            if df is None or df.empty:
                return None
            df = df[["Open", "High", "Low", "Close", "Volume"]].copy()
            df.index = pd.to_datetime(df.index).tz_localize(None)
            return df
        df = cached(f"ohlcv:{s}:{period}:{interval}", 60 * 15 if drop_today else 60 * 60 * 6, _get)
        if df is not None and len(df):
            return (drop_partial_bar(df) if drop_today else df), s
    raise SystemExit(f"找不到 {ticker} 的行情資料（試過 {candidates}）")


# ---------------------------------------------------------------- FinMind
def finmind(dataset: str, data_id: str, start: str, ttl_sec: int = 60 * 60 * 12) -> pd.DataFrame:
    """Free FinMind v4. Datasets used in this toolbox:
       TaiwanStockInstitutionalInvestorsBuySell, TaiwanStockMarginPurchaseShortSale,
       TaiwanStockMonthRevenue, TaiwanStockFinancialStatements, TaiwanStockBalanceSheet,
       TaiwanStockCashFlowsStatement, TaiwanStockDividend, TaiwanStockPER
    """
    def _get():
        params = {"dataset": dataset, "data_id": data_id, "start_date": start}
        tok = os.environ.get("FINMIND_TOKEN")
        if tok:
            params["token"] = tok
        r = requests.get(FINMIND_URL, params=params, timeout=30)
        r.raise_for_status()
        js = r.json()
        if js.get("status") != 200:
            raise RuntimeError(f"FinMind {dataset}: {js.get('msg')}")
        df = pd.DataFrame(js.get("data", []))
        if "date" in df.columns:
            df["date"] = pd.to_datetime(df["date"])
        return df
    try:
        return cached(f"finmind:{dataset}:{data_id}:{start}", ttl_sec, _get)
    except Exception as e:  # never let a sidecar dataset kill the run
        print(f"[warn] FinMind {dataset} 取得失敗: {e}")
        return pd.DataFrame()


def yf_info(symbol: str) -> dict:
    import yfinance as yf
    def _get():
        return pd.Series(yf.Ticker(symbol).info or {})
    s = cached(f"info:{symbol}", 60 * 60 * 24, _get)
    return {} if s is None else s.to_dict()


def dump(obj, path: str | None):
    """Write JSON to path (if given) and always print a compact copy for the model."""
    txt = json.dumps(obj, ensure_ascii=False, indent=2, default=str)
    if path:
        Path(path).write_text(txt, encoding="utf-8")
    print(txt)

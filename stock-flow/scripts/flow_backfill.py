#!/usr/bin/env python3
"""補齊盤後快取，供多週期族群流向（3／5／10／20 日，flowlib.group_windows）使用。

用法:
  python flow_backfill.py                 # 補到最近 40 個交易日（20 日窗口 + 20 日比較基準）
  python flow_backfill.py --days 60
  python flow_backfill.py --windows       # 補完印出多週期族群流向

每個交易日寫兩個檔（已存在就跳過）：
  eod_YYYYMMDD.csv       全市場收盤與成交值（上市 MI_INDEX + 上櫃 afterTrading/dailyQuotes）
  eod_inst_YYYYMMDD.csv  個股三大法人張數（上市 T86 + 上櫃 insti/dailyTrade）
兩市場任一缺資料就不寫，避免污染基準。每個請求間隔 --pause 秒（證交所太快會暫時封 IP）。
約 4 個請求／交易日，40 日約 8–10 分鐘，可背景跑。
"""
import argparse, time
import pandas as pd
from flowlib import (CACHE, eod_prices_on, eod_problems, eod_institutional, inst_path, snapshot_path,
                     load_groups, group_windows, windows_cell)


def backfill(days: int, pause: float, max_lookback: int) -> None:
    today = pd.Timestamp.now(tz="Asia/Taipei")
    # 今天 16:30 前法人可能還沒出來，從昨天往回找；今天的交給 flow_eod.py
    start = today.normalize() - pd.Timedelta(days=1 if today.hour * 60 + today.minute < 16 * 60 + 30 else 0)
    got, d, tried = 0, start, 0
    while got < days and tried < max_lookback:
        tried += 1
        d8 = f"{d:%Y%m%d}"
        d -= pd.Timedelta(days=1)
        if pd.Timestamp(d8).weekday() >= 5:
            continue
        px_path, ip = snapshot_path("eod", d8), inst_path(d8)
        if px_path.exists() and ip.exists():
            got += 1
            continue
        if not px_path.exists():
            px = eod_prices_on(d8); time.sleep(pause)
            if px.empty:
                print(f"{d8} 休市或無資料，略過"); continue
            probs = eod_problems(px)
            if probs:
                print(f"{d8} 收盤資料不完整（{'；'.join(probs)}），不寫快取"); continue
            px.to_csv(px_path, index=False)
        if not ip.exists():
            inst = eod_institutional(d8); time.sleep(pause)
            im = inst.attrs.get("inst_markets", {})
            if inst.empty or not (im.get("twse") and im.get("tpex")):
                print(f"{d8} 法人資料不完整（{im}），不寫法人快取"); continue
            inst.to_csv(ip, index=False)
        got += 1
        print(f"{d8} ok（{got}/{days}）")
    print(f"[backfill] 完成 {got} 個交易日，快取在 {CACHE}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=40, help="補到最近幾個交易日")
    ap.add_argument("--pause", type=float, default=3.0, help="請求間隔秒數")
    ap.add_argument("--max-lookback", type=int, default=120, help="最多往回找幾個日曆日")
    ap.add_argument("--windows", action="store_true", help="補完印出多週期族群流向")
    a = ap.parse_args()
    backfill(a.days, a.pause, a.max_lookback)
    if a.windows:
        w = group_windows(load_groups())
        print(f"\n【族群多週期流向】至 {w.attrs['upto']}（可用 {w.attrs['days']} 日）")
        t = pd.DataFrame({"group": w["group"], **{f"{n}日": [windows_cell(r, n) for r in w.to_dict("records")] for n in (3, 5, 10, 20)}})
        print(t.to_string(index=False))

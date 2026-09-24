#!/usr/bin/env python3
"""從 MoneyDJ「細產業」抓題材分類（約 1,000 個細題材，如 AI伺服器、伺服器用散熱模組、BBU、ABF載板、人形機器人），
   存到快取 themes_moneydj.json，給族群清單（groups.yaml）以外的個股標題材用。

   約 1,000 頁、每頁間隔 0.3 秒 → 全部跑完約 6–8 分鐘；每月跑一次即可（stock-watch 超過 30 天會提醒）。
   官方產業別（半導體業、電子零組件業）太粗，不用。

用法:
  python themes_sync.py
"""
from __future__ import annotations
import json, re, time
from datetime import datetime
import requests
from flowlib import CACHE, UA

BASE = "https://concords.moneydj.com/z/zh"
OUT = CACHE / "themes_moneydj.json"


def get(url: str, tries: int = 3) -> str:
    for i in range(tries):
        try:
            r = requests.get(url, headers=UA, timeout=20)
            r.raise_for_status()
            return r.content.decode("cp950", "ignore")
        except requests.RequestException:
            if i == tries - 1:
                raise
            time.sleep(3 * (i + 1))


def main():
    html = get(f"{BASE}/zha/ZHA.djhtm")
    subs: dict[str, tuple[str, str]] = {}   # 細產業代碼 → (產業別, 細產業名)
    for row in re.split(r'<td class="t3t1"><a href="/z/zh/zhc/zhc\.djhtm\?a=', html)[1:]:
        parent = re.match(r'C\d+">([^<]+)<', row).group(1).strip()
        for code, name in re.findall(r'zhc\.djhtm\?a=(C\d+)">([^<]+)<', row):
            subs.setdefault(code, (parent, name.strip()))
    print(f"[info] {len(subs)} 個細題材，開始抓成分股…")
    themes, fails = {}, 0
    for i, (code, (parent, name)) in enumerate(subs.items(), 1):
        try:
            page = get(f"{BASE}/zhc/zhc.djhtm?a={code}")
        except requests.RequestException as e:
            fails += 1; print(f"[warn] {name}: {e}"); continue
        stocks = sorted({c for c in re.findall(r"GenLink2stk\('A[SO]?(\d{4})'", page)})
        if stocks:
            key = name if name not in themes else f"{parent}/{name}"
            themes[key] = {"parent": parent, "codes": stocks}
        if i % 100 == 0:
            print(f"  {i}/{len(subs)}")
        time.sleep(0.3)
    if fails > len(subs) * 0.1:
        raise SystemExit(f"[error] 失敗 {fails} 頁（>10%），不覆蓋舊檔")
    OUT.write_text(json.dumps({"updated": datetime.now().isoformat(timespec="seconds"), "themes": themes},
                              ensure_ascii=False))
    print(f"[ok] {len(themes)} 個題材、涵蓋 {len({c for t in themes.values() for c in t['codes']})} 檔 → {OUT}")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""從證交所／櫃買 OpenAPI 抓「產業別」生成 assets/groups_industry.yaml（每季跑一次即可）。
   人工族群 groups.yaml 為主；此檔補「官方產業分類」層，用 --industry 才會載入。"""
import requests, yaml
from pathlib import Path
OUT = Path(__file__).resolve().parent.parent / "assets" / "groups_industry.yaml"
SRC = [("https://openapi.twse.com.tw/v1/opendata/t187ap03_L", "公司代號", "產業別"),
       ("https://www.tpex.org.tw/openapi/v1/mopsfin_t187ap03_O", "SecuritiesCompanyCode", "SecuritiesIndustryCode")]
g = {}
for url, kc, ki in SRC:
    try:
        rows = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=30).json()
    except Exception as e:
        print(f"[warn] {url}: {e}"); continue
    for r in rows:
        code, ind = str(r.get(kc, "")).strip(), str(r.get(ki, "")).strip()
        if code.isdigit() and len(code) == 4 and ind:
            g.setdefault(f"產業_{ind}", []).append(code)
OUT.write_text(yaml.safe_dump(g, allow_unicode=True), encoding="utf-8")
print(f"[ok] {len(g)} 個產業別 → {OUT}")

# stock-common — 共用資料層

不是 skill（沒有 SKILL.md），是 stock-ta／stock-fa／stock-risk／stock-journal 共用的 Python 模組。

- `stockdata.py`：yfinance 日線（自動 .TW/.TWO、盤中快取 15 分鐘、剔除 NaN 棒）、FinMind、yf info、磁碟快取、JSON 輸出。
- 引用方式：`sys.path.insert(0, <skills 目錄>/stock-common)` 後 `from stockdata import ...`。
- 資料層只改這一份；以前 stock-ta 與 stock-fa 各有一份 `data.py`，分歧後 fa 少了盤中快取修正。

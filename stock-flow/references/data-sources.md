# stock-flow 資料源（全部 $0）

| 用途 | 端點 | 注意 |
|---|---|---|
| 盤中即時報價 | `https://mis.twse.com.tw/stock/api/getStockInfo.jsp?ex_ch=tse_2330.tw|otc_6488.tw&json=1&delay=0` | 非官方公開 API，約 5 秒更新；一次 ≤ ~100 檔；整體請求間隔建議 ≥ 3 秒，`--watch` 用 ≥ 60 秒。欄位：z 現價、y 昨收、o/h/l、v 累計量(張)、tv 最新一筆量、a/b 五檔賣/買價(`_`分隔)、t 時間、n 名稱 |
| 大盤即時成交值 | 同上，`ex_ch=tse_t00.tw|otc_o00.tw` | 成交金額在 `m` 欄，單位十萬元（÷10 → 百萬）；舊版的 `v` 欄已消失（2026-09 確認） |
| 上市收盤 | `https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL` | 欄位 Code, Name, ClosingPrice, Change, TradeValue |
| 上櫃收盤 | `https://www.tpex.org.tw/openapi/v1/tpex_mainboard_daily_close_quotes` | 欄位 Date(民國), SecuritiesCompanyCode, CompanyName, Close, Change, **TransactionAmount**（舊名 TradingAmount）；含權證約 5000 筆，金額佔比 <0.5% |
| 上市三大法人 | `https://www.twse.com.tw/rwd/zh/fund/T86?date=YYYYMMDD&selectType=ALLBUT0999&response=json` | 舊 OpenAPI `/v1/fund/T86` 已下線（302 → 404）。回傳 fields 為中文欄名，依欄名取值；可指定日期 |
| 上櫃三大法人 | `https://www.tpex.org.tw/openapi/v1/tpex_3insti_daily_trading` | 只給最新一日；欄名夾空白、大小寫不一（如 `ForeignInvestorsInclude MainlandAreaInvestors-Difference`），程式先去掉非英文字母再比對 |
| 法人備援 | FinMind `TaiwanStockInstitutionalInvestorsBuySell` | 任一市場取不到或全為 0 時補該市場 |
| 5 分 K（短窗量比基準） | yfinance `interval=5m`（最多 60 天） | 延遲約 20 分；只到 13:20、無收盤集合競價，總量約為日量 87%（穩定），故只取各時段佔比 |
| 法人備援 | FinMind `TaiwanStockInstitutionalInvestorsBuySell`（不帶 data_id，整市場單日） | 免費層可用；設 `FINMIND_TOKEN` 提高額度 |
| 產業別 | TWSE `opendata/t187ap03_L`、TPEx `mopsfin_t187ap03_O` | `groups_sync.py` 用 |

## 快取
`~/.cache/stock-toolbox/flow/`：`eod_YYYYMMDD.csv`（全市場收盤，5 日基準用）、`eod_groups_YYYYMMDD.csv`（族群法人，`--history` 用）、`intraday_YYYYMMDD.csv`（盤中快照累積）、`market_map.json`（上市/上櫃對照，30 天）。
每天盤後跑一次 `flow_eod.py` 才會累積基準；可用 cron `35 15 * * 1-5`。

## 已知限制
- 盤中成交值為估計（VWAP 近似），大盤成交值用指數 v 欄位，兩者口徑略不同，佔比可能有 ±0.5 個百分點誤差。
- 內外盤只用「最新成交價貼近買/賣價」判斷一筆，是粗估，不等於主動買賣金額。
- 上述端點欄位名稱由交易所維護，若腳本印出 warn 或全為 0，先開 `references/data-sources.md` 對照欄位再改 `flowlib.py`。
- 美股沒有免費即時族群資金流，本 skill 只做台股。

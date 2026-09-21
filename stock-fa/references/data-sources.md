# 資料源與成本（目標：每月 ≤ US$11，預設 US$0）

| 資料 | 來源 | 費用 | 額度 | 用在 |
|---|---|---|---|---|
| 台股／美股 OHLCV、週線 | yfinance | $0 | 非官方，勿高頻（每支每 6 小時快取一次） | ta_quick / ta_precise / pe_band |
| 美股／台股 `.info`、季報三表 | yfinance | $0 | 同上，快取 24h | fa_quick / fa_precise |
| 台股月營收、法人買賣超、融資券、股利、交易所 PER/PBR | FinMind v4 | $0 | 免註冊 300 次/小時；註冊免費 token 600 次/小時 | fa_quick / fa_precise / ta_precise |
| 大盤指數 ^TWII / ^GSPC | yfinance | $0 | — | 相對強弱 |

## 可選升級（仍在 $11 內，二選一）
- **FinMind 贊助方案**：解除頻率限制、增加即時資料。價格請以 finmindtrade.com 當下公告為準（過去約 NT$ 300/月，換算約 US$9–10，此數字未即時核對）。適合一天要掃 >100 支台股時。
- **Alpha Vantage / Finnhub 免費層**：美股基本面備援，$0；只在 yfinance 掛掉時切換。

## 環境變數
- `FINMIND_TOKEN`：註冊後填入即提升到 600 次/小時。
- `STOCK_CACHE`：快取目錄，預設 `~/.cache/stock-toolbox`。

## 已知限制（寫報告時要提）
1. Yahoo 台股季報科目對應偶有錯位（例如營業利益與稅前淨利混淆），異常季請回 MOPS 核對。
2. FinMind `TaiwanStockPER` 的 PER 由交易所用最近四季 EPS 計算，與分析師 forward PE 不可直接比。
3. yfinance `dividendYield` 在部分版本已是百分比、部分是小數，腳本已做判斷但仍建議與交易所殖利率交叉看。
4. 美股法人籌碼（13F）不在免費即時範圍，本工具箱不提供。
5. yfinance 通常只回傳最近 5 季財報；台股已改用 FinMind 取損益（3 年單季）與交易所 PER（5 年），美股的河流圖仍是季 EPS 滾動自算，早期區間用最早可得 EPS 往前補，僅供參考。
6. FinMind `TaiwanStockFinancialStatements` 的 `type` 名稱以官方為準（Revenue / GrossProfit / OperatingIncome / IncomeAfterTaxes / EPS），腳本用寬鬆比對，若某欄全 None 先印一次 `piv.columns` 對照。

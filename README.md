# Buy-Right — 台股右側動能交易工具箱（Claude Code skills）

策略：追強勢、突破才進場、錯了就出場。找資金流向 → 找最強標的 → 確認突破 → 依風險算部位 → 移動停利 → 事後檢討。

| skill | 用途 |
|---|---|
| `stock-flow` | 族群資金流向（盤中成交佔比 vs 近 5 日、盤後三大法人）、盤中短窗量比異動掃描 |
| `stock-watch` | 一鍵盯盤：大盤環境、族群流向、盤中異動、自選與持倉狀態、警示 |
| `stock-ta` | 技術分析：快速多檔掃描／單檔精準（支撐壓力、籌碼、訊號歷史勝率） |
| `stock-fa` | 基本面：月營收、8 季財報、本益比河流圖、同業比較 |
| `stock-risk` | 風控：大盤環境（多頭／震盪／空頭）、部位大小（1R）、移動停利 |
| `stock-journal` | 交易日誌：進場論點與證偽條件、每日檢查、檢討統計，rclone 同步 Google Drive |
| `stock-common` | 共用資料層（非 skill）：行情、FinMind、快取，上面各 skill 共用 |

## 安裝
1. 放到 `~/.claude/skills/`（或設 `STOCK_TOOLBOX` 指向此目錄）。
2. `pip install -r stock-*/requirements.txt`
3. 複製範本成個人設定（不進 git）：
   - `stock-risk/assets/config.example.yaml` → `config.yaml`（總資金、每筆風險 %）
   - `stock-watch/assets/watchlist.example.yaml` → `watchlist.yaml`（持股／觀察）
   - `stock-journal/assets/config.example.yaml` → `config.yaml`（日誌路徑、rclone）
   沒複製時程式會直接讀範本。
4. 選用：`FINMIND_TOKEN`（FinMind 免費註冊，300 → 600 次/小時）。

資料來源全部免費：證交所／櫃買 OpenAPI 與 mis 即時報價、yfinance、FinMind 免費層。本工具只做紀錄與分析，不下單、不構成投資建議。

## 測試
```bash
python -m unittest discover -s stock-journal/tests -v   # 不連網、不碰 ~/stock-journal
```

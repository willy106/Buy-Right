---
name: stock-flow
description: 台股盤中／盤後「族群資金流向」工具（免費即時報價＋證交所法人資料）。盤中：每族群成交值佔大盤比、相對近 5 日的資金移入移出、平均漲幅、漲跌家數、外盤比、領漲股，可持續刷新；盤後：三大法人（外資／投信／自營）按族群加總的淨買賣金額、法人主導程度、成交值佔比變化。只要使用者問「今天錢往哪裡跑」「哪個族群在流入／流出」「資金輪動」「法人在買哪個族群」「盤中族群強弱」「AI 伺服器／散熱／CPO 族群今天怎樣」、要看族群排行或想加族群清單，就用這個 skill，即使沒說「資金流向」四個字。
---

# stock-flow — 族群資金流向

## 模式選擇

| 情境 | 腳本 | 說明 |
|---|---|---|
| 交易時段 09:00–13:30，問「現在／今天盤中」 | `scripts/flow_intraday.py` | 一次或 `--watch` 持續刷新（`--interval` ≥ 60 秒，避免被限流） |
| 13:30 後或問「今天法人」「收盤後」 | `scripts/flow_eod.py` | 法人資料約 15:30 上市、16:00 上櫃後才齊 |
| 「哪檔正在爆量」「盤中異動」「現在誰在被買」 | `scripts/flow_surge.py` | 短窗量比：最近 15 分鐘量 ÷ 20 日同時段均量，掃族群清單全部標的 |
| 想看族群近幾日輪動 | `flow_eod.py --history 5` | 需要每天跑過一次才有快取 |
| 使用者要加／改族群 | 編輯 `assets/groups.yaml` | 純數字代號；同一檔可屬多族群 |
| 想用官方產業別 | 先跑 `scripts/groups_sync.py`，再加 `--industry` | 每季同步一次即可 |

先用 `user_time_v0`／系統時間判斷是否在交易時段，再選腳本；非交易時段跑盤中腳本會拿到最後一筆快照，要標明時間。

## 執行
```bash
python <skill_dir>/scripts/flow_intraday.py --top 6 --json /tmp/flow.json
python <skill_dir>/scripts/flow_intraday.py --watch --interval 90        # 使用者要持續看時
python <skill_dir>/scripts/flow_eod.py --chart /tmp/flow_eod.png --json /tmp/flow_eod.json
python <skill_dir>/scripts/flow_eod.py --history 5
python <skill_dir>/scripts/flow_surge.py --top 10 --json /tmp/surge.json      # 盤中異動
python <skill_dir>/scripts/flow_surge.py --watch --interval 60                # 盤中取樣器（每分鐘存快照）
```
缺套件：`pip install pandas numpy requests pyyaml matplotlib yfinance`。

## 解讀規則
1. **資金流向 ≠ 漲幅**。判斷「流入」看 `share_vs_5d`（成交值佔比相對近 5 日）為正，且 `avg_chg` 為正、`up > down`；只有漲幅沒有佔比上升，寫「跟漲」不寫「資金流入」。
2. 盤中成交值是 **估計值**（用 (開高低現)/4 近似 VWAP），標【推估】；盤後成交值與法人數字來自證交所／櫃買，標【事實】。
3. 盤後 `inst_vs_turnover_pct` > 10% 才算「法人主導」；投信單獨買超集中在少數族群時要點出（作帳／季底效應）。
4. `share_vs_5d` 沒有值代表快取不足 5 天，說明「基準不足，僅看當日佔比」。
5. 族群內若只有 1–2 檔貢獻了大部分成交值（看 `leaders`），要說是「個股行情」而非族群行情。
6. 族群清單是人工維護，同一檔會跨族群（例如 3037 同時在 PCB 與載板），比較族群時要提醒重疊。
7. 不做左側建議；流出族群只描述，不說「可以逢低承接」。
8. `flow_surge` 的 `src=yf~HH:MM` 是延遲約 20 分的資料（當天首次執行、無 N 分鐘前快照時的退路），要講明時間；`src=snap` 才是即時。同族群多檔爆量流入才叫族群行情。

## 輸出格式
```
# 族群資金流向 <日期 時間>（盤中｜盤後）  大盤成交值 xxxx 億
## 流入 TOP N   表：族群｜佔比%｜vs 5日｜平均漲幅｜漲/跌｜（盤後：外資/投信/合計 M）｜領漲或法人買最多
## 流出 TOP N
## 觀察（3–5 點）  哪些是真流入、哪些是個股行情、法人與價格是否同向、與前幾日相比的輪動
```
盤後附堆疊長條圖（外資／投信／自營）。

## 參考
- `references/data-sources.md`：即時報價欄位、限流建議、法人 API 欄位名稱（若證交所改欄位先看這裡）。

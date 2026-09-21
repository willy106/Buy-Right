---
name: stock-journal
description: 交易日誌與操作檢討（台股／美股），日誌同步到 Google Drive。進場時記錄論點類型、進場論點與證偽條件；每日盤後自動抓價、更新最高浮盈／最大回檔、檢查證偽條件是否觸發、超過預計持有期就提醒；出場後產生檢討報告：勝率、賺賠比、期望值、依論點類型的績效、證偽觸發後的出場紀律率、賣飛程度。使用者說「我買了 xxxx」「記一下這筆」「我進場了」「平倉／賣掉了」「檢討我的操作」「我的交易紀錄」「這個月做得怎樣」「日誌同步」，就用這個 skill。
---

# stock-journal — 交易日誌與檢討

本機 `~/stock-journal/`（`assets/config.yaml` 可改）：`trades.csv`（統計）、`journal_YYYYMM.md`（可讀，按月分檔，含每日筆記；新月份開頭自動列出承上月持倉）、`review_YYYYMM.md`。每次寫入後用 rclone 同步到 Google Drive。首次使用先看 `references/rclone-setup.md`。

## 指令
```bash
python <skill_dir>/scripts/journal.py add 6239 --price 150 --qty 5 --name 力成 \
   --type 族群流入 --thesis "封測族群連3日資金流入，站回MA20" \
   --falsify "收盤跌破142或投信轉賣超3日" --falsify-price 142 --days 20
#  動態證偽：--falsify-ma 5（每日盤後重算 MA5）；與 --falsify-price 並用時取較高者（守固定價，均線上來後改守均線）
python <skill_dir>/scripts/journal.py check              # 盤後跑；也適合排程
python <skill_dir>/scripts/journal.py close 3 --price 158 --reason 達標 --note "族群輪動"
python <skill_dir>/scripts/journal.py review --months 3
python <skill_dir>/scripts/journal.py list --all
python <skill_dir>/scripts/journal.py sync
```
`--type` 從 config 的 `thesis_types` 選；`--reason` 只有 達標／證偽／情緒／其他 四種，**分類要誠實**，檢討的價值全在這裡。

## 記錄時你要做的事
使用者說「我買了 6239 在 150」時，**不要只記價格**。缺少的欄位要問，但一次問完、用選項形式：
1. 進場論點是什麼？（族群流入／技術突破／均線回測／基本面／事件）
2. 什麼情況你會承認這筆看錯？最好給一個價位。
3. 預計抱多久？

若使用者答不出證偽條件，這件事本身值得說：沒有證偽條件的單子事後無法檢討，也容易凹單。可以用工具箱幫他找一個合理價位（`stock-ta` 精準模式的支撐區、或進場價 −1.5×ATR），但要標明是建議、由他決定。

## 檢討時你要做的事
跑 `review` 後，報告已含數字。你要補的是**解讀，而且要誠實**：
- 期望值為負但勝率高 → 賺小賠大，問題在出場不在選股。
- 「情緒」出場佔比高、或證偽紀律率低 → 問題在執行不在方法，不要再談選股。
- 某個論點類型 n < 5 → 明說樣本不足，不要下結論。
- 「最高浮盈 − 實際獲利」大 → 賣飛或抱回來，可討論移動停利。
- 平均持有遠超預計 → 凹單傾向，對照那些單子的損益。
- 超額報酬（對同期大盤）才是選股能力；絕對報酬為正但超額 ≤ 0 → 只是搭大盤順風車。當沖單大盤基準記 0，超額≈絕對報酬，看選股能力時排除。
- 不要因為結果好就說論點對（可能只是運氣），看的是論點成立與否、證偽有無觸發。

使用者虧損時照實講數字，但對事不對人；不用鼓勵性的空話，也不要落井下石。

## 風控整合（有裝 stock-risk 時自動）
- `add` 帶 `--falsify-price` 會自動算建議部位並比對實際張數；`--stop-init` 可另外指定算 R 用的初始停損（預設＝證偽價）。**之後把停損上移到保本，也不要改 stop_init**，R 才算得對。
- `check` 會寫入 `trail_stop`（移動停利，只上移）與 `falsify_level`（當日實際防守價＝固定價、動態 MA、移動停利取最高），日誌多一行大盤環境。
- 出場原因多一個 `停利`：跌破移動停利出場用它，跌破原始證偽條件才用 `證偽`。
- `review` 多了 R 倍數：虧損超過 1.2R 的單要點名（跳空還是拖延）。

## 與工具箱其他 skill 的關係
- 進場論點若來自盯盤報告，把 `stock-watch` 當時的族群排名寫進 `--thesis`。
- 證偽價位建議由 `stock-ta` 精準模式的支撐區提供。
- `stock-watch` 盤中報告會讀 `trades.csv`，在自選標的表標出持倉與證偽狀態。

## 注意
- 這是紀錄與統計工具，不下單、不建議部位大小。
- 使用者問「現在該加碼嗎」不屬於本 skill，轉到 ta/flow 給資料，由他自己決定。

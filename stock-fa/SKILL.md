---
name: stock-fa
description: 股票基本面分析工具箱（台股＋美股，免費資料源）。分「快速」與「精準」兩種模式：快速 = 估值／獲利／成長／財務結構一頁快照，台股加月營收 YoY、MoM、YTD，可多支同業並排；精準 = 單支 8 季三表趨勢、DuPont、盈餘品質（OCF/NI、存貨與應收天數）、本益比河流圖、台股股利與交易所本益比，可加同業比較表。只要使用者提到基本面、財報、營收、毛利率、EPS、本益比、PB、ROE、估值、貴不貴、同業比較、股利、現金流、「這家公司體質」、或給代號問「值不值得研究」，就用這個 skill；若是台股且使用者要完整九大區塊研究報告，先跑本 skill 取數，再交給 tw-stock-analysis 框架寫報告。
---

# stock-fa — 基本面分析工具箱

## 模式選擇

| 使用者說的話 | 模式 | 腳本 |
|---|---|---|
| 「貴不貴」「快速看體質」「月營收」、多支並排、一句話結論 | **快速** | `scripts/fa_quick.py` |
| 「精準」「深入」「財報趨勢」「盈餘品質」「本益比區間／河流圖」「跟同業比」 | **精準** | `scripts/fa_precise.py` |
| 要寫完整研究報告（台股） | 精準取數 → 套 `tw-stock-analysis` 九大區塊 | — |

## 執行

```bash
python <skill_dir>/scripts/fa_quick.py 2330
python <skill_dir>/scripts/fa_quick.py 2330 2454 3711        # 同業並排
python <skill_dir>/scripts/fa_precise.py 2330 --out /tmp/2330_fa
python <skill_dir>/scripts/fa_precise.py 2454 --peers 2330 3711 --out /tmp/2454_fa
```
輸出 `<out>.json`、`<out>_pe_band.png`、`<out>_trend.png`。首次缺套件：`pip install yfinance pandas numpy matplotlib requests`。

## 資料可信度（寫報告前先分級）

| 欄位 | 來源 | 標籤 |
|---|---|---|
| 台股月營收、法人、融資券、股利、交易所 PER/PBR | FinMind（轉載 MOPS / TWSE） | 【事實】 |
| 台股 8 季損益、本益比河流圖 | FinMind（MOPS 單季申報、交易所每日 PER） | 【事實】 |
| 美股 8 季三表；台股資產負債／現金流品質指標 | yfinance（Yahoo 整理） | 【事實】但需注意幣別與科目對應，異常值要回 MOPS／10-Q 核對；yfinance 通常只有 5 季，YoY 只有 1 季可算 |
| yfinance `.info` 的 PE/ROE/margins/analyst | Yahoo 彙整 | 【推估】級，台股尤其常有幾季落差 |
| 美股本益比河流圖 | 季 EPS 滾動 4 季自算 | 【推估】 |

輸出 JSON 每個區塊都帶 `source`，寫報告時照抄標籤，不要把【推估】寫成【事實】。

## 解讀規則

1. **成長看營收 YoY 和毛利率方向的組合**：營收 YoY↑ 但 GM↓ 要問是產品組合還是殺價；營收↓ 但 GM↑ 要問是高毛利產品佔比或是折舊減少。
2. **盈餘品質**：`OCF/NI_ttm < 0.8` 或 存貨 YoY 明顯高於營收 YoY，要點名為警訊，不可略過。
3. **估值一定放進歷史區間**：本益比 percentile > 80 要說「處於自身歷史高檔」，不用「合理」帶過；同業比較用 forward PE，且要說明各家 forward EPS 來源不同（Yahoo 分析師共識）。
4. **台股**：月營收 MoM 受工作天數影響，用 YoY 與 YTD YoY 為主；季報公布月（3/5/8/11 月）前後 Yahoo 資料可能未更新，要註明資料日期。
5. **不給目標價，給證偽條件**：例如「若 Q3 GM 低於 xx% 或月營收 YoY 連兩月轉負，成長論述失效」。
6. 分析師目標價（`analyst`）只能標【市場預期】，不能當結論。
7. 使用者是嚴謹派：數字要附單位（億／M／%），四捨五入到有意義的位數，不要湊整。

## 快速模式輸出格式

一支：估值一行、獲利一行、成長一行、結構一行、台股加月營收一行、最後一句「值得深挖的點」。
多支：一張表（代號｜市值｜fwd PE｜PB｜GM%｜OPM%｜ROE%｜營收 YoY%｜月營收 YoY%），加 2–3 句相對比較。

## 精準模式輸出格式

```
# <代號 名稱> 基本面 — <資料日期>
## 結論
## 成長與獲利趨勢【事實】  8 季營收／YoY／GM／OPM，指出轉折季
## DuPont 與盈餘品質【事實＋推估】
## 估值【事實＋推估】  現在 PE、歷史 percentile、同業表；台股附交易所 PER/PBR、殖利率
## （台股）股利與月營收【事實】
## 證偽條件【推估】
## 資料缺口  列出 JSON 為 None 的關鍵欄位，建議去 MOPS／10-Q 補
```
附 `_trend.png` 與 `_pe_band.png`。

## 參考
- `references/data-sources.md`：每個欄位來自哪個資料源、免費額度、如何升級（總預算 ≤ US$11/月）。

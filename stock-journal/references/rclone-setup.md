# rclone 設定（一次性，約 3 分鐘，$0）

```bash
sudo apt install rclone            # 或 curl https://rclone.org/install.sh | sudo bash
rclone config                      # n → 名稱輸入 gdrive → storage 選 drive → client_id/secret 填自己在 Google Cloud Console 建的 OAuth 用戶端（Desktop app，並 Publish app 以免 token 7 天失效）；rclone 內建共用 client_id 預計 2026 年停用
                                   # scope 選 1 (full) → 其餘 Enter → 瀏覽器登入 Google 授權 → y 確認
rclone lsd gdrive:                 # 看得到 Drive 根目錄就完成
rclone mkdir gdrive:stock-journal
```
無桌面環境（SSH）時：`rclone config` 會給一段 `rclone authorize "drive"` 指令，到有瀏覽器的電腦執行後把 token 貼回來。

## 同步策略
腳本用 `rclone sync <local_dir> gdrive:stock-journal --exclude ".*"`，本機為主、雲端為鏡像；**不要**在雲端直接改 CSV（會被下次同步覆蓋）。想在手機看用 Google Drive App 開當月的 `journal_YYYYMM.md` 即可。
若要雙向，改成 `rclone bisync`（需先 `--resync` 一次），config.yaml 的 `sync_mode: bisync`。

## 排程
`crontab -e`：
```
40 15 * * 1-5  cd ~/.claude/skills/stock-journal/scripts && python journal.py check
```
`check` 會補當日持倉狀態、檢查證偽條件、然後同步。

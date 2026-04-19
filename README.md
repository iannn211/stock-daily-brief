# Stock Daily Brief

自動抓台股 + 美股新聞 → 按產業分類 → 產生一段可以貼給 Claude.ai 分析的 prompt。

## 它在做什麼

每個工作日早上 07:30（台北時間），GitHub Actions 會：
1. 從 7 個財經 RSS 源抓最近 36 小時的新聞
2. 按使用者持股 (0050、2330、VOO) 和 8 個產業題材分類
3. 產出 `briefs/YYYY-MM-DD.md`（commit 回 repo）
4. 開一個 GitHub Issue，內容就是那份 brief
5. GitHub 自動寄 email 通知 → 收信複製內容 → 貼到 Claude.ai 分析

## 本機測試

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python daily_brief.py
# 產出檔案：briefs/YYYY-MM-DD.md 和 briefs/latest.md
```

## 調整

- **加/減新聞源**：改 `daily_brief.py` 裡的 `FEEDS`
- **加/減持股**：改 `HOLDINGS`
- **加/減產業關鍵字**：改 `SECTORS`
- **排程時間**：改 `.github/workflows/daily-brief.yml` 的 `cron`
  - cron 用 UTC；07:30 Taipei = `30 23 * * 0-4`（上一天 23:30 UTC，週日到週四）

## 之後想升級的方向

- v2：GitHub Pages 網頁 dashboard（瀏覽歷史 brief）
- v3：呼叫 Claude API 直接產生分析，不用手動貼
- v4：LINE Bot 推播（LINE Messaging API）
- v5：加證券商研究報告、法說會逐字稿、外資買賣超

## Provenance 怎麼讀（dashboard 上的彩色小點）

Dashboard 上每個關鍵數字旁邊可能會看到一個彩色圓點 — 那是**資料來源**。
（spec：`specs/fix-08-provenance-layer.md`）

| 圓點 | 意思 | 例子 |
|:---:|------|------|
| 🟢 | **官方／原始報告** (primary_report) | TWSE 三大法人期貨未平倉、TAIFEX 融資融券、分析師研報 |
| 🔵 | **使用者確認** (user_input) | `supply_chains.yaml` 裡明確寫的題材→股票對應 |
| 🟡 | **媒體綜合** (secondary_news) | Gemini 綜合多家財經媒體的陳述 |
| 🔴 | **LLM 推論** (llm_inference) | Gemini 自行推論、無第一手引用 → 最要存疑 |

**信任順序：🟢 > 🔵 > 🟡 > 🔴**。

滑鼠移到點上會看到：資料來源 / 日期 / 幾天前 / LLM 信心分數。如果資料太舊
（例如散熱股 target price 30 天前），旁邊會出現 `⚠ N 天` 過期提醒 — 過期
天數按產業速度分級（半導體嚴、金融寬）。

### 例：Opportunity Radar 的 lead stocks

一個題材 Gemini 挑 5 檔時，會看到混合 🔵 和 🔴：
- 🔵 = 該檔在你 `supply_chains.yaml` 的同題材名單裡（**雙重確認**）
- 🔴 = Gemini 自己選的、yaml 沒收錄（需要你判斷這檔到底算不算該題材）

想調整：編輯 `supply_chains.yaml`，下次 rebuild 後新增的股票自動從 🔴 升級 🔵。
可用 `.venv/bin/python scripts/provenance_audit.py` 看整體 🔵/🔴 分布。

## 疑難排解

- **某個 RSS 源 404**：腳本會印警告然後跳過，不會整個掛掉。多試幾天如果還是壞，就把那條 FEEDS 拿掉。
- **GitHub Actions cron 沒跑**：免費 repo 如果 60 天沒 activity，schedule 會被暫停。推個 commit 就會恢復。
- **brief 是空的**：檢查 Actions log 看哪些 feed 失敗。可能是 IP 被 rate-limit（GitHub runner 的 IP 偶爾會被擋）。

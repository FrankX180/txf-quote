<!-- Generated: 2026-09-01 | Updated: 2026-09-01 -->

# FuturesHTML（txf-quote）

## Purpose

公開單頁台指期近月看盤站。前端 `index.html` 上 GitHub Pages；即時報價／1 分 K 走 Cloudflare Worker `wtx.blok.trading`（舊別名 `wtx.19850926.xyz`）；靜態 JSON 由 Actions 每 5 分寫入 `data/`。

**全站雲端自足（硬原則）**：運行與部署都不依賴任何本機檔案／服務。

- Pages：`https://frankx180.github.io/txf-quote/`
- Repo：`https://github.com/FrankX180/txf-quote`
- Worker：`https://wtx.blok.trading/`（舊 `https://wtx.19850926.xyz/`）

## Key Files

| File | Description |
|------|-------------|
| `index.html` | 唯一前端（報價、五檔、走勢、K、法人表）。頁腳 VER 與 `purgeOldMinCache` 的 VER **必須同號** |
| `README.md` | 極短入口；說明書在 `01_Docs/` |
| `jian.md` | 備忘：改碼要推 GitHub 並升版 |
| `.gitignore` | 忽略 `99_TempScripts/`、`Backup/`、pyc |

## Subdirectories

| Directory | Purpose |
|-----------|---------|
| `00_DevLog/` | 工作日誌 `YYYYMM/YYYYMMDD.md`（見 `00_DevLog/AGENTS.md`） |
| `01_Docs/` | 規格 SSOT（見 `01_Docs/AGENTS.md`） |
| `data/` | Actions 寫入的 JSON，進 Pages（見 `data/AGENTS.md`） |
| `scripts/` | 抓檔／部署（見 `scripts/AGENTS.md`） |
| `worker/` | Cloudflare Worker（見 `worker/AGENTS.md`） |
| `.github/` | `update-quote.yml`（每 5 分抓檔，休市日由 guard job 跳過）＋ `refresh-holidays.yml`（每月更新休市日）＋ `deploy-worker.yml`（push worker 即雲端部署） |
| `90_Tools/` | 空殼 |
| `98_Archive/` | 舊日誌 digest；`_local_deprecated/` 放已除役的本機專用腳本（daemon／relay） |
| `99_TempScripts/` | 探測腳本，gitignore |
| `Backup/` | 資料檔備份，gitignore |

## For AI Agents

### Working In This Directory

1. **發佈鐵律（硬）**：改 `index.html`／`worker/yahoo-quote.js` 同一輪必須  
   - 升頁腳 VER **與** `txfMinVer`（同號，例如 2.13）  
   - `git commit` + **push `origin/master`**  
   - 改 Worker：push 後由 `.github/workflows/deploy-worker.yml` **雲端自動部署**（repo secret `CLOUDFLARE_API_KEY`）；本機可選跑 `scripts/deploy_worker.py`  
   - 遠端常有行情快照：用 cherry-pick 接到最新 `origin/master` 再推，不要跟 `data/*.json` rebase 打架  
   - SSOT：`01_Docs/發佈鐵律.md`  
   - **本機改完 ≠ 線上。右下角版號沒變＝沒吃到。**
2. Yahoo 夜盤時間戳：平日 −1 天、週五夜最多 −2 天。**禁止無限 while 回移**（會把 KEEP_DAYS 舊夜盤疊進今晚，千點刷針）。SSOT：`01_Docs/Yahoo-台指期K線日期慣例.md`
3. 不要把盤中 5 秒全量價 commit 進 GitHub。
4. 根目錄不發明 02–09 業務夾：這是單頁＋Actions，不是 pipeline。
5. **休市日 SSOT**：`data/holidays.json`（`scripts/fetch_holidays.py` 抓證交所「市場開休市日期」產生，Actions 每月自動更新）。前端（`loadHolidays`）、GitHub Actions（`scripts/holiday_guard.py`）、Worker（`ensureHolidays`）**都讀同一份，不得各自硬編**。假日只檔日盤與當晚夜盤；**凌晨 00:00–05:09 仍屬前一營業日夜盤，不可檔**。
6. **雲端自足（硬）**：本站一切**運行**必須在雲端（GitHub Actions／Cloudflare Worker／Pages）自力完成，**嚴禁**任何環節依賴本機（本機 daemon、本機 PG、本機 relay、絕對路徑）。已除役者見 `98_Archive/_local_deprecated/`，不得復用；Worker **部署**亦走 `deploy-worker.yml`。動手前先自問：**「老公電腦關機，這條還跑得動嗎？」**

### Testing Requirements

- 線上驗：右下角 VER、夜盤走勢對玩股網（15:00 起單一路徑，一小時約 60 根 1 分 K）
- Worker：`https://wtx.blok.trading/?kind=px1m`

### Common Patterns

- 前端 `gh(path)` 相對路徑吃 Pages 的 `data/`
- `LIVE = "https://wtx.blok.trading/"`
- `session` 日／夜；`daySpan` 0＝當日走勢

## Dependencies

### External

- 奇摩 `StockServices.stockList` / `chart`（`WTX&`）
- 期交所／MoneyDJ／富邦 DJ（腳本）
- Cloudflare Worker + D1 `txf-imb`
- klinecharts CDN

<!-- MANUAL: 2026-09-01 夜盤刷針＝無限回移疊歷史；修在 2.13 -->
<!-- MANUAL: 2026-09-11 日盤收盤後五檔被奇摩退化書清空；收盤至夜盤轉換（13:46–14:57）凍結真書，修在 2.24/2.25 -->

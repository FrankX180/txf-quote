<!-- Generated: 2026-09-01 | Updated: 2026-09-01 -->

# FuturesHTML（txf-quote）

<!-- session-history:begin -->
<!-- 本段由 E:\_PluginTools\AgentHome\Script\apply_session_history.py 維護；直接改這裡，下次重跑會被覆寫。-->
## 對話歷史（需要時再查，不必每次開場跑）

老公的習慣是「哪個話題就點哪個資料夾開對話」。這裡有依時間排序的對話索引，**知道去哪查就好，不要每次開場都把對話原文讀一遍**（容量太大）。

**什麼時候查**：老公說「上次／之前／繼續／我們談過」、要接續某條研究或決策、或發現自己在憑印象猜舊結論時。

1. 先看本資料夾的 `_對話索引.md`（時間、則數、檔名，不含內文），確認有哪幾條。
2. 要摘要才跑（約 2 秒，抽最近 5 條的開頭與收尾）：
   `& R:\PythonProgram\Python312\python.exe E:\_PluginTools\AgentHome\Script\folder_session_log.py --dir . --limit 5`

3. 要細節才針對那一條 `.jsonl` 讀區段，一次只讀需要的那一條。
4. `_對話索引.md` 可重跑覆蓋；索引不含對話內文（這個資料夾可能對外）。
5. session 原文在 `C:\Users\AnyUser\.hanako\agents\<agent>\sessions\`，綁 agent 不綁資料夾；反查用 cwd（腳本已跨 7 個 agent＋子資料夾）。

不用查的情況：純問答、單純貼資料、與本專案無關的閒聊——直接做事，不要為了儀式感去讀舊對話。
<!-- session-history:end -->

## Purpose

公開單頁**大台（TXF）**近月看盤站。前端 `index.html` 由 Cloudflare Workers Static Assets（worker `txf-site`）供應（2026-11-23 起；舊 GitHub Pages 只剩 301 入口）；即時報價／1 分 K 走 Cloudflare Worker `wtx.blok.trading`（舊別名 `wtx.19850926.xyz`）；靜態 JSON 由 Actions 每 5 分寫入 `data/` 並自動重新部署。

**2026-10-29 曾短暫改小台 MXF，同日已 revert 回大台 TXF**（小台內外盤資料不完整、夜盤無分時源）。目前資料源：

| 用途 | 來源 |
|------|------|
| 報價／五檔 | 期交所 MIS `getQuoteList`（`TXF*-F` 日／`TXF*-M` 夜）＋ SockJS 推流完整五檔 |
| 1 分 K 日盤 | 富邦 DJ `CZMB.djbcd?a=FITXN`（大台） |
| 1 分 K 夜盤 | Yahoo `WTX&`（大台） |
| 5／15 分 | 由 1 分自行聚合 |
| 日／週／月 K | MoneyDJ `FITX`（大台） |

**全站雲端自足（硬原則）**：運行與部署都不依賴任何本機檔案／服務。

- 正式站：`https://txf.blok.trading/`（Workers Static Assets，靜態請求免費且無流量上限）
- 舊入口：`https://frankx180.github.io/txf-quote/`（GitHub Pages 301 → 正式站）
- Repo：`https://github.com/FrankX180/txf-quote`
- Worker：`https://wtx.blok.trading/`（舊 `https://wtx.19850926.xyz/`）

## Key Files

| File | Description |
|------|-------------|
| `index.html` | 唯一前端（報價、五檔、走勢、K、法人表）。頁腳 VER 與 `purgeOldMinCache` 的 VER **必須同號** |
| `wrangler.site.toml` | `txf-site`（Workers Static Assets）設定；`[assets]` 指向 `./public` |
| `scripts/build_site.py` | 產生 `public/`：只收 `index.html` ＋頂層 `data/*.json`（白名單，不碰 `data/Backup`）＋`_headers`；先建 `public.tmp` 再 rename |
| `scripts/deploy_site.py` | 本機／CI 部署 `txf-site`（金鑰只注入子進程、不印出；逾時 600 秒） |
| `README.md` | 極短入口；說明書在 `01_Docs/` |
| `jian.md` | 備忘：改碼要推 GitHub 並升版 |
| `.gitignore` | 忽略 `99_TempScripts/`、`Backup/`、pyc |

## Subdirectories

| Directory | Purpose |
|-----------|---------|
| `00_DevLog/` | 工作日誌 `YYYYMM/YYYYMMDD.md`（見 `00_DevLog/AGENTS.md`） |
| `01_Docs/` | 規格 SSOT（見 `01_Docs/AGENTS.md`） |
| `data/` | Actions 寫入的 JSON，進 git 後自動部署到 Workers（見 `data/AGENTS.md`） |
| `scripts/` | 抓檔／部署（見 `scripts/AGENTS.md`） |
| `worker/` | Cloudflare Worker（見 `worker/AGENTS.md`） |
| `.github/` | `update-quote.yml`（每 5 分抓檔，休市日由 guard job 跳過）＋ `twn-hist.yml`（每 5 分存 SGX 富台 1 分 K 到 `data/twn-hist.json`，不套休市閘門）＋ `refresh-holidays.yml`（每月更新休市日）＋ `typhoon-check.yml`（營業日 09:20/09:50/10:30 以鴻海期 WDHF&＋台積電期 WCDF& 是否成交偵測颱風假，寫 holidays.json `adhoc`）＋ `deploy-worker.yml`（push worker 即雲端部署）＋ `deploy-site.yml`（`index.html`／`wrangler.site.toml`／建置腳本改動即部署 `txf-site`）＋ `fubon-imb.yml`（每 5 分用富邦逐筆補 D1 內外盤缺口；只補不覆寫 Yahoo；secrets 無交易密碼） |
| `90_Tools/` | 空殼 |
| `98_Archive/` | 舊日誌 digest；`_local_deprecated/` 放已除役的本機專用腳本（daemon／relay） |
| `99_TempScripts/` | 探測腳本，gitignore |
| `Backup/` | 資料檔備份，gitignore |

## For AI Agents

### Working In This Directory

1. **發佈鐵律（硬）**：改 `index.html`／`worker/yahoo-quote.js` 同一輪必須  
   - 升頁腳 VER **與** `txfMinVer`（同號，例如 2.13），**且與程式改動放在同一個 commit**（2026-10-29 曾先推未升號版本、再另起 chore 升版，origin/master 短暫存在版號與內容不符的中間版本）  
   - `git commit` + **push `origin/master`**  
   - 靜態站（`txf-site`）：`data/*.json` 有變更時 `update-quote.yml` 會自動 `build_site.py` + `deploy_site.py`；`index.html` 改動也會在下一次資料變更時一併帶上去，要立即生效可跑 `workflow_dispatch` 或本機 `python scripts/build_site.py && python scripts/deploy_site.py`  
   - 改 Worker：push 後由 `.github/workflows/deploy-worker.yml` **雲端自動部署**（repo secret `CLOUDFLARE_API_KEY`）；本機可選跑 `scripts/deploy_worker.py`  
   - 遠端常有行情快照：用 cherry-pick 接到最新 `origin/master` 再推，不要跟 `data/*.json` rebase 打架  
   - SSOT：`01_Docs/發佈鐵律.md`  
   - **本機改完 ≠ 線上。右下角版號沒變＝沒吃到。**
2. Yahoo 夜盤時間戳：平日 −1 天、週五夜最多 −2 天。**禁止無限 while 回移**（會把 KEEP_DAYS 舊夜盤疊進今晚，千點刷針）。SSOT：`01_Docs/Yahoo-台指期K線日期慣例.md`
3. 不要把盤中 5 秒全量價 commit 進 GitHub。
4. 根目錄不發明 02–09 業務夾：這是單頁＋Actions，不是 pipeline。
5. **休市日 SSOT**：`data/holidays.json`（`scripts/fetch_holidays.py` 抓證交所「市場開休市日期」產生，Actions 每月自動更新）。颱風假等臨時休市由 `scripts/typhoon_check.py` 寫入 `adhoc` 並併入 `holidays`（月更會保留）。前端（`loadHolidays`）、GitHub Actions（`scripts/holiday_guard.py`）、Worker（`ensureHolidays`）**都讀同一份，不得各自硬編**。假日只檔日盤與當晚夜盤；**凌晨 00:00–05:09 仍屬前一營業日夜盤，不可檔**。
6. **雲端自足（硬）**：本站一切**運行**必須在雲端（GitHub Actions／Cloudflare Worker／Workers Static Assets）自力完成，**嚴禁**任何環節依賴本機（本機 daemon、本機 PG、本機 relay、絕對路徑）。已除役者見 `98_Archive/_local_deprecated/`，不得復用；Worker **部署**亦走 `deploy-worker.yml`。動手前先自問：**「老公電腦關機，這條還跑得動嗎？」**

### Testing Requirements

- 線上驗：右下角 VER、夜盤走勢對玩股網（15:00 起單一路徑，一小時約 60 根 1 分 K）
- Worker：`https://wtx.blok.trading/?kind=px1m`

### Common Patterns

- 前端 `gh(path)` 相對路徑吃本站的 `data/`（現為 Workers Static Assets 提供）
- `LIVE = "https://wtx.blok.trading/"`
- `session` 日／夜；`daySpan` 0＝當日走勢

## Dependencies

### External

- 期交所 MIS `getQuoteList`／SockJS 推流（`CID=TXF`；報價＋完整五檔）
- 富邦 DJ `CZMB.djbcd`（`FITXN` 大台日盤 1 分 K）
- MoneyDJ（`FITX` 日／週 K）
- 奇摩 `StockServices.stockList`（僅相關商品 `WCDF&`／`WCCF&`）
- Cloudflare Worker + D1 `txf-imb`
- klinecharts CDN

<!-- MANUAL: 2026-09-01 夜盤刷針＝無限回移疊歷史；修在 2.13 -->
<!-- MANUAL: 2026-09-11 日盤收盤後五檔被奇摩退化書清空；收盤至夜盤轉換（13:46–14:57）凍結真書，修在 2.24/2.25 -->
<!-- MANUAL: 2026-10-29 五檔常壞：MIS 推流是增量 frame，前端整包覆蓋又丟掉無成交價的 frame，五檔被洗掉；判定用「買一必夾成交±5 點」在夜盤誤殺。修在 3.32（累積合併＋pxTs/bookTs 分開＋新鮮書只擋交叉＋90 秒推流看門狗） -->

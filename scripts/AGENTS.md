<!-- Parent: ../AGENTS.md -->
<!-- Generated: 2026-09-01 | Updated: 2026-09-01 -->

# scripts

## Purpose

GitHub Actions／本機抓行情與部署 Worker。台北時段閘門在 `when.py`。

## Key Files

| File | Description |
|------|-------------|
| `when.py` | 台北閘門。本機／`workflow_dispatch` 對報價預設放行；法人／微台看 `day_chips_ready`。真強制只有 `TXF_FORCE=1` |
| `fetch_quote.py` | 奇摩報價＋五檔 → `data/snapshot.json`（開高低勿用奇摩混盤） |
| `fetch_minute.py` | 1/5/15 分；`fix_future_ts` **最多 −2 天** + `collapse_minute` |
| `fetch_kline.py` | MoneyDJ 日／週／月 K |
| `fetch_uncovered.py` | 法人未平倉 → `uncovered.json` |
| `fetch_tmf_retail.py` | 微型臺指散戶多空 |
| `fetch_imb.py` | Worker D1 內外盤差 → `imb-*.json` |
| `poll_live_daemon.py` | 本機每 5 秒 `?kind=poll` |
| `deploy_worker.py` | 上傳 `yahoo-quote.js`、綁 D1／自訂網域／cron |

## For AI Agents

- 改 `fetch_minute.py` 回移必須與前端／Worker 對齊（最多 2 天）
- 部署：`& R:\PythonProgram\Python312\python.exe E:\_Project\FuturesHTML\scripts\deploy_worker.py`（高風險：覆蓋正式 Worker／D1／cron，不要「順便驗證」）
- CF key：`E:\_PluginTools\Memory\secrets\LLM_API_KEY.MD`
- 奇摩只用 `tw.stock.yahoo.com/_td-stock/api/resource/...`，禁 `query1.finance.yahoo.com`；snapshot OHLC 禁寫奇摩混盤開高低
- `fetch_uncovered.py` ~1800 行，不要憑檔頭重寫 main；散戶 `-(外資+投信+自營)`；大台等值 `TX+MTX/4+TMF/20`
- 法人已定案後不要套 `forced()` 重抓
- `poll_live_daemon.py` 自寫 `in_session`，不要改去 import when（窗差是刻意的）
- `fetch_imb.py` 註解會騙人，非週日幾乎都會抓
- 月 K 依結算日切段，斷鏈改 `taifex_settlement_dates.json`，不要放寬 STALE

<!-- MANUAL: scout 2026-09-01 -->

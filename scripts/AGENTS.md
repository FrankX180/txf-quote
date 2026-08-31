<!-- Parent: ../AGENTS.md -->
<!-- Generated: 2026-09-01 | Updated: 2026-09-01 -->

# scripts

## Purpose

GitHub Actions／本機抓行情與部署 Worker。台北時段閘門在 `when.py`。

## Key Files

| File | Description |
|------|-------------|
| `when.py` | 盤中／盤後閘門；`workflow_dispatch` 勿當全開 |
| `fetch_quote.py` | 奇摩報價＋五檔 → `data/snapshot.json`（開高低勿用奇摩混盤） |
| `fetch_minute.py` | 1/5/15 分；`fix_future_ts` **最多 −2 天** + `collapse_minute` |
| `fetch_kline.py` | MoneyDJ 日／週／月 K |
| `fetch_uncovered.py` | 法人未平倉 → `uncovered.json` |
| `fetch_tmf_retail.py` | 微型臺指散戶多空 |
| `fetch_imb.py` | Worker D1 內外盤差 → `imb-*.json` |
| `poll_live_daemon.py` | 本機每 5 秒 `?kind=poll` |
| `deploy_worker.py` | 上傳 `yahoo-quote.js`、綁 D1／自訂網域／cron |

## For AI Agents

- 改 `fetch_minute.py` 的回移邏輯必須與前端 `fixYahooFutureTs`、Worker `barsFromChartJson` 對齊（最多 2 天）
- 部署：`& R:\PythonProgram\Python312\python.exe E:\_Project\FuturesHTML\scripts\deploy_worker.py`
- CF key 在 `E:\_PluginTools\Memory\secrets\LLM_API_KEY.MD`

<!-- MANUAL: -->

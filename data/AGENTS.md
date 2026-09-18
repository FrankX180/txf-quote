<!-- Parent: ../AGENTS.md -->
<!-- Generated: 2026-09-01 | Updated: 2026-09-01 -->

# data

## Purpose

進 Git 的靜態 JSON，Pages 相對路徑讀取。由 Actions `update-quote.yml` commit，**不要手改當 SSOT**。

## Key Files

| File | Description |
|------|-------------|
| `snapshot.json` | 日／夜報價＋五檔 |
| `kline-minute.json` | night/day 1m 5m 15m |
| `kline-daily.json` | 日週月 K |
| `uncovered.json` | 法人未平倉歷史 |
| `vix.json` | **VIXTWN 日收盤 SSOT**（由 `fetch_vix.py` 每日累積；長歷史靠本檔累积，官網月檔僅近月。不讀任何外部專案） |
| `tmf_retail.json` | 微型臺指散戶 |
| `imb-YYYYMMDD.json` / `imb-latest.json` | 內外盤差 |
| `history-day.json` / `history-night.json` | 收盤歸檔，非盤中主力 |
| `taifex_settlement_dates.json` | 結算日 |

## For AI Agents

- rebase／push 常跟 bot 的「行情快照」撞車：前端 commit 用 cherry-pick 接到最新 origin/master
- `kline-minute.json` 若同一 `date` 一小時出現遠大於 60 根 → 回移把舊夜盤疊進來了
- `vix.json` 是本站 VIX 唯一來源（`fetch_vix.py` 一日一次累積）；官網月檔只保留近 ~3 個月，別把舊資料洗掉

<!-- MANUAL: -->

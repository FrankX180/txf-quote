<!-- Parent: ../AGENTS.md -->
<!-- Generated: 2026-09-01 | Updated: 2026-09-01 -->

# relay

## Purpose

本機 TAIFEX 臺指期報價中繼（除錯／備援）。公開 Pages 主路徑打 Worker，不依賴本夾。

綁 `127.0.0.1:8720`。POST 期交所 `getQuoteList`（夜盤 MarketType `1`、日盤 `0`），挑 `TXF*-M` / `TXF*-F` 有成交價的合約，欄位碼對成 `CLastPrice`／五檔等，經 SockJS/WebSocket 推本機客戶端。狀態分 night/day，歷史緩衝 KEEP=900。註解裡的對外域名曾寫 `txf.19850926.xyz`（現況即時站是 Worker `wtx.19850926.xyz`）。

## Key Files

| File | Description |
|------|-------------|
| `server.py` | 中繼本體 |
| `start_relay.cmd` | 以 Python312 從專案根啟動 `relay\server.py`（ASCII-only） |

## For AI Agents

- 新 bind port 前查 `E:\_PluginTools\LOCAL_PORTS.md` 並登記；8720 已占用則不要改到 pywebview 42001
- 不要把 relay 當生產發佈路徑

<!-- MANUAL: scout 2026-09-01 -->

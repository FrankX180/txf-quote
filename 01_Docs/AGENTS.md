<!-- Parent: ../AGENTS.md -->
<!-- Generated: 2026-09-01 | Updated: 2026-09-01 -->

# 01_Docs

## Purpose

本站規格 SSOT。改時間軸／發佈流程前先讀這裡，不要只憑記憶改 `index.html`。

## Key Files

| File | Description |
|------|-------------|
| `發佈鐵律.md` | 改完必升 VER＋push master；Worker 另 deploy |
| `Yahoo-台指期K線日期慣例.md` | 夜盤 Yahoo 標次交易日；回移最多 2 天 |
| `台指期看盤頁-說明書.md` | 架構、資料流、部署 |

## For AI Agents

- 發佈／版號爭議以 `發佈鐵律.md` 為準
- 夜盤日期／假未來 K 以 `Yahoo-台指期K線日期慣例.md` 為準
- 說明書已從根目錄搬入本夾（good-folder）

<!-- MANUAL: 三層時間戳必須齊：fetch_minute.fix_future_ts、worker.barsFromChartJson、index.fixYahooFutureTs；回移最多 2 天。 -->

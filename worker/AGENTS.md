<!-- Parent: ../AGENTS.md -->
<!-- Generated: 2026-09-01 | Updated: 2026-09-01 -->

# worker

## Purpose

Cloudflare Worker `txf-yahoo`：代打奇摩、寫 D1（內外盤差、1 分價）、回 `px1m`／`1m`／報價。網域 `wtx.19850926.xyz`。

## Key Files

| File | Description |
|------|-------------|
| `yahoo-quote.js` | 全部路由與 D1 |
| `wrangler.toml` | 本機 wrangler 設定（正式部署走 `deploy_worker.py`） |

## For AI Agents

- **push JS ≠ 上線**，必須 `deploy_worker.py`
- `barsFromChartJson`：Yahoo 夜盤回移 **最多 2 天**；`sessionOf` 對齊前端
- `kind=1m` 轉奇摩 chart；`kind=px1m` 讀 D1；`kind=poll` 強制寫入；`kind=heal` 觸發 GH workflow
- 單價 `appendPricePx` 不新建 K 棒，只更新已有 chart 棒

<!-- MANUAL: -->

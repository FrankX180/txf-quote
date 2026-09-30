<!-- Parent: ../AGENTS.md -->
<!-- Generated: 2026-09-01 | Updated: 2026-09-01 -->

# worker

## Purpose

Cloudflare Worker `txf-yahoo`：代打奇摩、寫 D1（內外盤差、1 分價）、回 `px1m`／`1m`／報價。主網域 `wtx.blok.trading`（舊別名 `wtx.19850926.xyz`）。

## Key Files

| File | Description |
|------|-------------|
| `yahoo-quote.js` | 全部路由與 D1 |
| `wrangler.toml` | 參考設定（正式部署走 `deploy_worker.py`；雲端走 `deploy-worker.yml`） |

## For AI Agents

- **push JS ≠ 上線**：由 `.github/workflows/deploy-worker.yml` 雲端自動部署（secret `CLOUDFLARE_API_KEY`）；或本機 `python scripts/deploy_worker.py`
- `barsFromChartJson`：Yahoo 夜盤回移 **最多 2 天**；`sessionOf` 對齊前端
- `kind=1m` 轉奇摩 chart；`kind=px1m` 讀 D1；`kind=poll` 強制寫入（`chart=1` 才回填 1m）；`kind=heal` 觸發 GH workflow；`imb`／`tmf`／`ping`／`stats` 見 scout 表
- 單價 `appendPricePx` 不新建 K 棒，只更新已有 chart 棒
- Cron 每分 `pollAndStore({chart:true})`；本機 15 秒 daemon 已除役（`98_Archive/_local_deprecated/`），不依賴本機常駐
- **先 quote 再 chart**（反過來奇摩常擋第二槍 → px1m 滿、imb 稀疏）
- `sessionOf`：日 08:45–13:45；夜 14:58 或 &lt;05:10；週日／週六 ≥05:10 休

<!-- MANUAL: scout 2026-09-01：回移 while shift&lt;2；≥400 點／15 分跳過；價濾 10000–80000 -->

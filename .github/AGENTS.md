<!-- Parent: ../AGENTS.md -->
<!-- Generated: 2026-09-01 | Updated: 2026-09-01 -->

# .github

## Purpose

排程抓檔。

## Key Files

| File | Description |
|------|-------------|
| `workflows/update-quote.yml` | 每 5 分／push scripts 時跑 fetch_*，只 commit `data/*` |

## For AI Agents

- concurrency `update-quote` cancel-in-progress
- 推前端時遠端常已有新快照 → cherry-pick，不要整包 rebase `data/uncovered.json`

<!-- MANUAL: -->

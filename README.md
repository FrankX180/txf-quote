# 台指期 TXF 看盤小頁

資料來源：奇摩股市（不依賴本機）

- 台指近月 `WTX&`：報價、五檔、1 分／5 分／15 分
- 台積電期近月 `WCDF&`、聯電期近月 `WCCF&`：報價

即時報價：Cloudflare Worker `https://wtx.19850926.xyz/` 代打奇摩，頁面每 5 秒更新。電腦關機不影響。
K 線仍由 GitHub Actions 每 5 分鐘寫入 `data/kline-minute.json`。

# 台指期 小台 MXF 看盤小頁

說明書：[`01_Docs/台指期看盤頁-說明書.md`](01_Docs/台指期看盤頁-說明書.md)

資料來源（全雲端，不依賴本機）

- **報價／五檔**：期交所 MIS `getQuoteList`（`CID=MXF`，近月 `MXF*-F` 日盤／`MXF*-M` 夜盤）＋ SockJS 推流完整五檔
- **1 分 K 日盤**：富邦 DJ `CZMB.djbcd?a=FIMTXN`（真小台）
- **1 分 K 夜盤**：Yahoo `WTX&`（大台）— 小台無夜盤分時源，同一標的價差 1~3 點
- **5／15 分**：由 1 分 K 自行聚合
- **日／週／月 K**：MoneyDJ `FITX`（大台；`FIMTX` 實測為別名，payload 已標 `proxyNote`）
- 相關商品：台積電期 `WCDF&`、聯電期 `WCCF&`

> 為何不是奇摩：奇摩股市**沒有小台**（`WTXM&` 停更於 2026-06-23、`MXF&`/`MTX&` 皆 404）。
> 2026-10-29 由大台 TXF 全面改小台 MXF，改動前備份在 `Backup/20261029-before-mxf/`。

即時報價：Cloudflare Worker `https://wtx.blok.trading/`（舊別名 `https://wtx.19850926.xyz/`），頁面每 15 秒更新。電腦關機不影響。
即時報價與 1 分 K 走上方 Worker。靜態 JSON 仍由 GitHub Actions 每 5 分鐘寫入 `data/`。

前端改完必升頁腳版本並 `git push origin master`（見 `01_Docs/發佈鐵律.md`）。本機存檔不會更新 Pages。

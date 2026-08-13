# 台指期 TXF 看盤小頁

資料來源：奇摩股市期貨 `WTX&`（台指近月）

- 報價＋五檔：`StockServices.stockList`
- 當日 1 分／多日 5／15 分：`StockServices.chart`、`FinanceChartService.ApacLibraCharts`

GitHub Actions 每 5 分鐘寫入 `data/*.json`。頁面只讀這些靜態檔，不連本機閘道。

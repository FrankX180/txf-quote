# 台指期 TXF 看盤小頁

資料來源：臺灣期貨交易所 MIS 公開行情  
`POST https://mis.taifex.com.tw/futures/api/getQuoteList`

GitHub Pages 瀏覽器**不能**直連官方 API（回應沒有 CORS）。  
本頁讀 `data/snapshot.json`，由 GitHub Actions 約每 5 分鐘抓一次。

官方列表只有買一／賣一，沒有完整五檔。

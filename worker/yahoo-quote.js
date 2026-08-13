const YAHOO =
  "https://tw.stock.yahoo.com/_td-stock/api/resource/" +
  "StockServices.stockList;symbols=WTX%26,WCDF%26,WCCF%26";
const CHART1M =
  "https://tw.stock.yahoo.com/_td-stock/api/resource/" +
  "StockServices.chart;symbol=WTX%26;period=1m;range=1d";

const CORS = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Methods": "GET, OPTIONS",
  "Access-Control-Allow-Headers": "*",
};

export default {
  async fetch(request) {
    if (request.method === "OPTIONS") {
      return new Response(null, { status: 204, headers: CORS });
    }
    const kind = new URL(request.url).searchParams.get("kind");
    const target = kind === "1m" ? CHART1M : YAHOO;
    const r = await fetch(target, {
      headers: {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0.0.0",
        Referer: "https://tw.stock.yahoo.com/",
      },
    });
    const body = await r.text();
    return new Response(body, {
      status: r.status,
      headers: {
        "content-type": "application/json; charset=utf-8",
        "cache-control": "no-store",
        ...CORS,
      },
    });
  },
};

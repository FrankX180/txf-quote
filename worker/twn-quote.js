// 富台指（SGX FTSE Taiwan Futures）走勢與即時報價模組
// 來源：GAS 基準倍數/USD-TWD/即時現價 + HiStock
// 支援邊緣快取 5 秒，避免高頻請求對上游造成負擔

export async function handleTwnQuote(request, env, ctx) {
  const url = new URL(request.url);
  const cacheKey = new Request(url.origin + "/?kind=twn", request);
  const cache = caches.default;
  let cached = await cache.match(cacheKey);
  if (cached) return cached;

  try {
    // 1. 抓取即時 USD/TWD 與富台基準倍數及即時價 (Google Apps Script 超高可用，平均 300ms)
    let gasData = null;
    try {
      const gasUrl = "https://script.google.com/macros/s/AKfycbyX-wGspxXlgk9pcYGKH6PD1AElWRny-4ZP1XFx1MCSpQwz4hexET3x7AS1034ea2kAzQ/exec";
      const gasResp = await fetch(gasUrl, {
        headers: { "User-Agent": "Mozilla/5.0" },
        cf: { cacheTtl: 5 }
      });
      if (gasResp.ok) gasData = await gasResp.json().catch(() => null);
    } catch (_) {}

    // 2. 抓取 HiStock 分時序列 (若超時則平滑降級)
    let points = [];
    let latestPrice = (gasData && gasData.rtxNow) || 0;
    let latestTs = Date.now();

    try {
      const histockUrl = "https://histock.tw/stock/module/stockdata.aspx?no=TWN";
      const hResp = await fetch(histockUrl, {
        headers: {
          "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
          "Referer": "https://histock.tw/index-tw/TWN",
        },
        cf: { cacheTtl: 5 }
      });
      if (hResp.ok) {
        const hJson = await hResp.json().catch(() => null);
        let rawStr = (hJson && hJson.data) || "[]";
        rawStr = rawStr.replace(/,\s*\]\s*$/, "]");
        points = JSON.parse(rawStr);
        if (points && points.length > 0) {
          const last = points[points.length - 1];
          latestTs = last[0];
          latestPrice = last[1];
        }
      }
    } catch (_) {}

    const payload = {
      ok: true,
      symbol: "TWN",
      name: "富台指 (SGX FTSE Taiwan)",
      latestPrice: latestPrice,
      latestTs: latestTs,
      points: points,
      gas: gasData || null,
      updatedAt: new Date().toISOString(),
    };

    const out = new Response(JSON.stringify(payload), {
      status: 200,
      headers: {
        "Content-Type": "application/json; charset=utf-8",
        "Access-Control-Allow-Origin": "*",
        "Cache-Control": "public, max-age=0, s-maxage=5",
        "CDN-Cache-Control": "public, max-age=5",
        "Cloudflare-CDN-Cache-Control": "public, max-age=5",
      }
    });

    ctx.waitUntil(cache.put(cacheKey, out.clone()));
    return out;
  } catch (err) {
    return new Response(JSON.stringify({ ok: false, error: String(err && err.message || err) }), {
      status: 500,
      headers: {
        "Content-Type": "application/json; charset=utf-8",
        "Access-Control-Allow-Origin": "*",
        "Cache-Control": "no-store",
      }
    });
  }
}

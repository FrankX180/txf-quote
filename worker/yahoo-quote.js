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

function jsonResp(obj, status, extra) {
  return new Response(JSON.stringify(obj), {
    status: status || 200,
    headers: {
      "content-type": "application/json; charset=utf-8",
      "Cache-Control": "public, max-age=0",
      "CDN-Cache-Control": "public, max-age=5",
      "Cloudflare-CDN-Cache-Control": "public, max-age=5",
      ...CORS,
      ...(extra || {}),
    },
  });
}

function rawNum(v) {
  if (v == null || v === "") return null;
  if (typeof v === "object" && v.raw != null) v = v.raw;
  const n = Number(v);
  return Number.isFinite(n) ? n : null;
}

function twParts(ms) {
  const fmt = new Intl.DateTimeFormat("en-US", {
    timeZone: "Asia/Taipei",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hourCycle: "h23",
  });
  const parts = {};
  for (const p of fmt.formatToParts(new Date(ms))) {
    if (p.type !== "literal") parts[p.type] = p.value;
  }
  return {
    y: parts.year,
    mo: parts.month,
    d: parts.day,
    h: Number(parts.hour),
    mi: Number(parts.minute),
    s: Number(parts.second),
    hm: Number(parts.hour) * 100 + Number(parts.minute),
  };
}

function sessionOf(ms) {
  const p = twParts(ms);
  const hm = p.hm;
  if (hm >= 845 && hm <= 1345) return "day";
  if (hm >= 1455 || hm < 510) return "night";
  return null;
}

/** 夜盤跨午夜：00:00-05:59 歸前一曆日交易鍵 */
function tradingDayKey(ms) {
  const p = twParts(ms);
  if (p.hm >= 600) return p.y + p.mo + p.d;
  const q = twParts(ms - 24 * 3600 * 1000);
  return q.y + q.mo + q.d;
}

function minuteSlot(ms) {
  return Math.floor(ms / 60000) * 60000;
}

function extractWtx(rows) {
  if (!Array.isArray(rows) || !rows.length) return null;
  const w = rows.find((x) => x && x.symbol === "WTX&") || rows[0];
  if (!w) return null;
  let inn = rawNum(w.inMarket);
  let outv = rawNum(w.outMarket);
  if (inn != null && Math.abs(inn) < 1e6) inn = Math.round(inn * 1000);
  if (outv != null && Math.abs(outv) < 1e6) outv = Math.round(outv * 1000);
  return { inn, outv, px: rawNum(w.price) };
}

async function ensureSchema(db) {
  await db
    .prepare(
      "CREATE TABLE IF NOT EXISTS imb (" +
        "day_key TEXT NOT NULL," +
        "session TEXT NOT NULL," +
        "t INTEGER NOT NULL," +
        "d REAL NOT NULL," +
        "inn REAL," +
        "outv REAL," +
        "ts INTEGER," +
        "PRIMARY KEY (day_key, session, t)" +
        ")"
    )
    .run();
}

async function appendImb(env, rows, nowMs) {
  if (!env.IMB_DB) return;
  const sess = sessionOf(nowMs);
  if (!sess) return;
  const w = extractWtx(rows);
  if (!w || w.inn == null || w.outv == null) return;
  const dayKey = tradingDayKey(nowMs);
  const slot = minuteSlot(nowMs);
  const d = w.outv - w.inn;
  await ensureSchema(env.IMB_DB);
  await env.IMB_DB.prepare(
    "INSERT INTO imb (day_key, session, t, d, inn, outv, ts) VALUES (?, ?, ?, ?, ?, ?, ?) " +
      "ON CONFLICT(day_key, session, t) DO UPDATE SET d=excluded.d, inn=excluded.inn, outv=excluded.outv, ts=excluded.ts"
  )
    .bind(dayKey, sess, slot, d, w.inn, w.outv, nowMs)
    .run();
}

async function loadPack(env, dayKey) {
  const empty = { dayKey, day: [], night: [], updatedAt: null };
  if (!env.IMB_DB) return empty;
  try {
    await ensureSchema(env.IMB_DB);
    const { results } = await env.IMB_DB.prepare(
      "SELECT session, t, d, inn, outv, ts FROM imb WHERE day_key = ? ORDER BY t ASC"
    )
      .bind(dayKey)
      .all();
    const day = [];
    const night = [];
    let updatedAt = null;
    for (const row of results || []) {
      const bar = {
        t: row.t,
        d: row.d,
        in: row.inn,
        out: row.outv,
        ts: row.ts,
      };
      if (row.session === "day") day.push(bar);
      else if (row.session === "night") night.push(bar);
      if (row.ts && (!updatedAt || row.ts > updatedAt)) updatedAt = row.ts;
    }
    return {
      dayKey,
      day,
      night,
      updatedAt: updatedAt ? new Date(updatedAt).toISOString() : null,
    };
  } catch (e) {
    return empty;
  }
}

export default {
  async fetch(request, env, ctx) {
    if (request.method === "OPTIONS") {
      return new Response(null, {
        status: 204,
        headers: { ...CORS, "Cache-Control": "no-store" },
      });
    }

    const url = new URL(request.url);
    const kind = url.searchParams.get("kind");

    if (kind === "imb") {
      const now = Date.now();
      const day =
        (url.searchParams.get("day") || "").replace(/-/g, "") ||
        tradingDayKey(now);
      const pack = await loadPack(env, day);
      return jsonResp(
        {
          dayKey: day,
          day: pack.day,
          night: pack.night,
          updatedAt: pack.updatedAt,
          source: "d1",
        },
        200,
        {
          "Cache-Control": "public, max-age=0",
          "CDN-Cache-Control": "public, max-age=3",
          "Cloudflare-CDN-Cache-Control": "public, max-age=3",
        }
      );
    }

    const target = kind === "1m" ? CHART1M : YAHOO;
    const r = await fetch(target, {
      headers: {
        "User-Agent":
          "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0.0.0",
        Referer: "https://tw.stock.yahoo.com/",
      },
    });
    const body = await r.text();

    if (kind !== "1m" && r.ok && env.IMB_DB) {
      const nowMs = Date.now();
      ctx.waitUntil(
        (async () => {
          try {
            const rows = JSON.parse(body);
            await appendImb(env, rows, nowMs);
          } catch (e) {
            /* ignore */
          }
        })()
      );
    }

    return new Response(body, {
      status: r.status,
      headers: {
        "content-type": "application/json; charset=utf-8",
        "Cache-Control": "public, max-age=0",
        "CDN-Cache-Control": "public, max-age=5",
        "Cloudflare-CDN-Cache-Control": "public, max-age=5",
        ...CORS,
      },
    });
  },
};

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
  // 週六 05:10 後、週日：不寫
  const wdFmt = new Intl.DateTimeFormat("en-US", {
    timeZone: "Asia/Taipei",
    weekday: "short",
  });
  const wd = wdFmt.format(new Date(ms)); // Sun Mon ...
  if (wd === "Sun") return null;
  if (wd === "Sat" && hm >= 510) return null;
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
  await db
    .prepare(
      "CREATE TABLE IF NOT EXISTS presence (" +
        "sid TEXT PRIMARY KEY," +
        "last_seen INTEGER NOT NULL," +
        "day_key TEXT" +
        ")"
    )
    .run();
  await db
    .prepare(
      "CREATE TABLE IF NOT EXISTS traffic_day (" +
        "day_key TEXT PRIMARY KEY," +
        "pv INTEGER NOT NULL DEFAULT 0," +
        "uv INTEGER NOT NULL DEFAULT 0," +
        "peak INTEGER NOT NULL DEFAULT 0" +
        ")"
    )
    .run();
  await db
    .prepare(
      "CREATE TABLE IF NOT EXISTS traffic_sid (" +
        "day_key TEXT NOT NULL," +
        "sid TEXT NOT NULL," +
        "PRIMARY KEY (day_key, sid)" +
        ")"
    )
    .run();
}

const ONLINE_MS = 90 * 1000;

async function handlePing(env, url) {
  if (!env.IMB_DB) return { ok: false, reason: "no-db" };
  const sid = (url.searchParams.get("sid") || "").slice(0, 64);
  if (!sid || !/^[a-zA-Z0-9_-]{8,64}$/.test(sid)) {
    return { ok: false, reason: "bad-sid" };
  }
  const hit = url.searchParams.get("hit") === "1";
  const now = Date.now();
  const dayKey = tradingDayKey(now);
  await ensureSchema(env.IMB_DB);
  await env.IMB_DB.prepare(
    "INSERT INTO presence (sid, last_seen, day_key) VALUES (?, ?, ?) " +
      "ON CONFLICT(sid) DO UPDATE SET last_seen=excluded.last_seen, day_key=excluded.day_key"
  )
    .bind(sid, now, dayKey)
    .run();
  // 清超過 1 小時沒心跳
  await env.IMB_DB.prepare("DELETE FROM presence WHERE last_seen < ?")
    .bind(now - 3600 * 1000)
    .run();

  if (hit) {
    await env.IMB_DB.prepare(
      "INSERT INTO traffic_day (day_key, pv, uv, peak) VALUES (?, 0, 0, 0) " +
        "ON CONFLICT(day_key) DO NOTHING"
    )
      .bind(dayKey)
      .run();
    await env.IMB_DB.prepare(
      "UPDATE traffic_day SET pv = pv + 1 WHERE day_key = ?"
    )
      .bind(dayKey)
      .run();
    const existed = await env.IMB_DB.prepare(
      "SELECT 1 AS x FROM traffic_sid WHERE day_key = ? AND sid = ?"
    )
      .bind(dayKey, sid)
      .first();
    if (!existed) {
      await env.IMB_DB.prepare(
        "INSERT INTO traffic_sid (day_key, sid) VALUES (?, ?)"
      )
        .bind(dayKey, sid)
        .run();
      await env.IMB_DB.prepare(
        "UPDATE traffic_day SET uv = uv + 1 WHERE day_key = ?"
      )
        .bind(dayKey)
        .run();
    }
  }

  const onlineRow = await env.IMB_DB.prepare(
    "SELECT COUNT(*) AS n FROM presence WHERE last_seen >= ?"
  )
    .bind(now - ONLINE_MS)
    .first();
  const online = Number(onlineRow && onlineRow.n) || 0;
  await env.IMB_DB.prepare(
    "INSERT INTO traffic_day (day_key, pv, uv, peak) VALUES (?, 0, 0, ?) " +
      "ON CONFLICT(day_key) DO UPDATE SET peak = MAX(traffic_day.peak, excluded.peak)"
  )
    .bind(dayKey, online)
    .run();

  return loadStats(env, dayKey, now);
}

async function loadStats(env, dayKey, nowMs) {
  const now = nowMs || Date.now();
  const dk = dayKey || tradingDayKey(now);
  const monthKey = dk.slice(0, 6);
  const empty = {
    ok: false,
    dayKey: dk,
    monthKey,
    online: 0,
    pv: 0,
    uv: 0,
    peak: 0,
    monthPv: 0,
    monthUv: 0,
    totalPv: 0,
    totalUv: 0,
    windowSec: ONLINE_MS / 1000,
  };
  if (!env.IMB_DB) return empty;
  await ensureSchema(env.IMB_DB);
  const onlineRow = await env.IMB_DB.prepare(
    "SELECT COUNT(*) AS n FROM presence WHERE last_seen >= ?"
  )
    .bind(now - ONLINE_MS)
    .first();
  const dayRow = await env.IMB_DB.prepare(
    "SELECT pv, uv, peak FROM traffic_day WHERE day_key = ?"
  )
    .bind(dk)
    .first();
  const monthRow = await env.IMB_DB.prepare(
    "SELECT COALESCE(SUM(pv), 0) AS pv FROM traffic_day WHERE day_key LIKE ?"
  )
    .bind(monthKey + "%")
    .first();
  const monthUvRow = await env.IMB_DB.prepare(
    "SELECT COUNT(DISTINCT sid) AS n FROM traffic_sid WHERE day_key LIKE ?"
  )
    .bind(monthKey + "%")
    .first();
  const totalRow = await env.IMB_DB.prepare(
    "SELECT COALESCE(SUM(pv), 0) AS pv FROM traffic_day"
  ).first();
  const totalUvRow = await env.IMB_DB.prepare(
    "SELECT COUNT(DISTINCT sid) AS n FROM traffic_sid"
  ).first();
  return {
    ok: true,
    dayKey: dk,
    monthKey,
    online: Number(onlineRow && onlineRow.n) || 0,
    pv: Number(dayRow && dayRow.pv) || 0,
    uv: Number(dayRow && dayRow.uv) || 0,
    peak: Number(dayRow && dayRow.peak) || 0,
    monthPv: Number(monthRow && monthRow.pv) || 0,
    monthUv: Number(monthUvRow && monthUvRow.n) || 0,
    totalPv: Number(totalRow && totalRow.pv) || 0,
    totalUv: Number(totalUvRow && totalUvRow.n) || 0,
    windowSec: ONLINE_MS / 1000,
  };
}

async function appendImb(env, rows, nowMs) {
  if (!env.IMB_DB) return { ok: false, reason: "no-db" };
  const sess = sessionOf(nowMs);
  if (!sess) return { ok: false, reason: "closed" };
  const w = extractWtx(rows);
  if (!w || w.inn == null || w.outv == null) return { ok: false, reason: "no-imb" };
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
  return { ok: true, dayKey, sess, d, slot };
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

async function fetchYahooQuote() {
  const r = await fetch(YAHOO, {
    headers: {
      "User-Agent":
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0.0.0",
      Referer: "https://tw.stock.yahoo.com/",
    },
  });
  const body = await r.text();
  return { ok: r.ok, status: r.status, body };
}

/** 無人看盤也寫：Cron 觸發（CF 最短 1 分） */
async function pollAndStore(env) {
  const nowMs = Date.now();
  if (!sessionOf(nowMs)) return { skipped: true, reason: "closed" };
  const { ok, body } = await fetchYahooQuote();
  if (!ok) return { skipped: true, reason: "yahoo-fail" };
  const rows = JSON.parse(body);
  return await appendImb(env, rows, nowMs);
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

    if (kind === "ping") {
      try {
        const st = await handlePing(env, url);
        return jsonResp(st, 200, {
          "Cache-Control": "no-store",
          "CDN-Cache-Control": "no-store",
          "Cloudflare-CDN-Cache-Control": "no-store",
        });
      } catch (e) {
        return jsonResp({ ok: false, error: String(e && e.message || e) }, 500, {
          "Cache-Control": "no-store",
        });
      }
    }

    if (kind === "stats") {
      try {
        const st = await loadStats(env);
        return jsonResp(st, 200, {
          "Cache-Control": "public, max-age=0",
          "CDN-Cache-Control": "public, max-age=5",
          "Cloudflare-CDN-Cache-Control": "public, max-age=5",
        });
      } catch (e) {
        return jsonResp({ ok: false, error: String(e && e.message || e) }, 500, {
          "Cache-Control": "no-store",
        });
      }
    }

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

    // 手動／外部守護：強制打奇摩並寫 D1（不走 5 秒 CDN 快取語意）
    if (kind === "poll") {
      const res = await pollAndStore(env);
      return jsonResp({ ok: true, ...res, at: new Date().toISOString() }, 200, {
        "Cache-Control": "no-store",
        "CDN-Cache-Control": "no-store",
        "Cloudflare-CDN-Cache-Control": "no-store",
      });
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

  async scheduled(event, env, ctx) {
    ctx.waitUntil(pollAndStore(env));
  },
};

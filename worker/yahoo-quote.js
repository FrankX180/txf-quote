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
  await db
    .prepare(
      "CREATE TABLE IF NOT EXISTS price_1m (" +
        "day_key TEXT NOT NULL," +
        "session TEXT NOT NULL," +
        "t INTEGER NOT NULL," +
        "o REAL," +
        "h REAL," +
        "l REAL," +
        "c REAL NOT NULL," +
        "v INTEGER DEFAULT 0," +
        "ts INTEGER," +
        "source TEXT," +
        "PRIMARY KEY (day_key, session, t)" +
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

async function appendPricePx(env, px, nowMs, source) {
  if (!env.IMB_DB || px == null || !Number.isFinite(px)) return { ok: false, reason: "no-px" };
  const sess = sessionOf(nowMs);
  if (!sess) return { ok: false, reason: "closed" };
  const dayKey = tradingDayKey(nowMs);
  const slot = minuteSlot(nowMs);
  await ensureSchema(env.IMB_DB);
  const prev = await env.IMB_DB.prepare(
    "SELECT o, h, l, c, v FROM price_1m WHERE day_key=? AND session=? AND t=?"
  )
    .bind(dayKey, sess, slot)
    .first();
  let o = px;
  let h = px;
  let l = px;
  let c = px;
  let v = 0;
  if (prev && prev.c != null) {
    o = prev.o != null ? prev.o : px;
    h = Math.max(prev.h != null ? prev.h : px, px);
    l = Math.min(prev.l != null ? prev.l : px, px);
    c = px;
    v = Number(prev.v) || 0;
  }
  await env.IMB_DB.prepare(
    "INSERT INTO price_1m (day_key, session, t, o, h, l, c, v, ts, source) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?) " +
      "ON CONFLICT(day_key, session, t) DO UPDATE SET " +
      "o=excluded.o, h=excluded.h, l=excluded.l, c=excluded.c, v=excluded.v, ts=excluded.ts, source=excluded.source"
  )
    .bind(dayKey, sess, slot, o, h, l, c, v, nowMs, source || "quote")
    .run();
  return { ok: true, dayKey, sess, slot, c: px };
}


async function upsertPriceBars(env, bars) {
  if (!env.IMB_DB || !bars || !bars.length) return 0;
  await ensureSchema(env.IMB_DB);
  const sql =
    "INSERT INTO price_1m (day_key, session, t, o, h, l, c, v, ts, source) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?) " +
    "ON CONFLICT(day_key, session, t) DO UPDATE SET " +
    "o=COALESCE(excluded.o, price_1m.o), " +
    "h=CASE WHEN excluded.h IS NOT NULL AND (price_1m.h IS NULL OR excluded.h > price_1m.h) THEN excluded.h ELSE price_1m.h END, " +
    "l=CASE WHEN excluded.l IS NOT NULL AND (price_1m.l IS NULL OR excluded.l < price_1m.l) THEN excluded.l ELSE price_1m.l END, " +
    "c=excluded.c, " +
    "v=CASE WHEN excluded.v IS NOT NULL AND excluded.v >= COALESCE(price_1m.v, 0) THEN excluded.v ELSE price_1m.v END, " +
    "ts=excluded.ts, source=excluded.source";
  const stmts = [];
  for (const bar of bars) {
    if (!bar || bar.c == null) continue;
    stmts.push(
      env.IMB_DB.prepare(sql).bind(
        bar.dayKey,
        bar.session,
        bar.t,
        bar.o,
        bar.h,
        bar.l,
        bar.c,
        bar.v || 0,
        bar.ts,
        bar.source || "chart"
      )
    );
  }
  // D1 batch 上限約 1000；分塊寫入
  for (let i = 0; i < stmts.length; i += 200) {
    await env.IMB_DB.batch(stmts.slice(i, i + 200));
  }
  return stmts.length;
}

function barsFromChartJson(chart) {
  const tsList = (chart && chart.timestamp) || [];
  const q0 = (((chart && chart.indicators) || {}).quote || [{}])[0] || {};
  const opens = q0.open || [];
  const highs = q0.high || [];
  const lows = q0.low || [];
  const closes = q0.close || [];
  const vols = q0.volume || [];
  const out = [];
  for (let i = 0; i < tsList.length; i++) {
    const c = closes[i];
    if (c == null) continue;
    const tsSec = +tsList[i];
    const ts = tsSec < 1e12 ? tsSec * 1000 : tsSec;
    const sess = sessionOf(ts);
    if (!sess) continue;
    const dayKey = tradingDayKey(ts);
    const slot = minuteSlot(ts);
    const o = opens[i] != null ? +opens[i] : +c;
    const h = highs[i] != null ? +highs[i] : +c;
    const l = lows[i] != null ? +lows[i] : +c;
    const v = vols[i] != null ? Math.round(+vols[i] * 1000) : 0;
    out.push({
      dayKey,
      session: sess,
      t: slot,
      o,
      h,
      l,
      c: +c,
      v,
      ts,
      source: "chart",
    });
  }
  return out;
}

async function backfillChart1m(env) {
  if (!env.IMB_DB) return { ok: false, reason: "no-db" };
  const r = await fetch(CHART1M, {
    headers: {
      "User-Agent":
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0.0.0",
      Referer: "https://tw.stock.yahoo.com/",
    },
  });
  if (!r.ok) return { ok: false, reason: "yahoo-chart-fail", status: r.status };
  const chart = await r.json();
  const all = barsFromChartJson(chart);
  const dayKey = tradingDayKey(Date.now());
  const bars = all.filter((b) => b.dayKey === dayKey);
  const n = await upsertPriceBars(env, bars.length ? bars : all);
  return { ok: true, n, dayKey, raw: all.length };
}

async function loadPricePack(env, dayKey) {
  const empty = { dayKey, day: [], night: [], updatedAt: null };
  if (!env.IMB_DB) return empty;
  try {
    await ensureSchema(env.IMB_DB);
    const { results } = await env.IMB_DB.prepare(
      "SELECT session, t, o, h, l, c, v, ts FROM price_1m WHERE day_key = ? ORDER BY t ASC"
    )
      .bind(dayKey)
      .all();
    const day = [];
    const night = [];
    let updatedAt = null;
    for (const row of results || []) {
      const bar = {
        timestamp: row.t,
        open: row.o,
        high: row.h,
        low: row.l,
        close: row.c,
        volume: row.v || 0,
        date: dayKey,
        session: row.session,
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

/** 無人看盤也寫：Cron／poll 觸發（CF 最短 1 分；本機守護可 5 秒） */
async function pollAndStore(env, opts) {
  const nowMs = Date.now();
  if (!sessionOf(nowMs)) return { skipped: true, reason: "closed" };
  const { ok, body } = await fetchYahooQuote();
  if (!ok) return { skipped: true, reason: "yahoo-fail" };
  const rows = JSON.parse(body);
  const imb = await appendImb(env, rows, nowMs);
  const w = extractWtx(rows);
  const px = await appendPricePx(env, w && w.px, nowMs, "quote");
  const out = { imb, px };
  // chart 回填只走 cron（約 1 分一次），避免本機 5 秒 poll 打爆奇摩
  if (opts && opts.chart) {
    try {
      out.chart = await backfillChart1m(env);
    } catch (e) {
      out.chart = { ok: false, reason: String(e && e.message || e) };
    }
  }
  return out;
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

    // 後端權威 1 分價（與 imb 同級；前端優先讀此）
    if (kind === "px1m") {
      const now = Date.now();
      const day =
        (url.searchParams.get("day") || "").replace(/-/g, "") ||
        tradingDayKey(now);
      const pack = await loadPricePack(env, day);
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
      const wantChart = url.searchParams.get("chart") === "1";
      const res = await pollAndStore(env, wantChart ? { chart: true } : undefined);
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

    if (kind === "1m" && r.ok && env.IMB_DB) {
      ctx.waitUntil(
        (async () => {
          try {
            const chart = JSON.parse(body);
            const all = barsFromChartJson(chart);
            const dayKey = tradingDayKey(Date.now());
            const bars = all.filter((b) => b.dayKey === dayKey);
            await upsertPriceBars(env, bars.length ? bars : all);
          } catch (e) {
            /* ignore */
          }
        })()
      );
    }

    if (kind !== "1m" && r.ok && env.IMB_DB) {
      const nowMs = Date.now();
      ctx.waitUntil(
        (async () => {
          try {
            const rows = JSON.parse(body);
            await appendImb(env, rows, nowMs);
            const w = extractWtx(rows);
            await appendPricePx(env, w && w.px, nowMs, "quote");
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
    ctx.waitUntil(pollAndStore(env, { chart: true }));
  },
};

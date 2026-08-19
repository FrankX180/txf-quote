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
  await db
    .prepare(
      "CREATE TABLE IF NOT EXISTS tmf_retail (" +
        "id INTEGER PRIMARY KEY CHECK (id = 1)," +
        "payload TEXT NOT NULL," +
        "fetched_at TEXT," +
        "ts INTEGER" +
        ")"
    )
    .run();
}


function normalizeSite(url) {
  const s = (url.searchParams.get("site") || "txf").slice(0, 32);
  return /^[a-zA-Z0-9_-]+$/.test(s) ? s : "txf";
}
function presenceKey(site, sid) {
  return site + "::" + sid;
}
function trafficDayKey(site, dayKey) {
  return site === "txf" ? dayKey : site + ":" + dayKey;
}

const ONLINE_MS = 90 * 1000;

async function handlePing(env, url) {
  if (!env.IMB_DB) return { ok: false, reason: "no-db" };
  const site = normalizeSite(url);
  const sid = (url.searchParams.get("sid") || "").slice(0, 64);
  if (!sid || !/^[a-zA-Z0-9_-]{8,64}$/.test(sid)) {
    return { ok: false, reason: "bad-sid" };
  }
  const hit = url.searchParams.get("hit") === "1";
  const now = Date.now();
  const dayKeyRaw = tradingDayKey(now);
  const dayKey = trafficDayKey(site, dayKeyRaw);
  const pSid = presenceKey(site, sid);
  await ensureSchema(env.IMB_DB);
  await env.IMB_DB.prepare(
    "INSERT INTO presence (sid, last_seen, day_key) VALUES (?, ?, ?) " +
      "ON CONFLICT(sid) DO UPDATE SET last_seen=excluded.last_seen, day_key=excluded.day_key"
  )
    .bind(pSid, now, dayKey)
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

  let online = 0;
  if (site === "txf") {
    const onlineRow = await env.IMB_DB.prepare(
      "SELECT COUNT(*) AS n FROM presence WHERE last_seen >= ? AND (sid LIKE ? OR sid NOT LIKE '%::%')"
    )
      .bind(now - ONLINE_MS, "txf::%")
      .first();
    online = Number(onlineRow && onlineRow.n) || 0;
  } else {
    const onlineRow = await env.IMB_DB.prepare(
      "SELECT COUNT(*) AS n FROM presence WHERE last_seen >= ? AND sid LIKE ?"
    )
      .bind(now - ONLINE_MS, site + "::%")
      .first();
    online = Number(onlineRow && onlineRow.n) || 0;
  }
  await env.IMB_DB.prepare(
    "INSERT INTO traffic_day (day_key, pv, uv, peak) VALUES (?, 0, 0, ?) " +
      "ON CONFLICT(day_key) DO UPDATE SET peak = MAX(traffic_day.peak, excluded.peak)"
  )
    .bind(dayKey, online)
    .run();

  return loadStats(env, dayKeyRaw, now, site);
}

async function loadStats(env, dayKey, nowMs, siteName) {
  const now = nowMs || Date.now();
  const site = siteName || "txf";
  const dkRaw = dayKey || tradingDayKey(now);
  const dk = trafficDayKey(site, dkRaw);
  const monthKey = dkRaw.slice(0, 6);
  const empty = {
    ok: false,
    dayKey: dkRaw,
    monthKey,
    site,
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
  let onlineRow;
  if (site === "txf") {
    onlineRow = await env.IMB_DB.prepare(
      "SELECT COUNT(*) AS n FROM presence WHERE last_seen >= ? AND (sid LIKE ? OR sid NOT LIKE '%::%')"
    )
      .bind(now - ONLINE_MS, "txf::%")
      .first();
  } else {
    onlineRow = await env.IMB_DB.prepare(
      "SELECT COUNT(*) AS n FROM presence WHERE last_seen >= ? AND sid LIKE ?"
    )
      .bind(now - ONLINE_MS, site + "::%")
      .first();
  }
  const dayRow = await env.IMB_DB.prepare(
    "SELECT pv, uv, peak FROM traffic_day WHERE day_key = ?"
  )
    .bind(dk)
    .first();
  const monthLike = site === "txf" ? monthKey + "%" : site + ":" + monthKey + "%";
  const monthRow = await env.IMB_DB.prepare(
    "SELECT COALESCE(SUM(pv), 0) AS pv FROM traffic_day WHERE day_key LIKE ?"
  )
    .bind(monthLike)
    .first();
  const monthUvRow = await env.IMB_DB.prepare(
    "SELECT COUNT(DISTINCT sid) AS n FROM traffic_sid WHERE day_key LIKE ?"
  )
    .bind(monthLike)
    .first();
  let totalRow;
  let totalUvRow;
  if (site === "txf") {
    totalRow = await env.IMB_DB.prepare(
      "SELECT COALESCE(SUM(pv), 0) AS pv FROM traffic_day WHERE day_key NOT LIKE '%:%'"
    ).first();
    totalUvRow = await env.IMB_DB.prepare(
      "SELECT COUNT(DISTINCT sid) AS n FROM traffic_sid WHERE day_key NOT LIKE '%:%'"
    ).first();
  } else {
    totalRow = await env.IMB_DB.prepare(
      "SELECT COALESCE(SUM(pv), 0) AS pv FROM traffic_day WHERE day_key LIKE ?"
    )
      .bind(site + ":%")
      .first();
    totalUvRow = await env.IMB_DB.prepare(
      "SELECT COUNT(DISTINCT sid) AS n FROM traffic_sid WHERE day_key LIKE ?"
    )
      .bind(site + ":%")
      .first();
  }
  return {
    ok: true,
    dayKey: dkRaw,
    monthKey,
    site,
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
    "SELECT o, h, l, c, v, source FROM price_1m WHERE day_key=? AND session=? AND t=?"
  )
    .bind(dayKey, sess, slot)
    .first();
  // quote 只有「當下最新價」：可更新當根 close／伸縮高低，但不可把 chart 真棒打成扁棒、不可把量歸零
  let o = px;
  let h = px;
  let l = px;
  let c = px;
  let v = 0;
  let src = source || "quote";
  if (prev && prev.c != null) {
    o = prev.o != null ? prev.o : px;
    h = Math.max(prev.h != null ? prev.h : px, px);
    l = Math.min(prev.l != null ? prev.l : px, px);
    c = px;
    v = Number(prev.v) || 0;
    if (prev.source === "chart") src = "chart";
  }
  await env.IMB_DB.prepare(
    "INSERT INTO price_1m (day_key, session, t, o, h, l, c, v, ts, source) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?) " +
      "ON CONFLICT(day_key, session, t) DO UPDATE SET " +
      "o=COALESCE(price_1m.o, excluded.o), " +
      "h=CASE WHEN excluded.h IS NOT NULL AND (price_1m.h IS NULL OR excluded.h > price_1m.h) THEN excluded.h ELSE price_1m.h END, " +
      "l=CASE WHEN excluded.l IS NOT NULL AND (price_1m.l IS NULL OR excluded.l < price_1m.l) THEN excluded.l ELSE price_1m.l END, " +
      "c=excluded.c, " +
      "v=CASE WHEN excluded.v IS NOT NULL AND excluded.v >= COALESCE(price_1m.v, 0) THEN excluded.v ELSE price_1m.v END, " +
      "ts=excluded.ts, " +
      "source=CASE WHEN price_1m.source='chart' OR excluded.source='chart' THEN 'chart' ELSE excluded.source END"
  )
    .bind(dayKey, sess, slot, o, h, l, c, v, nowMs, src)
    .run();
  return { ok: true, dayKey, sess, slot, c: px, source: src };
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
    "ts=excluded.ts, " +
    "source=CASE WHEN excluded.source='chart' OR price_1m.source='chart' THEN 'chart' ELSE COALESCE(excluded.source, price_1m.source) END";
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
  let lastErr = null;
  for (let attempt = 0; attempt < 2; attempt++) {
    try {
      const r = await fetch(CHART1M, {
        headers: {
          "User-Agent":
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0.0.0",
          Referer: "https://tw.stock.yahoo.com/",
        },
      });
      if (!r.ok) {
        lastErr = "yahoo-chart-fail:" + r.status;
        continue;
      }
      const chart = await r.json();
      const all = barsFromChartJson(chart);
      const dayKey = tradingDayKey(Date.now());
      // 當日優先；若過濾後太少（夜盤切日／奇摩落後）仍寫入全包，避免只剩 quote 扁棒
      const today = all.filter((b) => b.dayKey === dayKey);
      const bars = today.length >= Math.min(30, all.length) ? today : all;
      const n = await upsertPriceBars(env, bars);
      return { ok: true, n, dayKey, raw: all.length, today: today.length, attempt: attempt + 1 };
    } catch (e) {
      lastErr = String((e && e.message) || e);
    }
  }
  return { ok: false, reason: lastErr || "chart-fail" };
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
  const out = {};
  // 權威順序：先奇摩 chart OHLC 回填，再寫 quote 最新價（只補當根 close／高低）
  // Cron 每分通常只有 1 筆最新價 → 單獨寫入必成 O=H=L=C 扁棒；chart 必須先落地
  if (opts && opts.chart) {
    try {
      out.chart = await backfillChart1m(env);
    } catch (e) {
      out.chart = { ok: false, reason: String(e && e.message || e) };
    }
  }
  const { ok, body } = await fetchYahooQuote();
  if (!ok) {
    out.skippedQuote = true;
    out.reason = "yahoo-fail";
    return out;
  }
  const rows = JSON.parse(body);
  out.imb = await appendImb(env, rows, nowMs);
  const w = extractWtx(rows);
  out.px = await appendPricePx(env, w && w.px, nowMs, "quote");
  return out;
}

// Big5 markers as latin1 (數字／逗號／TMF 仍是 ASCII，中文欄位用位元組比對)
const TMF_NAME_L1 = "\xB7\x4C\xAB\xAC\xBB\x4F\xAB\xFC"; // 微型臺指
const WHO_FOREIGN_L1 = "\xA5\x7E\xB8\xEA"; // 外資
const WHO_TRUST_L1 = "\xA7\xEB\xAB\x48"; // 投信
const WHO_DEALER_L1 = "\xA6\xDB\xC0\xE7"; // 自營
const TOTAL_L1 = "\xA6\x58\xAD\x70"; // 合計
const AFTER_L1 = "\xBD\x4C\xAB\xE1"; // 盤後

function nintCell(x) {
  const s = String(x == null ? "" : x).replace(/,/g, "").trim();
  if (!s || s === "-") return 0;
  const n = Number(s);
  return Number.isFinite(n) ? Math.trunc(n) : 0;
}

function parseCsvLatin1(text) {
  const rows = [];
  let row = [];
  let cur = "";
  let inQ = false;
  for (let i = 0; i < text.length; i++) {
    const ch = text[i];
    if (inQ) {
      if (ch === '"' && text[i + 1] === '"') {
        cur += '"';
        i++;
      } else if (ch === '"') inQ = false;
      else cur += ch;
      continue;
    }
    if (ch === '"') {
      inQ = true;
      continue;
    }
    if (ch === ",") {
      row.push(cur);
      cur = "";
      continue;
    }
    if (ch === "\n") {
      row.push(cur);
      rows.push(row);
      row = [];
      cur = "";
      continue;
    }
    if (ch !== "\r") cur += ch;
  }
  if (cur.length || row.length) {
    row.push(cur);
    rows.push(row);
  }
  return rows;
}

function ymdSlash(d) {
  const y = d.getUTCFullYear();
  const m = String(d.getUTCMonth() + 1).padStart(2, "0");
  const day = String(d.getUTCDate()).padStart(2, "0");
  return y + "/" + m + "/" + day;
}

function parseInstLatin1(text) {
  const out = {};
  const rows = parseCsvLatin1(text);
  for (const r of rows) {
    if (!r || r.length < 14) continue;
    if (String(r[1] || "").indexOf(TMF_NAME_L1) < 0) continue;
    const who = String(r[2] || "");
    if (who.indexOf(TOTAL_L1) >= 0) continue;
    const d = String(r[0] || "").replace(/\//g, "");
    if (d.length !== 8) continue;
    const il = nintCell(r[9]);
    const ish = nintCell(r[11]);
    const inet = nintCell(r[13]);
    const rec = out[d] || { il: 0, ish: 0, inet: 0, foreign: 0, trust: 0, dealer: 0 };
    rec.il += il;
    rec.ish += ish;
    rec.inet += inet;
    if (who.indexOf(WHO_FOREIGN_L1) >= 0) rec.foreign = inet;
    else if (who.indexOf(WHO_TRUST_L1) >= 0) rec.trust = inet;
    else if (who.indexOf(WHO_DEALER_L1) >= 0) rec.dealer = inet;
    out[d] = rec;
  }
  return out;
}

function parseOiLatin1(text) {
  const out = {};
  const rows = parseCsvLatin1(text);
  for (const r of rows) {
    if (!r || r.length < 12) continue;
    if (String(r[1] || "").trim() !== "TMF") continue;
    const sess = r[17] != null ? String(r[17]) : "";
    if (sess.indexOf(AFTER_L1) >= 0) continue;
    const d = String(r[0] || "").replace(/\//g, "");
    if (d.length !== 8) continue;
    out[d] = (out[d] || 0) + nintCell(r[11]);
  }
  return out;
}

async function taifexPost(url, form, referer) {
  const body = new URLSearchParams(form).toString();
  const r = await fetch(url, {
    method: "POST",
    headers: {
      "User-Agent": "Mozilla/5.0 (compatible; txf-quote-worker/1.0)",
      "Content-Type": "application/x-www-form-urlencoded",
      Origin: "https://www.taifex.com.tw",
      Referer: referer,
    },
    body,
  });
  if (!r.ok) throw new Error("taifex HTTP " + r.status);
  const buf = new Uint8Array(await r.arrayBuffer());
  let s = "";
  for (let i = 0; i < buf.length; i++) s += String.fromCharCode(buf[i]);
  return s;
}

function buildTmfSeries(instMap, oiMap) {
  const dates = Object.keys(instMap).sort().reverse();
  const series = [];
  for (const d of dates) {
    const a = instMap[d];
    const oi = oiMap[d];
    if (!oi || oi <= 0) continue;
    const retail = a.ish - a.il;
    const ratio = Math.round((retail / oi) * 1000) / 10;
    series.push({
      date: d,
      retail,
      ratio,
      marketOi: oi,
      instNet: a.inet,
      foreign: a.foreign,
      trust: a.trust,
      dealer: a.dealer,
    });
  }
  return series;
}

async function loadTmfPayload(env) {
  if (!env.IMB_DB) return null;
  await ensureSchema(env.IMB_DB);
  const row = await env.IMB_DB.prepare(
    "SELECT payload, fetched_at, ts FROM tmf_retail WHERE id = 1"
  ).first();
  if (!row || !row.payload) return null;
  try {
    const j = JSON.parse(row.payload);
    j._cachedAt = row.fetched_at || null;
    j._ts = row.ts || null;
    return j;
  } catch (e) {
    return null;
  }
}

async function saveTmfPayload(env, payload) {
  if (!env.IMB_DB) return;
  await ensureSchema(env.IMB_DB);
  const now = Date.now();
  const fetchedAt = twParts(now);
  const stamp =
    fetchedAt.y +
    "-" +
    fetchedAt.mo +
    "-" +
    fetchedAt.d +
    " " +
    String(fetchedAt.h).padStart(2, "0") +
    ":" +
    String(fetchedAt.mi).padStart(2, "0") +
    ":" +
    String(fetchedAt.s).padStart(2, "0");
  payload.fetchedAt = stamp;
  await env.IMB_DB.prepare(
    "INSERT INTO tmf_retail (id, payload, fetched_at, ts) VALUES (1, ?, ?, ?) " +
      "ON CONFLICT(id) DO UPDATE SET payload=excluded.payload, fetched_at=excluded.fetched_at, ts=excluded.ts"
  )
    .bind(JSON.stringify(payload), stamp, now)
    .run();
}

async function fetchTmfOpenApiLatest() {
  const hdr = {
    "User-Agent": "Mozilla/5.0 (compatible; txf-quote-worker/1.0)",
    Accept: "application/json",
  };
  const [instR, mktR] = await Promise.all([
    fetch(
      "https://openapi.taifex.com.tw/v1/MarketDataOfMajorInstitutionalTradersDetailsOfFuturesContractsBytheDate",
      { headers: hdr }
    ),
    fetch("https://openapi.taifex.com.tw/v1/DailyMarketReportFut", { headers: hdr }),
  ]);
  if (!instR.ok) throw new Error("openapi inst HTTP " + instR.status);
  if (!mktR.ok) throw new Error("openapi mkt HTTP " + mktR.status);
  const inst = await instR.json();
  const mkt = await mktR.json();
  const micro = (inst || []).filter(
    (x) => x && String(x.ContractCode || "").indexOf("微型臺指") >= 0
  );
  if (!micro.length) throw new Error("openapi no TMF inst");
  const d = String(micro[0].Date || "").replace(/\//g, "");
  if (d.length !== 8) throw new Error("openapi bad date");
  let il = 0;
  let ish = 0;
  let inet = 0;
  let foreign = 0;
  let trust = 0;
  let dealer = 0;
  for (const x of micro) {
    const who = String(x.Item || "");
    const l = nintCell(x["OpenInterest(Long)"]);
    const s = nintCell(x["OpenInterest(Short)"]);
    const n = nintCell(x["OpenInterest(Net)"]);
    il += l;
    ish += s;
    inet += n;
    if (who.indexOf("外資") >= 0) foreign = n;
    else if (who.indexOf("投信") >= 0) trust = n;
    else if (who.indexOf("自營") >= 0) dealer = n;
  }
  let oi = 0;
  for (const x of mkt || []) {
    if (!x || String(x.Contract || "").trim() !== "TMF") continue;
    if (String(x.Date || "").replace(/\//g, "") !== d) continue;
    const sess = String(x.TradingSession || "");
    if (sess.indexOf("盤後") >= 0) continue;
    const mon = String(x["ContractMonth(Week)"] || "");
    if (mon.indexOf("/") >= 0) continue;
    oi += nintCell(x.OpenInterest);
  }
  if (oi <= 0) throw new Error("openapi no TMF oi");
  const retail = ish - il;
  const ratio = Math.round((retail / oi) * 1000) / 10;
  return {
    date: d,
    retail,
    ratio,
    marketOi: oi,
    instNet: inet,
    foreign,
    trust,
    dealer,
  };
}

async function refreshTmfFromWebsite(env, days) {
  const n = days || 21;
  const now = Date.now();
  const p = twParts(now);
  const endUtc = Date.UTC(Number(p.y), Number(p.mo) - 1, Number(p.d));
  const startUtc = endUtc - (n - 1) * 86400000;
  // futDataDown 長區間會回 HTML 錯頁；切 7 日塊再 merge（對齊本機腳本）
  const span = 7;
  const instMap = {};
  const oiMap = {};
  let chunks = 0;
  let oiBytes = 0;
  for (let t = startUtc; t <= endUtc; t += span * 86400000) {
    const a = t;
    const b = Math.min(t + (span - 1) * 86400000, endUtc);
    const start = ymdSlash(new Date(a));
    const endS = ymdSlash(new Date(b));
    const instTxt = await taifexPost(
      "https://www.taifex.com.tw/cht/3/futContractsDateDown",
      { queryStartDate: start, queryEndDate: endS },
      "https://www.taifex.com.tw/cht/3/futContractsDateView"
    );
    const oiTxt = await taifexPost(
      "https://www.taifex.com.tw/cht/3/futDataDown",
      {
        down_type: "1",
        commodity_id: "TMF",
        queryStartDate: start,
        queryEndDate: endS,
      },
      "https://www.taifex.com.tw/cht/3/futDailyMarketView"
    );
    oiBytes += oiTxt.length;
    Object.assign(instMap, parseInstLatin1(instTxt));
    Object.assign(oiMap, parseOiLatin1(oiTxt));
    chunks++;
  }
  return {
    series: buildTmfSeries(instMap, oiMap),
    fetched: Object.keys(instMap).length,
    oiFetched: Object.keys(oiMap).length,
    chunks,
    oiBytes,
    range: [ymdSlash(new Date(startUtc)), ymdSlash(new Date(endUtc))],
    source: "taifex:futContractsDateDown+futDataDown",
  };
}

async function mergeSaveTmf(env, rows, meta) {
  const prev = await loadTmfPayload(env);
  const by = {};
  for (const r of (prev && prev.series) || []) {
    if (r && r.date) by[r.date] = r;
  }
  for (const r of rows || []) {
    if (r && r.date) by[r.date] = r;
  }
  const merged = Object.keys(by)
    .sort()
    .reverse()
    .map((k) => by[k])
    .slice(0, 1200);
  const src = (meta && meta.source) || "taifex:openapi+download";
  const payload = {
    date: merged[0] ? merged[0].date : "",
    source: src,
    label: "微台散戶多空比",
    note: "ratio%=retail_net/market_oi*100; retail_net=inst_short-inst_long",
    series: merged,
  };
  await saveTmfPayload(env, payload);
  return {
    ok: true,
    n: merged.length,
    date: payload.date,
    ...(meta || {}),
  };
}

async function importGithubTmf(env) {
  const r = await fetch("https://frankx180.github.io/txf-quote/data/tmf_retail.json", {
    headers: { "User-Agent": "txf-quote-worker/1.0", Accept: "application/json" },
  });
  if (!r.ok) throw new Error("github tmf HTTP " + r.status);
  const j = await r.json();
  const rows = (j && j.series) || [];
  if (!rows.length) throw new Error("github tmf empty");
  return mergeSaveTmf(env, rows, {
    source: (j && j.source) || "github:tmf_retail",
    via: "github-import",
    imported: rows.length,
  });
}

async function refreshTmfRetail(env, days) {
  const n = days || 21;
  let openRow = null;
  let openErr = null;
  try {
    openRow = await fetchTmfOpenApiLatest();
  } catch (e) {
    openErr = String(e && e.message || e);
  }

  // 日常：OpenAPI 只含最新交易日；有歷史快取且只補最新 → 不必再打下載頁
  const prev = await loadTmfPayload(env);
  const haveHist = !!(prev && prev.series && prev.series.length >= 10);
  if (openRow && haveHist && n <= 5) {
    return mergeSaveTmf(env, [openRow], {
      source: "taifex:openapi",
      via: "openapi",
      openapiDate: openRow.date,
      openErr,
    });
  }

  let web = null;
  let webErr = null;
  try {
    web = await refreshTmfFromWebsite(env, n);
  } catch (e) {
    webErr = String(e && e.message || e);
  }

  const rows = [];
  if (web && web.series) rows.push(...web.series);
  if (openRow) rows.push(openRow);
  if (!rows.length) {
    throw new Error("tmf refresh failed openapi=" + openErr + " web=" + webErr);
  }
  const src = openRow && web
    ? "taifex:openapi+futContractsDateDown+futDataDown"
    : openRow
      ? "taifex:openapi"
      : "taifex:futContractsDateDown+futDataDown";
  return mergeSaveTmf(env, rows, {
    source: src,
    via: openRow && web ? "openapi+web" : openRow ? "openapi" : "web",
    openapiDate: openRow ? openRow.date : null,
    openErr,
    webErr,
    fetched: web ? web.fetched : 0,
    oiFetched: web ? web.oiFetched : openRow ? 1 : 0,
    chunks: web ? web.chunks : 0,
    oiBytes: web ? web.oiBytes : 0,
    range: web ? web.range : null,
  });
}

function wantTmfRefresh(ms) {
  const p = twParts(ms);
  const fmt = new Intl.DateTimeFormat("en-US", {
    timeZone: "Asia/Taipei",
    weekday: "short",
  });
  const wd = fmt.format(new Date(ms));
  if (wd === "Sat" || wd === "Sun") return false;
  const hm = p.hm;
  if (hm >= 1500 && hm <= 2000) return true;
  if (hm >= 800 && hm <= 1000) return true;
  return false;
}

function twDateStr(ms) {
  const p = twParts(ms);
  return p.y + p.mo + p.d;
}

// 上一個工作日（六日往前找；國定假日的近似——誤判頂多多打一次網站下載，無害且自癒）
function lastWorkdayTw(ms) {
  const fmt = new Intl.DateTimeFormat("en-US", {
    timeZone: "Asia/Taipei",
    weekday: "short",
  });
  for (let i = 1; i <= 4; i++) {
    const t = ms - i * 86400000;
    const wd = fmt.format(new Date(t));
    if (wd !== "Sat" && wd !== "Sun") return twDateStr(t);
  }
  return twDateStr(ms - 86400000);
}

// 判斷 OpenAPI 給的「最新交易日」是否落後預期：落後 → 網站 14 天補抓
// 15:00–20:00 視窗（今天資料應公布）→ 預期今天；08:00–10:00（補昨晚）→ 預期上一個工作日
function tmfOpenApiStale(ms, openDate, prev) {
  if (!openDate || openDate.length !== 8) return "openapi-bad-date";
  const p = twParts(ms);
  let expected = twDateStr(ms);
  if (p.hm >= 800 && p.hm <= 1000) {
    expected = lastWorkdayTw(ms);
  }
  if (openDate < expected) return "openapi-stale(" + openDate + "<" + expected + ")";
  const top =
    prev && prev.series && prev.series.length ? String(prev.series[0].date) : null;
  if (top && openDate < top) return "openapi-rollback(" + openDate + "<" + top + ")";
  return null;
}

async function maybeRefreshTmf(env) {
  if (!env.IMB_DB) return { ok: false, reason: "no-db" };
  const now = Date.now();
  if (!wantTmfRefresh(now)) return { ok: false, reason: "outside-window" };
  const prev = await loadTmfPayload(env);
  if (prev && prev._ts && now - Number(prev._ts) < 25 * 60 * 1000) {
    return { ok: false, reason: "throttled", ageMin: Math.round((now - Number(prev._ts)) / 60000) };
  }
  // 日常 cron：先試 OpenAPI（穩定 JSON）；官方停更／落後預期交易日 → 網站 14 天補抓
  try {
    const openRow = await fetchTmfOpenApiLatest();
    const stale = tmfOpenApiStale(now, openRow.date, prev);
    if (stale) {
      const st = await refreshTmfRetail(env, 14);
      st.stale = stale;
      return st;
    }
    return mergeSaveTmf(env, [openRow], {
      source: "taifex:openapi",
      via: "openapi",
      openapiDate: openRow.date,
    });
  } catch (e) {
    const st = await refreshTmfRetail(env, 14);
    st.openErr = String(e && e.message || e);
    return st;
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

    if (kind === "tmf") {
      try {
        if (url.searchParams.get("import") === "1") {
          const st = await importGithubTmf(env);
          const payload = await loadTmfPayload(env);
          return jsonResp({ ...payload, _import: st }, 200, {
            "Cache-Control": "no-store",
            "CDN-Cache-Control": "no-store",
            "Cloudflare-CDN-Cache-Control": "no-store",
          });
        }
        if (url.searchParams.get("refresh") === "1") {
          const st = await refreshTmfRetail(env, Number(url.searchParams.get("days") || 30));
          let payload = await loadTmfPayload(env);
          // 短歷史（雲端只補近段）時併入 GitHub 全史
          if (!payload || !payload.series || payload.series.length < 200) {
            try {
              const imp = await importGithubTmf(env);
              payload = await loadTmfPayload(env);
              st.githubImport = imp;
            } catch (e) {
              st.githubImportErr = String(e && e.message || e);
            }
          }
          return jsonResp({ ...payload, _refresh: st }, 200, {
            "Cache-Control": "no-store",
            "CDN-Cache-Control": "no-store",
            "Cloudflare-CDN-Cache-Control": "no-store",
          });
        }
        let payload = await loadTmfPayload(env);
        if (!payload || !(payload.series && payload.series.length)) {
          await refreshTmfRetail(env, 30);
          payload = await loadTmfPayload(env);
        }
        if (payload && payload.series && payload.series.length < 200) {
          try {
            await importGithubTmf(env);
            payload = await loadTmfPayload(env);
          } catch (e) {}
        }
        if (!payload) return jsonResp({ ok: false, reason: "empty" }, 404, { "Cache-Control": "no-store" });
        return jsonResp(payload, 200, {
          "Cache-Control": "public, max-age=0",
          "CDN-Cache-Control": "public, max-age=60",
          "Cloudflare-CDN-Cache-Control": "public, max-age=60",
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

    const cacheHdr =
      kind === "1m"
        ? {
            "Cache-Control": "no-store",
            "CDN-Cache-Control": "no-store",
            "Cloudflare-CDN-Cache-Control": "no-store",
          }
        : {
            "Cache-Control": "public, max-age=0",
            "CDN-Cache-Control": "public, max-age=5",
            "Cloudflare-CDN-Cache-Control": "public, max-age=5",
          };
    return new Response(body, {
      status: r.status,
      headers: {
        "content-type": "application/json; charset=utf-8",
        ...cacheHdr,
        ...CORS,
      },
    });
  },

  async scheduled(event, env, ctx) {
    ctx.waitUntil(
      (async () => {
        await pollAndStore(env, { chart: true });
        try {
          await maybeRefreshTmf(env);
        } catch (e) {
          /* 記錄而非全吞：官方 OpenAPI 停更時可從 CF 日誌追蹤 */
          console.error("tmf cron failed", String((e && e.message) || e));
        }
      })()
    );
  },
};

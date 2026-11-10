const YAHOO =
  "https://tw.stock.yahoo.com/_td-stock/api/resource/" +
  "StockServices.stockList;symbols=WTX%26,WMT%26,WTM%26,WCDF%26,WCCF%26";
const CHART1M =
  "https://tw.stock.yahoo.com/_td-stock/api/resource/" +
  "StockServices.chart;symbol=WTX%26;period=1m;range=1d";

// ── 期交所 MIS 官方即時報價（REST；盤後即時、欄位穩定）──
// 台指期夜盤 Yahoo 常滯後或不穩（IP 選擇性 5xx）；以 MIS 即時價覆蓋，Yahoo 供五檔。
const MIS_API = "https://mis.taifex.com.tw/futures/api/getQuoteList";
const MIS_REFERER =
  "https://mis.taifex.com.tw/futures/RegularSession/EquityIndices/FuturesDomestic/";
const MIS_MONTHS = "ABCDEFGHIJKL";  // MIS 月份字母 A=1月..L=12月（非期貨慣用碼）

// ── 上游硬超時（2026-11-05）──
// 原本對 Yahoo／期交所 MIS 完全沒有 timeout：上游一卡，請求就一路掛到 Cloudflare 邊緣
// 自己斷線（實測 15～20 秒後回 http 000），前端因此卡在「載入中」。
// 超時屬 I/O 等待，不吃免費方案的 10ms CPU 額度。
const UPSTREAM_TIMEOUT_MS = 4000;   // Yahoo 總表／1 分 K／GitHub 檔案
const MIS_TIMEOUT_MS = 2500;        // 期交所 MIS 單次嘗試（夜盤最多試兩個 MT）
function fetchSignal(ms) {
  try { return AbortSignal.timeout(ms); } catch (_) { return undefined; }
}

function misMonthScore(s) {
  const m = /^TXF([A-Z])(\d)-[MF]$/.exec(s || "");
  if (!m) return 9e9;
  const mi = MIS_MONTHS.indexOf(m[1]);
  return mi < 0 ? 9e9 : Number(m[2]) * 100 + mi;
}

function misTimeIso(dateStr, timeStr) {
  if (!dateStr || !timeStr || String(dateStr).length < 8) return null;
  const y = dateStr.slice(0, 4), mo = dateStr.slice(4, 6), d = dateStr.slice(6, 8);
  const h = timeStr.slice(0, 2), mi = timeStr.slice(2, 4), s = timeStr.slice(4, 6);
  return y + "-" + mo + "-" + d + "T" + h + ":" + mi + ":" + s + "+08:00";
}

/** 最近一次成功的 root 報價（上游全滅時的最後防線；模組層，isolate 內有效）。 */
let _lastRootGood = null;

/** 取台指期近月即時報價；抓不到回 null。 */
let _misDebug = "";
async function fetchMisQuote() {
  _misDebug = "";
  const tries = sessionOf(Date.now()) === "day" ? ["0", "1"] : ["1", "0"];
  for (const mt of tries) {
    try {
      const r = await fetch(MIS_API, {
        method: "POST",
        headers: {
          "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0.0.0",
          "Content-Type": "application/json;charset=UTF-8",
          Origin: "https://mis.taifex.com.tw",
          Referer: MIS_REFERER,
        },
        signal: fetchSignal(MIS_TIMEOUT_MS),
        body: JSON.stringify({
          MarketType: mt, SymbolType: "F", KindID: "1", CID: "", ExpireMonth: "", PageNo: 1,
        }),
      });
      if (!r.ok) { _misDebug += " MT" + mt + ":http" + r.status; continue; }
      const txt = await r.text();
      let j;
      try { j = JSON.parse(txt); } catch (e) {
        _misDebug += " MT" + mt + ":notjson(" + txt.slice(0, 100) + ")"; continue;
      }
      const ql = (j && j.RtData && j.RtData.QuoteList) || [];
      // 日盤契約 -F、夜盤 -M（2026-10-13 實測）；兩種後綴都接受，再依 MT 順序與月份排序取近月
      const todayKey = twDateStr(Date.now());
      const cand = ql
        .filter((x) => /^TXF[A-Z]\d-[MF]$/.test(x.SymbolID || ""))
        .sort((a, b) => misMonthScore(a.SymbolID) - misMonthScore(b.SymbolID));
      // 優先取「今日交易日」且有成交價者，避免另一盤殘值；再退而求其次
      // 同一個曆日可能同時有日盤（13:45 收）與夜盤（15:00 起）兩筆，
      // 只比 CDate 會在夜盤取到日盤收盤價（2026-11-10 審查）
      const sesNow = sessionOf(Date.now());
      const cMin = (x) => {
        const t = String(x.CTime || "").padStart(6, "0");
        return Number(t.slice(0, 2)) * 60 + Number(t.slice(2, 4));
      };
      const inSes = (x) => {
        const m = cMin(x);
        if (!Number.isFinite(m)) return false;
        return sesNow === "day"
          ? (m >= 8 * 60 + 45 && m <= 13 * 60 + 50)
          : (m >= 15 * 60 || m <= 5 * 60 + 10);
      };
      const pick =
        cand.find((x) => x.CLastPrice && String(x.CDate) === todayKey && inSes(x)) ||
        cand.find((x) => x.CLastPrice && inSes(x)) ||
        cand.find((x) => x.CLastPrice) ||
        null;
      if (pick) {
        const num = (v) => (v == null || v === "" ? null : Number(v));
        return {
          sym: pick.SymbolID,
          px: num(pick.CLastPrice), bid: num(pick.CBidPrice1), ask: num(pick.CAskPrice1),
          open: num(pick.COpenPrice), high: num(pick.CHighPrice), low: num(pick.CLowPrice),
          ref: num(pick.CRefPrice), diff: num(pick.CDiff), rate: num(pick.CDiffRate),
          vol: num(pick.CTotalVolume), date: pick.CDate, time: pick.CTime,
          mt, want: tries[0],
        };
      }
      _misDebug += " MT" + mt + ":empty(RtCode=" + (j && j.RtCode) + ",n=" + ql.length + ")";
    } catch (e) {
      const nm = (e && e.name) || "";
      _misDebug += (nm === "TimeoutError" || nm === "AbortError")
        ? " MT" + mt + ":timeout(" + MIS_TIMEOUT_MS + "ms)"
        : " MT" + mt + ":ex(" + String((e && e.message) || e).slice(0, 100) + ")";
    }
  }
  return null;
}

const CORS = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Methods": "GET, OPTIONS",
  "Access-Control-Allow-Headers": "*",
  // 降級標記（X-TXF-Stale）是自訂 header，跨域要 expose 前端才讀得到
  "Access-Control-Expose-Headers": "X-TXF-Stale",
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

// 模組層快取：Intl.DateTimeFormat 建構一次要數十 µs，而免費方案每次請求 CPU 上限只有 10ms，
// 熱路徑（twParts／sessionOf／shouldRunCron／maybeHealChips）每次重建等於白燒 CPU。2026-10-20
const TW_FMT = new Intl.DateTimeFormat("en-US", {
  timeZone: "Asia/Taipei",
  year: "numeric",
  month: "2-digit",
  day: "2-digit",
  hour: "2-digit",
  minute: "2-digit",
  second: "2-digit",
  hourCycle: "h23",
});
const TW_WD_FMT = new Intl.DateTimeFormat("en-US", {
  timeZone: "Asia/Taipei",
  weekday: "short",
});

function twParts(ms) {
  const fmt = TW_FMT;
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


let _dormantUntil = 0;
let _yahooFailCount = 0;
let _yahooBackoffUntil = 0;

// ── 休市日 SSOT（與前端／Actions 共用 repo 的 data/holidays.json）──
// 假日只檔日盤與當晚夜盤；凌晨 00:00–05:09 仍屬前一營業日夜盤，不可檔。
// 取不到時 fail-open（空集合），回退到原有颱風假自適應熔斷。
const HOLIDAY_URL = "https://raw.githubusercontent.com/FrankX180/txf-quote/master/data/holidays.json";
let _holidaySet = null;
let _holidayFetchedAt = 0;
async function ensureHolidays() {
  const now = Date.now();
  if (_holidaySet && now - _holidayFetchedAt < 1 * 3600 * 1000) return; // 1h：颱風假 adhoc 早上寫入後要能當天生效
  try {
    const r = await fetch(HOLIDAY_URL, { cf: { cacheTtl: 3600, cacheEverything: true }, signal: fetchSignal(UPSTREAM_TIMEOUT_MS) });
    if (!r.ok) return;
    const j = await r.json();
    const arr = Array.isArray(j) ? j : (j && (j.holidays || j.closed)) || [];
    if (arr.length) {
      _holidaySet = new Set(arr.map(String));
      _holidayFetchedAt = now;
    }
  } catch (_) {}
}
function isHolidayMs(ms) {
  if (!_holidaySet) return false;
  const p = twParts(ms);
  return _holidaySet.has(`${p.y}${p.mo}${p.d}`);
}

/**
 * 夜盤歸屬（與前端 nightSessionLive 同一 SSOT）：
 * 夜盤 14:58（開盤曆日）跨午夜走到次日 05:09，凌晨要問「前一日是不是營業日」。
 * 前一日休市（國假／週末）就沒有夜盤，不能只看今天。ms 為台北時間。
 */
function nightLiveMs(ms) {
  const p = twParts(ms);
  const base = p.hm < 510 ? ms - 24 * 3600 * 1000 : ms;
  const wd = TW_WD_FMT.format(new Date(base));
  if (wd === "Sun" || wd === "Sat") return false;
  return !isHolidayMs(base);
}

/** 計算當前交易盤別的收盤時間戳 (台北時間，避免 Worker UTC 時區 setHours 偏移) */
function sessionEndMs(ms) {
  const p = twParts(ms);
  const hm = p.hm;
  const y = Number(p.y);
  const mo = Number(p.mo) - 1;
  const d = Number(p.d);

  // 日盤：收在台北時間 13:46:00 (對應 UTC 05:46:00)
  if (hm >= 845 && hm <= 1345) {
    return Date.UTC(y, mo, d, 5, 46, 0);
  }
  // 夜盤前半段 (14:58~23:59)：收在台北時間隔日 05:01:00 (對應今日 UTC 21:01:00)
  if (hm >= 1458) {
    return Date.UTC(y, mo, d, 21, 1, 0);
  }
  // 夜盤後半段 (00:00~05:10)：收在台北時間今日 05:01:00 (對應昨日 UTC 21:01:00)
  if (hm < 510) {
    const prev = new Date(ms - 24 * 3600 * 1000);
    const pp = twParts(prev.getTime());
    return Date.UTC(Number(pp.y), Number(pp.mo) - 1, Number(pp.d), 21, 1, 0);
  }
  return 0;
}

/** 0 毫秒極速判定：週末、非交易時段、或颱風假熔斷中，直接完全跳過定時任務 */
function shouldRunCron(ms) {
  const p = twParts(ms);
  const hm = p.hm;
  const wd = TW_WD_FMT.format(new Date(ms));

  // 1. 週末整整兩天完全無盤，直接 0ms return (週六 05:10 ~ 週一 08:44)
  if (wd === "Sun") return false;
  if (wd === "Sat" && hm >= 510) return false;
  if (wd === "Mon" && hm < 845) return false;

  // 1.5 假日判定：凌晨（00:00–05:09）屬前一營業日夜盤，看前一日；其餘看當日
  if (hm < 510) {
    if (!nightLiveMs(ms)) return false; // 前一日休市／週末 → 凌晨根本沒有夜盤
  } else if (isHolidayMs(ms)) {
    return false; // 國定假日：日盤與當晚夜盤整日跳過
  }

  // 2. 颱風假 / 國定假日自適應熔斷中：整盤休眠跳過
  if (ms < _dormantUntil) return false;

  // 3. Yahoo 連續異常退避冷卻中：暫停輪詢保護 CPU 與配額
  if (ms < _yahooBackoffUntil) return false;

  // 4. 工作日若處於盤中交易時段：執行
  if (sessionOf(ms)) return true;

  // 5. 工作日若處於 14:30 ~ 15:30 (盤後法人籌碼出爐守護視窗)：執行
  if (hm >= 1430 && hm <= 1530) return true;

  // 6. 其他日間空窗 (如 13:46~14:29、15:31~14:57)：完全跳過
  return false;
}

function sessionOf(ms) {
  const p = twParts(ms);
  const hm = p.hm;
  // 週六 05:10 後、週日：不寫
  const wd = TW_WD_FMT.format(new Date(ms)); // Sun Mon ...
  if (wd === "Sun") return null;
  if (wd === "Sat" && hm >= 510) return null;
  // 凌晨（00:00–05:09）屬前一日夜盤；其餘時段看當日
  if (hm < 510) return nightLiveMs(ms) ? "night" : null;
  if (isHolidayMs(ms)) return null; // 國定假日：日盤與當晚夜盤皆無盤
  if (hm >= 845 && hm <= 1345) return "day";
  if (hm >= 1458) return "night";
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

/** Yahoo regularMarketTime → 台北曆日 YYYYMMDD；解析不到回 ""。 */
function yahooSourceDayKey(rawW) {
  if (!rawW) return "";
  const t = rawW.regularMarketTime;
  let ms = 0;
  if (typeof t === "number" && t > 1e9) {
    ms = t < 1e12 ? t * 1000 : t;
  } else if (typeof t === "string" && t) {
    ms = Date.parse(t);
  } else if (t && typeof t === "object") {
    const raw = t.raw != null ? t.raw : t.fmt;
    if (typeof raw === "number" && raw > 1e9) ms = raw < 1e12 ? raw * 1000 : raw;
    else if (typeof raw === "string") ms = Date.parse(raw);
  }
  if (!ms || !Number.isFinite(ms)) return "";
  return tradingDayKey(ms);
}

/** 來源日必須等於今日交易鍵，才准把 Yahoo 五檔／內外盤當 live。 */
function yahooBookFresh(rawW, nowMs) {
  if (!rawW) return false;
  if (!sessionOf(nowMs)) return false;
  const src = yahooSourceDayKey(rawW);
  return !!src && src === tradingDayKey(nowMs);
}

/** 真五檔必夾住成交價（買一 ≤ 成交 ≤ 賣一，容差內）。
 *  夜盤 Yahoo 常「總表（成交價）續動、五檔欄位停更」，與官方即時成交價脫節的書一律不採用
 *  （寧可留白，也不把錯價送給前端 applyLive）。 */
function bookStraddlesPx(bk, px, tol) {
  if (!Array.isArray(bk) || !bk.length) return true;
  if (!(px > 0)) return true;
  const b = rawNum(bk[0] && bk[0].bid);
  const a = rawNum(bk[0] && bk[0].ask);
  if (b == null && a == null) return true;
  if (b != null && px < b - tol) return false;
  if (a != null && px > a + tol) return false;
  return true;
}

/** 夜盤 Yahoo 常標 marketStatus=close，不可當颱風假。有內外盤或有量即視為在交易。
 *  來源日≠今日交易鍵（假日殘值）一律不當 live。 */
function looksLikeLiveQuote(rawW, nowMs) {
  if (!rawW) return false;
  if (!yahooBookFresh(rawW, nowMs != null ? nowMs : Date.now())) return false;
  const w = extractWtx([rawW]);
  if (w && w.inn > 0 && w.outv > 0) return true;
  const px = rawNum(rawW.price);
  const vol = rawNum(rawW.volume);
  return px != null && px > 10000 && vol != null && vol > 0;
}

// D1 表結構與複合索引：每個 isolate 首次請求跑一次 DDL（IF NOT EXISTS 對已存在的表是 no-op）。
// 初值必須 false：原本寫 true 等於「假設表已經存在」，一旦 binding 指到新的／空的 D1，
// 所有寫入都會 no such table 被下面的空 catch 吞掉（2026-11-10 審查 S04#2）。
let _schemaEnsured = false;
async function ensureSchema(db, force = false) {
  if (!force && _schemaEnsured) return;
  if (!db) return;
  try {
    await db.prepare("CREATE TABLE IF NOT EXISTS imb (day_key TEXT NOT NULL, session TEXT NOT NULL, t INTEGER NOT NULL, d REAL NOT NULL, inn REAL, outv REAL, ts INTEGER, PRIMARY KEY (day_key, session, t))").run();
    await db.prepare("CREATE TABLE IF NOT EXISTS presence (sid TEXT PRIMARY KEY, last_seen INTEGER NOT NULL, day_key TEXT)").run();
    await db.prepare("CREATE TABLE IF NOT EXISTS traffic_day (day_key TEXT PRIMARY KEY, pv INTEGER NOT NULL DEFAULT 0, uv INTEGER NOT NULL DEFAULT 0, peak INTEGER NOT NULL DEFAULT 0)").run();
    await db.prepare("CREATE TABLE IF NOT EXISTS traffic_sid (day_key TEXT NOT NULL, sid TEXT NOT NULL, PRIMARY KEY (day_key, sid))").run();
    await db.prepare("CREATE TABLE IF NOT EXISTS price_1m (day_key TEXT NOT NULL, session TEXT NOT NULL, t INTEGER NOT NULL, o REAL, h REAL, l REAL, c REAL NOT NULL, v INTEGER DEFAULT 0, ts INTEGER, source TEXT, PRIMARY KEY (day_key, session, t))").run();
    await db.prepare("CREATE TABLE IF NOT EXISTS tmf_retail (id INTEGER PRIMARY KEY CHECK (id = 1), payload TEXT NOT NULL, fetched_at TEXT, ts INTEGER)").run();
    await db.prepare("CREATE TABLE IF NOT EXISTS heal_state (k TEXT PRIMARY KEY, ts INTEGER NOT NULL, v TEXT)").run();
    await db.prepare("CREATE TABLE IF NOT EXISTS heal_log (id INTEGER PRIMARY KEY AUTOINCREMENT, ts INTEGER NOT NULL, reason TEXT, ok INTEGER, detail TEXT)").run();
    await db.prepare("CREATE INDEX IF NOT EXISTS idx_traffic_sid_day ON traffic_sid (day_key, sid)").run();
    await db.prepare("CREATE INDEX IF NOT EXISTS idx_price_1m_query ON price_1m (day_key, session, t ASC)").run();
    await db.prepare("CREATE INDEX IF NOT EXISTS idx_imb_query ON imb (day_key, session, t ASC)").run();
    await db.prepare("CREATE INDEX IF NOT EXISTS idx_presence_last ON presence (last_seen, sid)").run();
    _schemaEnsured = true;
  } catch (_) {}
}




// ---- 空窗偵測自動補觸發（GH Actions 延遲跳班備援） ----
const HEAL_GITHUB_OWNER = "FrankX180";
const HEAL_GITHUB_REPO = "txf-quote";
const HEAL_WORKFLOW = "update-quote.yml";
const HEAL_MIN_INTERVAL_MS = 8 * 60 * 1000;
const HEAL_STALE_MS = 6 * 60 * 1000;
const HEAL_GH_SNAPSHOT = "https://frankx180.github.io/txf-quote/data/snapshot.json";
const HEAL_GH_KLINE = "https://frankx180.github.io/txf-quote/data/kline-minute.json";
const HEAL_GH_UNCOVERED = "https://frankx180.github.io/txf-quote/data/uncovered.json";
const HEAL_CHIPS_INTERVAL_MS = 2 * 60 * 1000; // 盤後籌碼定案視窗（14:30–15:30）每 2 分鐘刷一次直到齊備
// 富台（SGX TWN）1 分 K：GitHub 的 */5 cron 實測被節流成 3~5 小時一次，
// 前端只能拿到檔案裡已有的 bar，缺口補不回來 → 由 Worker cron 偵測並 dispatch（2026-10-23）
const HEAL_TWN_WORKFLOW = "twn-hist.yml";
const HEAL_GH_TWNHIST = "https://frankx180.github.io/txf-quote/data/twn-hist.json";
const HEAL_GH_TWNMETA = "https://frankx180.github.io/txf-quote/data/twn-hist-meta.json";
// 來源（TradingView 1 分 K）延遲約 10 分，門檻必須高於它才不會在健康狀態下自我觸發。
// 2026-10-27 實測：GitHub 的 */5 cron 被節流成 ~41 分鐘一次，原本 45 分門檻＝永遠落後 40 分以上
// （01:24 時資料還停在 01:00，玩股網已 4,046.75、我們 4,032.75）。
// 20 分＝來源 10 分延遲 + Pages 傳播 ~1 分 + 兩個檢查週期的餘裕。
// 實測穩態：剛補完 10～13 分、接近門檻約 20 分（＝約每 10 分鐘補一次，屬預期節奏不是異常）。
// dispatch 本身還有 HEAL_MIN_INTERVAL_MS（8 分）上限，實際不會高於 1 次／8 分。
const HEAL_TWN_STALE_MS = 10 * 60 * 1000; // 台股休市日（含颱風假）：富台是唯一價格來源；TV 本身延遲約 10 分，抓更密也追不上那 10 分，但能把堆疊從 20～30 分壓到 10～20 分
const HEAL_TWN_STALE_IDLE_MS = 30 * 60 * 1000; // 台股交易日：台指自己有資料，富台只需累積比值樣本
const HEAL_TWN_CHECK_MS = 2 * 60 * 1000;
// 同一根 bar 的重送間隔：實際由 triggerGithubWorkflow 的 HEAL_MIN_INTERVAL_MS（8 分）兜底，
// 這裡的 4 分只是多一層早退（尾巴不前進時最多 1 次／8 分，≈180 次／日，打不爆 API 額度）。
const HEAL_TWN_RETRY_MS = 4 * 60 * 1000;
// D1 故障時的保險（同一 isolate 內不重送）；主節流仍靠 D1
let _twnLastDispatchMs = 0;

// 回傳 null＝查無資料；回傳 { err: true }＝讀取失敗。
// 兩者必須分開，否則三道節流（檢查／重試／dispatch）在 D1 故障時全部變成放行，
// 會退回每分鐘 dispatch 的正回饋（Rumi 審查第二輪 MAJOR）。
async function getHealState(env, k) {
  if (!env.IMB_DB) return { err: true };
  try {
    await ensureSchema(env.IMB_DB);
    const row = await env.IMB_DB.prepare("SELECT ts, v FROM heal_state WHERE k = ?").bind(k).first();
    return row || null;
  } catch (_) {
    return { err: true };
  }
}
async function setHealState(env, k, ts, v) {
  if (!env.IMB_DB) return;
  try {
    await ensureSchema(env.IMB_DB);
    await env.IMB_DB.prepare("INSERT INTO heal_state (k, ts, v) VALUES (?, ?, ?) ON CONFLICT(k) DO UPDATE SET ts=excluded.ts, v=excluded.v").bind(k, ts, v || "").run();
  } catch (_) {}
}
async function logHeal(env, reason, ok, detail) {
  if (!env.IMB_DB) return;
  try {
    await ensureSchema(env.IMB_DB);
    await env.IMB_DB.prepare("INSERT INTO heal_log (ts, reason, ok, detail) VALUES (?, ?, ?, ?)").bind(Date.now(), reason || "", ok ? 1 : 0, (detail || "").slice(0, 800)).run();
  } catch (_) {}
}
function parseGhFetchedAt(s) {
  if (!s) return null;
  // format "2026-08-21 19:03:55" (Taipei) -> epoch
  const m = String(s).match(/(\d{4})-(\d{2})-(\d{2})\s+(\d{2}):(\d{2}):(\d{2})/);
  if (!m) {
    const d = new Date(s);
    return isNaN(d) ? null : d.getTime();
  }
  const y = Number(m[1]), mo = Number(m[2])-1, d = Number(m[3]), h = Number(m[4]), mi = Number(m[5]), sec = Number(m[6]);
  // Taipei is UTC+8, no DST
  return Date.UTC(y, mo, d, h, mi, sec) - 8 * 3600 * 1000;
}
async function fetchGhAgeMs() {
  try {
    const r = await fetch(HEAL_GH_SNAPSHOT, { headers: { "Cache-Control": "no-cache" }, cf: { cacheTtl: 0, cacheEverything: false }, signal: fetchSignal(UPSTREAM_TIMEOUT_MS) });
    if (!r.ok) return { ok: false, reason: "gh-fetch-" + r.status };
    const j = await r.json();
    const ts = parseGhFetchedAt(j.fetchedAt);
    if (!ts) return { ok: false, reason: "no-fetchedAt" };
    const age = Date.now() - ts;
    return { ok: true, ageMs: age, fetchedAt: j.fetchedAt, ts };
  } catch (e) {
    return { ok: false, reason: String((e && e.message) || e).slice(0, 120) };
  }
}
async function triggerGithubWorkflow(env, reason, opts) {
  const o = opts || {};
  const workflow = o.workflow || HEAL_WORKFLOW;
  const stateKey = o.stateKey || "last_dispatch";
  const token = (env.GH_TOKEN || env.GITHUB_TOKEN || env.GITHUB_FINE_PAT || "").trim();
  if (!token) return { ok: false, reason: "no-token", skipped: true };
  const now = Date.now();
  const last = await getHealState(env, stateKey);
  // D1 讀失敗回 { err: true }（ts 為 undefined → NaN 比較為 false → 直接放行＝fail-open，
  // 每分鐘 dispatch 的正回饋）。這裡必須 fail-closed，與 getHealState 的註解一致。
  if (last && last.err) return { ok: false, reason: "state-unavailable", skipped: true };
  if (last && now - Number(last.ts) < HEAL_MIN_INTERVAL_MS) {
    return { ok: false, reason: "rate-limited", lastTs: last.ts, skipped: true };
  }
  const api = `https://api.github.com/repos/${HEAL_GITHUB_OWNER}/${HEAL_GITHUB_REPO}/actions/workflows/${workflow}/dispatches`;
  try {
    const r = await fetch(api, {
      method: "POST",
      headers: {
        "Authorization": `Bearer ${token}`,
        "Accept": "application/vnd.github+json",
        "Content-Type": "application/json",
        "User-Agent": "txf-heal-worker/1.0",
      },
      body: JSON.stringify({ ref: "master" }),
    });
    // GH returns 204 on success; 401/404/422 etc are errors
    const detail = `status ${r.status} ${await r.text().catch(()=>"")}`.slice(0, 400);
    const ok = r.status === 204 || r.status === 201 || r.status === 200;
    await setHealState(env, stateKey, now, reason || "");
    await logHeal(env, reason || "manual", ok, detail);
    if (!ok) {
      // Fallback: try main branch
      if (r.status === 422 || r.status === 404) {
        const r2 = await fetch(api, {
          method: "POST",
          headers: {
            "Authorization": `Bearer ${token}`,
            "Accept": "application/vnd.github+json",
            "Content-Type": "application/json",
            "User-Agent": "txf-heal-worker/1.0",
          },
          body: JSON.stringify({ ref: "main" }),
        });
        const detail2 = `status ${r2.status} ${await r2.text().catch(()=>"")}`.slice(0, 400);
        const ok2 = r2.status === 204 || r2.status === 201 || r2.status === 200;
        await logHeal(env, (reason || "") + ":retry-main", ok2, detail2);
        return { ok: ok2, status: r2.status, detail: detail2, via: "main" };
      }
    }
    return { ok, status: r.status, detail, via: "master" };
  } catch (e) {
    const msg = String((e && e.message) || e).slice(0, 300);
    await logHeal(env, reason || "", false, msg);
    return { ok: false, reason: msg };
  }
}
// SGX 富台是否開盤（台北時間；不依賴伺服器時區）
// 日盤 08:45–13:45、夜盤 15:00–次日 05:05；週六 05:05 後至週一 08:45 前休
function twnSgxOpen(ms) {
  const d = new Date(ms + 8 * 3600000);
  const dow = d.getUTCDay(); // 0=週日
  const hm = d.getUTCHours() * 100 + d.getUTCMinutes();
  if (hm < 505) return dow >= 2 && dow <= 6; // 凌晨屬前一日夜盤（週一凌晨無盤）
  if (dow === 0 || dow === 6) return false;
  if (hm >= 845 && hm <= 1345) return true; // 日盤
  if (hm >= 1500) return true; // 夜盤
  return false; // 13:45–15:00 休
}
// 富台 1 分 K 缺口修復：檔案內容的尾端 bar 超過門檻沒動就 dispatch twn-hist.yml。
// 門檻兩段：台股休市日 10 分（HEAL_TWN_STALE_MS）、交易日 30 分（HEAL_TWN_STALE_IDLE_MS）；
// 一天約 140 個 commit 在推 235KB 的 JSON；每次抓都是 4000 根全量、本來就會自己補洞，
// 降頻只影響檔案新鮮度，不會漏資料。
// 不用 Last-Modified／ETag：Pages 任何一次部署（例如行情快照）都會把它們刷新，
// 檔案內容其實沒變，會被誤判成「很新」。只看內容裡最後一根的 timestamp。
// 台指休市（含颱風假）也要跑，因為 SGX 照常交易；
// 因此這個檢查必須放在 scheduled() 的 shouldRunCron 早退之前。
// 讀富台檔案的尾端時間：先讀小檔（幾十 bytes），沒有才退回完整檔（罕見）
async function twnTailMs() {
  const opt = {
    headers: { "Cache-Control": "no-cache" },
    cf: { cacheTtl: 0, cacheEverything: false },
    signal: fetchSignal(UPSTREAM_TIMEOUT_MS),
  };
  try {
    const m = await fetch(HEAL_GH_TWNMETA, opt);
    if (m.ok) {
      const j = await m.json();
      const t = Number(j && j.lastTs);
      if (Number.isFinite(t) && t > 0) return t * 1000;
    }
  } catch (_) {}
  const r = await fetch(HEAL_GH_TWNHIST, opt);
  if (!r.ok) return 0;
  const j = await r.json();
  const bars = (j && j.bars) || [];
  return bars.length ? Number(bars[bars.length - 1][0]) * 1000 : 0;
}
async function maybeHealTwn(env) {
  const now = Date.now();
  if (!twnSgxOpen(now)) return { ok: false, reason: "sgx-closed", skipped: true };
  // 台股休市日富台是唯一價格來源（前端休市模擬走勢要用最新點），交易日則只是背景補樣本
  await ensureHolidays();
  const staleMs = isHolidayMs(now) ? HEAL_TWN_STALE_MS : HEAL_TWN_STALE_IDLE_MS;
  const lastCheck = await getHealState(env, "last_twn_check");
  // D1 讀不到就不動手（fail-closed），寧可缺一次也不要繞過節流狂打
  if (lastCheck && lastCheck.err) return { ok: false, reason: "db-unreadable", skipped: true };
  if (lastCheck && now - Number(lastCheck.ts) < HEAL_TWN_CHECK_MS) {
    return { ok: false, reason: "check-throttled", skipped: true };
  }
  await setHealState(env, "last_twn_check", now, "twn");
  try {
    const tailMs = await twnTailMs();
    if (!tailMs) return { ok: false, reason: "no-tail", skipped: true };
    const age = now - tailMs;
    if (age < staleMs) return { ok: true, fresh: true, ageMs: age, staleMs, skipped: true };
    // 同一根 bar 已送過且未超過重試間隔 → 不再打（防穩態自我觸發）
    const prev = await getHealState(env, "last_twn_dispatch");
    if (prev && prev.err) return { ok: false, reason: "db-unreadable", skipped: true };
    if (prev && String(prev.v) === String(tailMs) && now - Number(prev.ts) < HEAL_TWN_RETRY_MS) {
      return { ok: false, reason: "no-advance", ageMs: age, skipped: true };
    }
    // D1 故障時的保險：同一 isolate 內一小時只送一次
    if (_twnLastDispatchMs && now - _twnLastDispatchMs < HEAL_TWN_RETRY_MS) {
      return { ok: false, reason: "local-throttle", ageMs: age, skipped: true };
    }
    const res = await triggerGithubWorkflow(env, `twn-hist age=${Math.round(age / 60000)}m`, {
      workflow: HEAL_TWN_WORKFLOW,
      stateKey: "last_dispatch_twn",
    });
    // 只有真的送出去才記；rate-limited／no-token 不算已送（Rumi 審查 MINOR）
    if (res && res.ok) {
      _twnLastDispatchMs = now;
      await setHealState(env, "last_twn_dispatch", now, String(tailMs));
    }
    return Object.assign({ ageMs: age, tailMs }, res);
  } catch (e) {
    return { ok: false, reason: String((e && e.message) || e).slice(0, 200) };
  }
}
async function maybeHealGithub(env, reason) {
  const now = Date.now();
  await ensureHolidays();
  if (!sessionOf(now)) return { ok: false, reason: "closed", skipped: true };
  // GitHub Actions 約每 5 分鐘更新；每 5 分鐘最多檢查一次，避免 cron 空轉。
  const lastCheck = await getHealState(env, "last_gh_check");
  // D1 讀不到時 fail-closed（與 maybeHealTwn 一致）：原本 Number(undefined)=NaN，
  // `NaN < 300000` 恆 false，等於「不節流」而不是「不動手」（2026-11-10 審查 S04#11）
  if (lastCheck && lastCheck.err) return { ok: false, reason: "db-unreadable", skipped: true };
  if (lastCheck && now - Number(lastCheck.ts) < 5 * 60 * 1000 && reason !== "frontend-stale" && reason !== "force") {
    return { ok: false, reason: "check-throttled", skipped: true };
  }
  await setHealState(env, "last_gh_check", now, reason || "");
  const ageRes = await fetchGhAgeMs();
  if (!ageRes.ok) {
    // 取不到 GH 資料時不誤觸發，但記錄
    return { ok: false, reason: ageRes.reason, skipped: true };
  }
  if (ageRes.ageMs < HEAL_STALE_MS) {
    return { ok: false, reason: "fresh", ageMs: ageRes.ageMs, fetchedAt: ageRes.fetchedAt, skipped: true };
  }
  // 確認 stale -> 觸發 dispatch（內有 8 分鐘 rate limit）
  const trig = await triggerGithubWorkflow(env, `${reason || "stale"} age=${Math.round(ageRes.ageMs/60000)}m fetchedAt=${ageRes.fetchedAt}`);
  return { ok: trig.ok, ageMs: ageRes.ageMs, fetchedAt: ageRes.fetchedAt, trigger: trig, stale: true };
}

// 14:30–15:30 盤後法人籌碼定案守護：若 GitHub 上的 uncovered.json 還不是今日，每 2 分鐘自動 trigger 一次 Actions
async function maybeHealChips(env) {
  const now = Date.now();
  const p = twParts(now);
  const wd = TW_WD_FMT.format(new Date(now));
  if (wd === "Sat" || wd === "Sun") return { ok: false, reason: "weekend", skipped: true };
  if (p.hm < 1430 || p.hm > 1530) return { ok: false, reason: "outside-chips-window", skipped: true };

  const lastDispatch = await getHealState(env, "last_chips_dispatch");
  if (lastDispatch && now - Number(lastDispatch.ts) < HEAL_CHIPS_INTERVAL_MS) {
    return { ok: false, reason: "chips-throttled", skipped: true };
  }

  const todayDash = `${p.y}-${p.mo}-${p.d}`;
  const todayYmd = `${p.y}${p.mo}${p.d}`;
  try {
    const r = await fetch(HEAL_GH_UNCOVERED, { headers: { "Cache-Control": "no-cache" }, cf: { cacheTtl: 0, cacheEverything: false } });
    if (!r.ok) return { ok: false, reason: "gh-unc-fetch-" + r.status };
    const j = await r.json();
    const uncDate = String(j.date || "").replace(/\D/g, "");
    const hist0 = (j.history || [{}])[0] || {};
    const hist0Date = String(hist0.date || "").replace(/\D/g, "");
    const isAllZero = (
      Number(hist0.foreign || 0) === 0 &&
      Number(hist0.trust || 0) === 0 &&
      Number(hist0.dealer || 0) === 0
    );
    const hasTodayChips = (uncDate === todayYmd || hist0Date === todayYmd) && hist0.foreign != null && !isAllZero;
    if (hasTodayChips) {
      return { ok: true, reason: "chips-ready", date: uncDate, skipped: true };
    }
    // 未定案 -> 觸發 GitHub Actions，間隔 2 分鐘
    await setHealState(env, "last_chips_dispatch", now, "chips-pending");
    const trig = await triggerGithubWorkflow(env, `chips-pending date=${uncDate} target=${todayYmd}`);
    return { ok: trig.ok, trigger: trig, pendingDate: uncDate, targetDate: todayYmd };
  } catch (e) {
    return { ok: false, reason: String((e && e.message) || e).slice(0, 120) };
  }
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
    // 冪等寫入 traffic_sid，避免全表重複 sid
    const ins = await env.IMB_DB.prepare(
      "INSERT INTO traffic_sid (day_key, sid) VALUES (?, ?) ON CONFLICT(day_key, sid) DO NOTHING"
    )
      .bind(dayKey, sid)
      .run();
    if (ins && ins.meta && ins.meta.changes > 0) {
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

// 記憶體快取：月/總計統計每 5 分鐘刷新一次，避免每個請求重複計算
let _cachedStatsAgg = { ts: 0, data: {} };

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

  // 月/總計匯總：每 5 分鐘內走記憶體快取，直接以 traffic_day 的 pv, uv 加總（不掃描 traffic_sid）
  const aggKey = site + ":" + monthKey;
  let agg = _cachedStatsAgg.data[aggKey];
  if (!agg || now - _cachedStatsAgg.ts > 5 * 60 * 1000) {
    const monthLike = site === "txf" ? monthKey + "%" : site + ":" + monthKey + "%";
    const monthRow = await env.IMB_DB.prepare(
      "SELECT COALESCE(SUM(pv), 0) AS pv, COALESCE(SUM(uv), 0) AS uv FROM traffic_day WHERE day_key LIKE ?"
    )
      .bind(monthLike)
      .first();
    let totalRow;
    if (site === "txf") {
      totalRow = await env.IMB_DB.prepare(
        "SELECT COALESCE(SUM(pv), 0) AS pv, COALESCE(SUM(uv), 0) AS uv FROM traffic_day WHERE day_key NOT LIKE '%:%'"
      ).first();
    } else {
      totalRow = await env.IMB_DB.prepare(
        "SELECT COALESCE(SUM(pv), 0) AS pv, COALESCE(SUM(uv), 0) AS uv FROM traffic_day WHERE day_key LIKE ?"
      )
        .bind(site + ":%")
        .first();
    }
    agg = {
      monthPv: Number(monthRow && monthRow.pv) || 0,
      monthUv: Number(monthRow && monthRow.uv) || 0,
      totalPv: Number(totalRow && totalRow.pv) || 0,
      totalUv: Number(totalRow && totalRow.uv) || 0,
    };
    _cachedStatsAgg.data[aggKey] = agg;
    _cachedStatsAgg.ts = now;
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
    monthPv: agg.monthPv,
    monthUv: agg.monthUv,
    totalPv: agg.totalPv,
    totalUv: agg.totalUv,
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
  // 單價不成立 K 棒：無既有當根（應先由 chart 寫入）→ 不建棒，只准更新已有棒的 close／高低
  if (!prev || prev.c == null) {
    return { ok: false, reason: "no-bar", dayKey, sess, slot };
  }
  const o = prev.o != null ? prev.o : px;
  const h = Math.max(prev.h != null ? prev.h : px, px);
  const l = Math.min(prev.l != null ? prev.l : px, px);
  const c = px;
  const v = Number(prev.v) || 0;
  const src = prev.source === "chart" ? "chart" : (source || "quote");
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

function barsFromChartJson(chart, limitTail = 0) {
  const tsList = (chart && chart.timestamp) || [];
  const q0 = (((chart && chart.indicators) || {}).quote || [{}])[0] || {};
  const opens = q0.open || [];
  const highs = q0.high || [];
  const lows = q0.low || [];
  const closes = q0.close || [];
  const vols = q0.volume || [];
  const out = [];
  const startIdx = limitTail > 0 && tsList.length > limitTail ? tsList.length - limitTail : 0;
  for (let i = startIdx; i < tsList.length; i++) {
    const c = closes[i];
    if (c == null) continue;
    let tsSec = +tsList[i];
    let ts = tsSec < 1e12 ? tsSec * 1000 : tsSec;
    // Yahoo 夜盤標次交易日（平日 +1 天、週五 +2~3 天、連假如中秋 +4 天、春節最多 +10 天）：逐日回移到不晚於現在
    // SSOT: 01_Docs/Yahoo-台指期K線日期慣例.md
    const nowLim = Date.now() + 120000;
    let shift = 0;
    while (ts > nowLim && shift < 10) {
      ts -= 86400000;
      shift++;
    }
    if (ts > nowLim) continue;
    // 夜盤開盤日須為營業日：只退到「不晚於現在」在連假／週末會落在假日夜（例：10/8 夜盤 → 10/9 假日夜）
    // 與前端 fixYahooFutureTs、scripts/fetch_minute.py 同規則
    for (let k = 0; k < 10; k++) {
      const hm = twParts(ts).hm;
      if (!(hm >= 1500 || hm < 510) || nightLiveMs(ts)) break;
      ts -= 86400000;
    }
    const cNum = +c;
    if (!(cNum >= 10000 && cNum <= 80000)) continue;
    const sess = sessionOf(ts);
    if (!sess) continue;
    const dayKey = tradingDayKey(ts);
    const slot = minuteSlot(ts);
    const o = opens[i] != null ? +opens[i] : +c;
    const h = highs[i] != null ? +highs[i] : +c;
    const l = lows[i] != null ? +lows[i] : +c;
    const v = vols[i] != null ? Math.round(+vols[i] * 1000) : 0;
    const prev = out.length ? out[out.length - 1] : null;
    if (prev && prev.session === sess && Math.abs(slot - prev.t) <= 15 * 60000 && Math.abs(cNum - prev.c) >= 400) continue;
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

async function backfillChart1m(env, full = false) {
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
        signal: fetchSignal(UPSTREAM_TIMEOUT_MS),
      });
      if (!r.ok) {
        lastErr = "yahoo-chart-fail:" + r.status;
        continue;
      }
      const chart = await r.json();
      // 非 full 模式只解析尾端 12 根 K 棒，省去 500 根的時區迴圈運算 (CPU 從 6ms 降至 0.1ms)
      const all = barsFromChartJson(chart, full ? 0 : 12);
      const dayKey = tradingDayKey(Date.now());
      const today = all.filter((b) => b.dayKey === dayKey);
      if (!today.length) return { ok: true, n: 0, dayKey, raw: all.length, today: 0 };
      
      let targetBars = full ? today : today.slice(-5);
      const n = await upsertPriceBars(env, targetBars);
      return { ok: true, n, dayKey, raw: all.length, today: today.length, target: targetBars.length, attempt: attempt + 1 };
    } catch (e) {
      lastErr = String((e && e.message) || e);
    }
  }
  return { ok: false, reason: lastErr || "chart-fail" };
}

// 記憶體快取：loadPricePack 與 loadPack (TTL 4s，同 isolate 併發防穿透)
const _pricePackCache = new Map();
const _imbPackCache = new Map();

async function loadPricePack(env, dayKey) {
  const empty = { dayKey, day: [], night: [], updatedAt: null };
  if (!env.IMB_DB) return empty;
  const now = Date.now();
  const cached = _pricePackCache.get(dayKey);
  if (cached && now - cached.ts < 4000) {
    return cached.data;
  }
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
    const pack = {
      dayKey,
      day,
      night,
      updatedAt: updatedAt ? new Date(updatedAt).toISOString() : null,
    };
    _pricePackCache.set(dayKey, { ts: now, data: pack });
    return pack;
  } catch (e) {
    return empty;
  }
}

async function lastImbBar(env, dayKey, sess) {
  if (!env.IMB_DB || !dayKey || !sess) return null;
  try {
    await ensureSchema(env.IMB_DB);
    return await env.IMB_DB.prepare(
      "SELECT d, inn, outv FROM imb WHERE day_key = ? AND session = ? ORDER BY t DESC LIMIT 1"
    )
      .bind(dayKey, sess)
      .first();
  } catch (e) {
    return null;
  }
}

async function appendImb(env, rows, nowMs) {
  if (!env.IMB_DB) return { ok: false, reason: "no-db" };
  const sess = sessionOf(nowMs);
  if (!sess) return { ok: false, reason: "closed" };
  const rawW = Array.isArray(rows) && rows.length
    ? (rows.find((x) => x && x.symbol === "WTX&") || rows[0])
    : null;
  if (!yahooBookFresh(rawW, nowMs)) return { ok: false, reason: "stale-source-day" };
  const w = extractWtx(rows);
  const dayKey = tradingDayKey(nowMs);
  const slot = minuteSlot(nowMs);
  let inn = w && w.inn;
  let outv = w && w.outv;
  // Yahoo 抽風回 0／缺欄：承接上一分鐘，避免夜盤副圖斷點（不可回補歷史，只能把當根補上）
  if (!(inn > 0) || !(outv > 0)) {
    const prev = await lastImbBar(env, dayKey, sess);
    if (!prev || !(prev.inn > 0) || !(prev.outv > 0)) {
      return { ok: false, reason: w ? "bad-imb" : "no-imb" };
    }
    inn = prev.inn;
    outv = prev.outv;
  }
  const d = outv - inn;
  await ensureSchema(env.IMB_DB);
  await env.IMB_DB.prepare(
    "INSERT INTO imb (day_key, session, t, d, inn, outv, ts) VALUES (?, ?, ?, ?, ?, ?, ?) " +
      "ON CONFLICT(day_key, session, t) DO UPDATE SET d=excluded.d, inn=excluded.inn, outv=excluded.outv, ts=excluded.ts"
  )
    .bind(dayKey, sess, slot, d, inn, outv, nowMs)
    .run();
  return { ok: true, dayKey, sess, d, slot };
}

// ── 大小微台內外盤（2026-11-02 老公指定）──
// 2026-11-05 現況：本函式已降級為「手動回填／最後手段」，主線是下面的 syncImb3Yahoo。
// 為何不用 quote 的 totalBidMatch／totalAskMatch：實測 TXF 2026-11-02 日盤 vol 52,064、bid 38,869、ask 38,716，
// 兩者相加是成交量的 1.5 倍（那是買方／賣方委託各自的成交筆數，每筆雙邊各計一次），不是內外盤口數。
// 唯一可行：抳 trades 逐筆，用 bid/ask（夾在中間用 tick rule）分類，再換算 1/20 大台口（TXF×20、MXF×5、TMF×1）。
const FUGLE_MD = "https://api.fugle.tw/marketdata/v1.0/futopt/intraday/";
const IMB3_SYMS = [["TXF1!", 20], ["MXF1!", 5], ["TMF1!", 1]];
const IMB3_PAGE = 500;
const IMB3_MAX_PAGES = 6;

const _imb3SchemaReady = new WeakSet();

async function ensureImb3Schema(db) {
  if (_imb3SchemaReady.has(db)) return;
  await db.prepare("CREATE TABLE IF NOT EXISTS imb3 (day_key TEXT NOT NULL, session TEXT NOT NULL, t INTEGER NOT NULL, d20 REAL NOT NULL, in20 REAL NOT NULL, out20 REAL NOT NULL, n INTEGER, ts INTEGER, PRIMARY KEY (day_key, session, t))").run();
  await db.prepare("CREATE TABLE IF NOT EXISTS imb3_cur (day_key TEXT NOT NULL, session TEXT NOT NULL, sym TEXT NOT NULL, serial REAL NOT NULL, in20 REAL NOT NULL, out20 REAL NOT NULL, last_px REAL, last_side INTEGER, upd INTEGER, PRIMARY KEY (day_key, session, sym))").run();
  // 2026-11-02：品質欄位（ok／partial）——自動對帳發現差距過大時標記，前端可提示
  try { await db.prepare("ALTER TABLE imb3 ADD COLUMN q TEXT").run(); } catch (e) {}
  // 2026-11-04：持久化截斷標記（某檔曾超過分頁上限未接回時 gap=1，直到回填才歸 0）
  try { await db.prepare("ALTER TABLE imb3_cur ADD COLUMN gap INTEGER DEFAULT 0").run(); } catch (e) {}
  _imb3SchemaReady.add(db);
}
async function fubonToken(env) {
  try {
    const row = await env.IMB_DB.prepare("SELECT tok FROM fubon_tok WHERE id = 1").first();
    return row && row.tok ? row.tok : null;
  } catch (e) {
    return null;
  }
}

// 內外盤方向：成交價打到賣價＝外盤（+1）、打到買價＝內盤（-1）、夾在中間用比上一筆漲跌。
// 2026-11-02 修：bid === ask（鎖漲跌停）時不可先命中 ask 全歸外盤，退回 tick rule。
function imb3Side(px, bid, ask, prevPx, prevSide) {
  if (bid && ask && bid === ask) {
    if (prevPx != null && px !== prevPx) return px > prevPx ? 1 : -1;
    return prevSide || 0;
  }
  if (ask && px >= ask) return 1;
  if (bid && px <= bid) return -1;
  if (prevPx != null && px !== prevPx) return px > prevPx ? 1 : -1;
  return prevSide || 0;
}

// 每分鐘跑一次：每一檔只抓最新頁（由新到舊），用 prevSerial 去重與續接
// 2026-11-04 修：不可在單頁迴圈內把 serial 更新為 p=0 的最大值，否則 p>=1（較舊頁）永遠 fresh=[] 提前 break，
//   導致爆量時 MXF/TMF 只抓到第一頁 500 筆而漏單；改為收集完所有頁再一併按時間正序分類與更新 serial，
//   且若抓滿 IMB3_MAX_PAGES 仍未接回 prevSerial（仍有斷層），將該分鐘標 q='partial'。
async function syncImb3(env, nowMs) {
  if (!env.IMB_DB) return { ok: false, reason: "no-db" };
  const sess = sessionOf(nowMs);
  if (!sess) return { ok: false, reason: "closed" };
  const tok = await fubonToken(env);
  if (!tok) return { ok: false, reason: "no-token" };
  const dayKey = tradingDayKey(nowMs);
  await ensureImb3Schema(env.IMB_DB);
  const qs = sess === "night" ? "session=afterhours" : "";
  let tin = 0, tout = 0, added = 0, pages = 0, err = null, txGross = 0, truncated = false;
  for (let i = 0; i < IMB3_SYMS.length; i++) {
    const sym = IMB3_SYMS[i][0], w = IMB3_SYMS[i][1];
    const cur = await env.IMB_DB.prepare(
      "SELECT serial, in20, out20, last_px, last_side, gap FROM imb3_cur WHERE day_key=? AND session=? AND sym=?"
    ).bind(dayKey, sess, sym).first();
    const prevSerial = cur ? Number(cur.serial) : 0;
    let serial = prevSerial;
    let in20 = cur ? Number(cur.in20) : 0;
    let out20 = cur ? Number(cur.out20) : 0;
    let lastPx = cur && cur.last_px != null ? Number(cur.last_px) : null;
    let lastSide = cur && cur.last_side != null ? Number(cur.last_side) : 0;
    let symGap = cur && Number(cur.gap) ? 1 : 0;
    const allFresh = [];
    let symReachedBoundary = false;
    let symErr = null;
    for (let p = 0; p < IMB3_MAX_PAGES; p++) {
      const u = FUGLE_MD + "trades/" + encodeURIComponent(sym) + "?limit=" + IMB3_PAGE + "&offset=" + (p * IMB3_PAGE) + (qs ? "&" + qs : "");
      let res;
      try {
        res = await fetch(u, { headers: { "X-SDK-TOKEN": tok, "User-Agent": "txf-imb3/1" },
          // 富邦一 hang，這個請求就拖到邊緣斷線（全檔唯一沒 timeout 的長鏈）
          signal: fetchSignal(UPSTREAM_TIMEOUT_MS) });
      } catch (e) { symErr = "fetch"; err = symErr; break; }
      if (res.status === 401 || res.status === 403) return { ok: false, reason: "token-expired" };
      if (!res.ok) { symErr = "http-" + res.status; err = symErr; break; }
      let arr = [];
      try { arr = ((await res.json()) || {}).data || []; } catch (e) { symErr = "json"; err = symErr; break; }
      pages++;
      if (!arr.length) { symReachedBoundary = true; break; }
      const fresh = arr.filter((x) => x && typeof x.serial === "number" && x.serial > prevSerial);
      if (!fresh.length) { symReachedBoundary = true; break; }
      for (const x of fresh) allFresh.push(x);
      if (fresh.length < arr.length) { symReachedBoundary = true; break; } // 已接到上次的邊界
      if (arr.length < IMB3_PAGE) { symReachedBoundary = true; break; }    // 到底了
    }
    if (symErr) {
      // 中途 HTTP／JSON 失敗（尚未接回 prevSerial）：不推進該商品的 serial 與累計值，讓下一分鐘重抓補齊，避免永久跳號
      truncated = true;
    } else {
      if (!symReachedBoundary) {
        symGap = 1;
        truncated = true;
      }
      if (allFresh.length > 0) {
        // offset 分頁跨頁邊界可能有重複 serial，先以 serial 去重再按時間正序分類
        const seenSerial = new Set();
        const uniq = [];
        for (const x of allFresh) {
          if (!seenSerial.has(x.serial)) {
            seenSerial.add(x.serial);
            uniq.push(x);
          }
        }
        uniq.sort((a, b) => (Number(a.time) - Number(b.time)) || (a.serial - b.serial));
        for (const x of uniq) {
          const side = imb3Side(x.price, x.bid, x.ask, lastPx, lastSide);
          lastPx = x.price;
          if (side) lastSide = side;
          const v = (Number(x.size) || 0) * w;
          if (side > 0) out20 += v;
          else if (side < 0) in20 += v;
          if (x.serial > serial) serial = x.serial;
          added++;
        }
      }
      await env.IMB_DB.prepare(
        "INSERT INTO imb3_cur (day_key, session, sym, serial, in20, out20, last_px, last_side, upd, gap) VALUES (?,?,?,?,?,?,?,?,?,?) " +
        "ON CONFLICT(day_key, session, sym) DO UPDATE SET serial=excluded.serial, in20=excluded.in20, out20=excluded.out20, last_px=excluded.last_px, last_side=excluded.last_side, upd=excluded.upd, gap=excluded.gap"
      ).bind(dayKey, sess, sym, serial, in20, out20, lastPx, lastSide, nowMs, symGap).run();
    }
    if (symGap) truncated = true;
    tin += in20;
    tout += out20;
    if (i === 0) txGross = in20 + out20; // TXF 累計（1/20 大台口）→ 供自動對帳
  }
  // 三檔全失敗（例如被 429 限流）時不得寫入 0 值污染序列
  if (added === 0 && tin === 0 && tout === 0) {
    return { ok: false, reason: err || "empty" };
  }
  // 2026-11-04 方案 A 安全網：富邦逐筆是備援，但它的游標若停在好幾小時前，
  // 一次只能補 6 頁（3000 筆），算出來的累計值會遠小於 Yahoo 主線已經落地的值。
  // 這種情況不准覆寫，否則前端曲線會倒退跳針。
  try {
    const prevRow = await env.IMB_DB.prepare(
      "SELECT in20, out20 FROM imb3 WHERE day_key=? AND session=? ORDER BY t DESC LIMIT 1"
    ).bind(dayKey, sess).first();
    const prevSum = prevRow ? (Number(prevRow.in20) || 0) + (Number(prevRow.out20) || 0) : 0;
    if (prevSum > 0 && tin + tout < prevSum * 0.95) {
      return { ok: false, reason: "would-regress", prevSum: Math.round(prevSum), now: Math.round(tin + tout) };
    }
  } catch (e) {}
  // 2026-11-02 自動對帳：用期交所 MIS 的當盤總成交量當尺，比對 TXF 逐筆累計量。
  // 2026-11-04 補：若任一商品抓滿 IMB3_MAX_PAGES 仍未接回 prevSerial（truncated），同樣標 partial。
  let q = truncated ? "partial" : "ok";
  let qDiff = 0;
  try {
    const mis = await fetchMisQuote();
    const mv = mis && mis.vol;
    if (mv > 0 && txGross > 0) {
      const ours = txGross / 20;
      qDiff = Math.round(ours - mv);
      if (Math.abs(qDiff) / mv > 0.01) q = "partial";
    }
  } catch (e) {}
  const slot = minuteSlot(nowMs);
  await env.IMB_DB.prepare(
    "INSERT INTO imb3 (day_key, session, t, d20, in20, out20, n, ts, q) VALUES (?,?,?,?,?,?,?,?,?) " +
    "ON CONFLICT(day_key, session, t) DO UPDATE SET d20=excluded.d20, in20=excluded.in20, out20=excluded.out20, n=excluded.n, ts=excluded.ts, q=excluded.q"
  ).bind(dayKey, sess, slot, tout - tin, tin, tout, added, nowMs, q).run();
  return { ok: true, dayKey, sess, in20: tin, out20: tout, added, pages, err, q, qDiff };
}

// ── 2026-11-04 方案 A（老公拍板）：內外盤主線改走 Yahoo 一次抓齊大小微台 ──
// Yahoo StockServices 的 inMarket／outMarket 是期交所端「當盤開盤累計」的狀態值，
// 爆量或斷線都不會漏單（富邦逐筆是增量流，斷一次就得靠回填補洞）。
// 單位沿用 imb3 的「1/20 大台口」刻度：TXF×20 + MXF×5 + TMF×1。
const IMB3_Y_SYMS = [["WTX&", 20], ["WMT&", 5], ["WTM&", 1]];

// Yahoo 的 inMarket／outMarket 是「千口」小數（實測 33.191＝33,191 口），這裡一律換算成口。
function imb3YNum(v) {
  if (v == null || v === "") return null;
  if (typeof v === "object" && v.raw != null) v = v.raw;
  const n = Number(String(v).replace(/,/g, ""));
  if (!Number.isFinite(n)) return null;
  return Math.abs(n) < 1e6 ? Math.round(n * 1000) : Math.round(n);
}

// 日盤時 Yahoo 的 inMarket／outMarket 是「前一夜盤＋當日日盤」的全天累計，
// 要扣掉同一交易日的前一夜盤末值，才會與夜盤一致地從 0 起算。
async function imb3YahooNightBase(db, dayKey) {
  try {
    const row = await db
      .prepare(
        "SELECT in20, out20 FROM imb3 WHERE session='night' AND day_key < ? ORDER BY day_key DESC, t DESC LIMIT 1"
      )
      .bind(dayKey)
      .first();
    if (!row) return { in20: 0, out20: 0 };
    return { in20: Number(row.in20) || 0, out20: Number(row.out20) || 0 };
  } catch (e) {
    return { in20: 0, out20: 0 };
  }
}

async function syncImb3Yahoo(env, rows, nowMs) {
  if (!env.IMB_DB) return { ok: false, reason: "no-db" };
  const sess = sessionOf(nowMs);
  if (!sess) return { ok: false, reason: "closed" };
  const list = Array.isArray(rows) ? rows : [];
  const wtx = list.find((x) => x && x.symbol === "WTX&");
  // 來源日≠今日交易鍵＝假日殘值，一律不寫，改走富邦逐筆
  if (!yahooBookFresh(wtx, nowMs)) {
    // 2026-11-05：備援改為不寫入。富邦逐筆自己分類會比官方多約 2%，
    // 一旦它插進 Yahoo 序列，累計值就跳升、下一分鐘 Yahoo 回來又掉下去
    // （實測造成 01:51→01:52 的斷崖）。寧可少一分鐘，也不要污染整條累計曲線。
    return { ok: false, reason: "yahoo-stale", skippedWrite: true };
  }
  let in20 = 0, out20 = 0, got = 0;
  const missing = [];
  for (const pair of IMB3_Y_SYMS) {
    const sym = pair[0], w = pair[1];
    const r = list.find((x) => x && x.symbol === sym);
    const i0 = r ? imb3YNum(r.inMarket) : null;
    const o0 = r ? imb3YNum(r.outMarket) : null;
    if (i0 == null || o0 == null || i0 + o0 <= 0) { missing.push(sym); continue; }
    in20 += i0 * w;
    out20 += o0 * w;
    got++;
  }
  // 任一檔缺值（未開盤／欄位停更）→ 退回已修好分頁的富邦逐筆，不寫半套
  if (got < IMB3_Y_SYMS.length) {
    // 同上：不拿富邦補寫，避免兩種口徑混進同一條累計
    return { ok: false, reason: "yahoo-missing", yahooMissing: missing, skippedWrite: true };
  }
  const dayKey = tradingDayKey(nowMs);
  await ensureImb3Schema(env.IMB_DB);
  let q = "ok";
  if (sess === "day") {
    const base = await imb3YahooNightBase(env.IMB_DB, dayKey);
    const baseSum = base.in20 + base.out20;
    if (baseSum > 0) {
      if (in20 + out20 >= baseSum) {
        in20 -= base.in20;
        out20 -= base.out20;
      } else {
        // 累計值還沒追上基期＝Yahoo 剛換盤或基期異常，寧可晚一分鐘也不寫暴增值
        return { ok: false, reason: "base-gt-now", dayKey, sess };
      }
    } else {
      // 查不到前一夜盤基期（Worker 昨夜沒跑到、或表被清）：不能靜默把全天累計寫進日盤，標待確認
      q = "partial";
    }
  }
  if (in20 + out20 <= 0) return { ok: false, reason: "yahoo-empty" };
  // 同一盤別內累計值必須單調不減；掉了＝Yahoo 端換盤或抖動，標 partial 讓前端顯示待確認
  try {
    const prev = await env.IMB_DB.prepare(
      "SELECT in20, out20 FROM imb3 WHERE day_key=? AND session=? AND t<? ORDER BY t DESC LIMIT 1"
    ).bind(dayKey, sess, minuteSlot(nowMs)).first();
    const pv = prev ? (Number(prev.in20) || 0) + (Number(prev.out20) || 0) : 0;
    if (pv > 0 && in20 + out20 < pv * 0.999) q = "partial";
  } catch (e) {}
  const slot = minuteSlot(nowMs);
  await env.IMB_DB.prepare(
    "INSERT INTO imb3 (day_key, session, t, d20, in20, out20, n, ts, q) VALUES (?,?,?,?,?,?,?,?,?)" +
    " ON CONFLICT(day_key, session, t) DO UPDATE SET d20=excluded.d20, in20=excluded.in20, out20=excluded.out20, n=excluded.n, ts=excluded.ts, q=excluded.q"
  ).bind(dayKey, sess, slot, out20 - in20, in20, out20, 0, nowMs, q).run();
  return { ok: true, source: "yahoo", dayKey, sess, in20, out20, q, got };
}

async function loadPack(env, dayKey) {
  const empty = { dayKey, day: [], night: [], updatedAt: null };
  if (!env.IMB_DB) return empty;
  const now = Date.now();
  const cached = _imbPackCache.get(dayKey);
  if (cached && now - cached.ts < 4000) {
    return cached.data;
  }
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
    const pack = {
      dayKey,
      day,
      night,
      updatedAt: updatedAt ? new Date(updatedAt).toISOString() : null,
    };
    _imbPackCache.set(dayKey, { ts: now, data: pack });
    return pack;
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
    signal: fetchSignal(UPSTREAM_TIMEOUT_MS),
  });
  const body = await r.text();
  return { ok: r.ok, status: r.status, body };
}

async function fetchYahooQuoteRetry(tries) {
  const n = tries || 3;
  let last = { ok: false, status: 0, body: "" };
  for (let i = 0; i < n; i++) {
    try {
      last = await fetchYahooQuote();
      if (last.ok && last.body) return last;
    } catch (e) {
      last = { ok: false, status: 0, body: "", error: String((e && e.message) || e) };
    }
    if (i + 1 < n) await new Promise((r) => setTimeout(r, 250 * (i + 1)));
  }
  return last;
}

/** 無人看盤也寫：Cron／poll 觸發（CF 最短 1 分；本機守護可 5 秒） */
async function pollAndStore(env, opts) {
  const nowMs = Date.now();
  if (!sessionOf(nowMs)) return { skipped: true, reason: "closed" };
  if (nowMs < _yahooBackoffUntil) return { skipped: true, reason: "yahoo-backoff" };

  const out = {};
  const { ok, body, status } = await fetchYahooQuoteRetry(2);
  if (!ok) {
    _yahooFailCount++;
    if (_yahooFailCount >= 2) {
      _yahooBackoffUntil = nowMs + 5 * 60 * 1000;
    }
    if (nowMs < _dormantUntil) return { skipped: true, reason: "dormant-holiday" };
    out.skippedQuote = true;
    out.reason = "yahoo-fail";
    out.status = status || 0;
  } else {
    _yahooFailCount = 0;
    _yahooBackoffUntil = 0;
    try {
      const rows = JSON.parse(body);

      // 颱風假熔斷：開盤 15 分後「真的沒有交易跡象」才睡。
      // Yahoo 夜盤常標 marketStatus=close，15:15 起會誤睡到 05:01（副圖 16:00 起斷點；2026-09-30）。
      const p = twParts(nowMs);
      const hm = p.hm;
      const isPast15m = (hm >= 900 && hm <= 1345) || (hm >= 1515 || hm < 500);
      const rawW = Array.isArray(rows) && rows.length
        ? (rows.find((x) => x && x.symbol === "WTX&") || rows[0])
        : null;
      if (looksLikeLiveQuote(rawW, nowMs)) {
        _dormantUntil = 0;
      } else if (nowMs < _dormantUntil) {
        return { skipped: true, reason: "dormant-holiday" };
      } else if (isPast15m && rawW) {
        const statusClose = rawW.marketStatus === "close";
        const regTime = rawW.regularMarketTime ? Date.parse(rawW.regularMarketTime) : 0;
        const isStaleOver1h = regTime > 0 && (nowMs - regTime > 60 * 60 * 1000);
        const sess = sessionOf(nowMs);
        const closeHalt = sess === "day" && statusClose;
        if (closeHalt || isStaleOver1h) {
          const endMs = sessionEndMs(nowMs);
          if (endMs > nowMs) {
            _dormantUntil = endMs;
            console.log(`[CircuitBreaker] 休市/颱風假，休眠至 ${new Date(endMs).toISOString()}`);
            setHealState(env, "dormant_until", endMs, "holiday-breaker").catch(() => {});
            return { ok: true, dormant: true, until: endMs, reason: "holiday-breaker" };
          }
        }
      }

      out.imb = await appendImb(env, rows, nowMs);
      // 2026-11-04 方案 A（老公拍板）：主線改用 Yahoo 一次抓齊大小微台（WTX&+WMT&+WTM&）寫入 imb3。
      // Yahoo 的 inMarket/outMarket 是期交所端開盤累計總值（狀態值），永遠不漏單。
      // 2026-11-05 更正宣稱：富邦逐筆 syncImb3() 仍留著，但它**不是隨時可用的保險**——
      // 它靠 D1 的 fubon_tok，只有人工跑 scripts/imb3_backfill.py 時才會刷新；
      // token 過期後 fallback 會直接 401。它的真實用途是「手動回填重建歷史序列」。
      // 一定要自己吃錯：imb3 寫入失敗不可拖垮同一個 try 裡的即時報價（out.px）。
      try {
        out.imb3y = await syncImb3Yahoo(env, rows, nowMs);
      } catch (e) {
        out.imb3y = { ok: false, reason: "throw", error: String((e && e.message) || e) };
      }
      const w = extractWtx(rows);
      out.px = await appendPricePx(env, w && w.px, nowMs, "quote");
    } catch (e) {
      out.skippedQuote = true;
      out.reason = "quote-parse-fail";
      out.error = String((e && e.message) || e);
    }
  }
  // chart 回填放後面：失敗也不擋內外盤
  if (opts && opts.chart) {
    try {
      out.chart = await backfillChart1m(env);
    } catch (e) {
      out.chart = { ok: false, reason: String((e && e.message) || e) };
    }
  }
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
    signal: fetchSignal(UPSTREAM_TIMEOUT_MS),
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
    fetch("https://openapi.taifex.com.tw/v1/DailyMarketReportFut", { headers: hdr, signal: fetchSignal(UPSTREAM_TIMEOUT_MS) }),
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
    signal: fetchSignal(UPSTREAM_TIMEOUT_MS),
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
  const wd = TW_WD_FMT.format(new Date(ms));
  if (wd === "Sat" || wd === "Sun") return false;
  const hm = p.hm;
  // 官方約兩點多公布；14:30 起抓，最慢 15:00 前要更新完
  if (hm >= 1430 && hm <= 2000) return true;
  if (hm >= 620 && hm <= 1000) return true;
  return false;
}

function twDateStr(ms) {
  const p = twParts(ms);
  return p.y + p.mo + p.d;
}

// 上一個工作日（六日往前找；國定假日的近似——誤判頂多多打一次網站下載，無害且自癒）
function lastWorkdayTw(ms) {
  const fmt = TW_WD_FMT;
  for (let i = 1; i <= 4; i++) {
    const t = ms - i * 86400000;
    const wd = fmt.format(new Date(t));
    if (wd !== "Sat" && wd !== "Sun") return twDateStr(t);
  }
  return twDateStr(ms - 86400000);
}

// 判斷 OpenAPI 給的「最新交易日」是否落後預期：落後 → 網站 14 天補抓
// 14:30–20:00 視窗（今天資料應公布）→ 預期今天；06:20–10:00（補昨晚）→ 預期上一個工作日
function tmfOpenApiStale(ms, openDate, prev) {
  if (!openDate || openDate.length !== 8) return "openapi-bad-date";
  const p = twParts(ms);
  let expected = twDateStr(ms);
  if (p.hm >= 620 && p.hm <= 1000) {
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

async function handleTwnQuote(request, env, ctx) {
  const url = new URL(request.url);
  const cacheKey = new Request(url.origin + "/?kind=twn", request);
  const cache = caches.default;
  let cached = await cache.match(cacheKey);
  if (cached) return cached;

  try {
    // 富台價與換算係數已全面自建：分時序列走 HiStock，ratio/rateAdj/dayClose 走 data/twn-ratio.json（前端自讀）。
    // 原本等的那支 GAS 實測 3.4~65s（極不穩），是首屏卡 10 秒的元凶 → 已自關鍵路徑移除；gas 固定 null 供前端回退鏈。
    // 抓取 HiStock 分時序列 (若超時則平滑降級)
    let points = [];
    let latestPrice = 0;
    let latestTs = Date.now();

    try {
      const histockUrl = "https://histock.tw/stock/module/stockdata.aspx?no=TWN";
      const hResp = await fetch(histockUrl, {
        headers: {
          "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
          "Referer": "https://histock.tw/index-tw/TWN",
        },
        cf: { cacheTtl: 5 },
        // 富台是休市日唯一價格來源；全檔其他上游都有 timeout，這裡原本漏掉
        signal: fetchSignal(UPSTREAM_TIMEOUT_MS)
      });
      if (hResp.ok) {
        const hJson = await hResp.json().catch(() => null);
        let rawStr = (hJson && hJson.data) || "[]";
        rawStr = rawStr.replace(/,\s*\]\s*$/, "]");
        points = JSON.parse(rawStr);
        if (points && points.length > 0) {
          const last = points[points.length - 1];
          latestTs = last[0];
          // HiStock 時間戳或快取可能落後／偏移，價格以 GAS 即時值優先
          if (!latestPrice) latestPrice = last[1];
        }
      }
    } catch (_) {}

    const payload = {
      // 抓失敗不再回 ok:true：前端 `if (j && j.ok) twnData = j` 會拿空殼蓋掉手上好好的資料，
      // 而上一行 catch (_) {} 完全沒有訊號（2026-11-10 審查）
      ok: points.length > 0,
      reason: points.length > 0 ? undefined : "histock-unavailable",
      symbol: "TWN",
      name: "富台指 (SGX FTSE Taiwan)",
      latestPrice: latestPrice,
      latestTs: latestTs,
      points: points,
      gas: null, // GAS 已退場（實測 3.4~65s）；ratio/rateAdj/dayClose 走 data/twn-ratio.json
      updatedAt: new Date().toISOString(),
    };

    const out = jsonResp(payload, 200, {
      "Cache-Control": "public, max-age=0, s-maxage=5",
      "CDN-Cache-Control": "public, max-age=5",
      "Cloudflare-CDN-Cache-Control": "public, max-age=5",
    });

    ctx.waitUntil(cache.put(cacheKey, out.clone()));
    return out;
  } catch (err) {
    return jsonResp({ ok: false, error: String(err && err.message || err) }, 500, {
      "Cache-Control": "no-store",
    });
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

    if (kind === "twn") {
      return handleTwnQuote(request, env, ctx);
    }

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
          // days 直接決定對期交所的迴圈次數（每圈 2 個 POST）；不夾上限就是
          // 匿名 GET 換幾十個上游請求，最壞害 Worker 出口 IP 被 ban（2026-11-10 審查）
          const daysRaw = Number(url.searchParams.get("days") || 30);
          const days = Math.min(120, Math.max(1, Number.isFinite(daysRaw) ? Math.floor(daysRaw) : 30));
          const st = await refreshTmfRetail(env, days);
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
    // 診断用（2026-11-02）：直接跑一次 syncImb3 並回傳結果，不落 D1 以外的副作用
    if (kind === "imb3-probe") {
      // 這個端點會用富邦付費 token 打最多 3 檔×6 頁的上游並寫 D1；
      // 沒帶 PROBE_KEY 就只讀不寫（2026-11-10 審查：匿名者可拿它燒配額）
      const probeKey3 = String(env.PROBE_KEY || "");
      const canSync3 = !!probeKey3 && url.searchParams.get("key") === probeKey3;
      const hasTok = !!(await fubonToken(env));
      let cur3 = null;
      let recent3 = null;
      let cnt3 = null;
      try {
        const nowP = Date.now();
        const skP = sessionOf(nowP);
        const dkP = tradingDayKey(nowP);
        await ensureImb3Schema(env.IMB_DB);
        const qq = await env.IMB_DB.prepare(
          "SELECT sym, serial, in20, out20, upd FROM imb3_cur WHERE day_key=? AND session=? ORDER BY sym"
        ).bind(dkP, skP).all();
        cur3 = qq.results;
        const rr = await env.IMB_DB.prepare(
          "SELECT session, t, in20, out20, n, ts FROM imb3 WHERE day_key=? ORDER BY t DESC LIMIT 12"
        ).bind(dkP).all();
        recent3 = rr.results;
        const cc = await env.IMB_DB.prepare(
          "SELECT session, COUNT(*) AS c, MIN(t) AS mn, MAX(t) AS mx FROM imb3 WHERE day_key=? GROUP BY session"
        ).bind(dkP).all();
        cnt3 = cc.results;
      } catch (e) {
        cur3 = String((e && e.message) || e);
      }
      let out = canSync3 ? null : { ok: false, skipped: "probe-key-required" };
      if (canSync3) {
        try {
          out = await syncImb3(env, Date.now());
        } catch (e) {
          out = { ok: false, error: String((e && e.message) || e) };
        }
      }
      return jsonResp({ hasToken: hasTok, cur: cur3, recent: recent3, counts: cnt3, result: out }, 200, { "Cache-Control": "no-store" });
    }
    if (kind === "imb3") {
      const now3 = Date.now();
      const day3 = (url.searchParams.get("day") || "").replace(/-/g, "") || tradingDayKey(now3);
      try {
        await ensureImb3Schema(env.IMB_DB);
        const q3 = await env.IMB_DB.prepare(
          "SELECT session, t, d20, in20, out20, n, ts, q FROM imb3 WHERE day_key=? ORDER BY t ASC"
        ).bind(day3).all();
        const dArr = [], nArr = [];
        let upd = null;
        let lastQ = null;
        for (const r of q3.results || []) {
          const bar = { t: r.t, d: r.d20, in: r.in20, out: r.out20, n: r.n, ts: r.ts, q: r.q || "ok" };
          if (r.session === "day") dArr.push(bar); else nArr.push(bar);
          if (!upd || r.ts > upd) { upd = r.ts; lastQ = bar.q; }
        }
        return jsonResp(
          {
            dayKey: day3,
            day: dArr,
            night: nArr,
            unit: "1/20-taiex",
            quality: lastQ || "ok",
            updatedAt: upd ? new Date(upd).toISOString() : null,
            source: "d1-imb3",
          },
          200,
          {
            "Cache-Control": "public, max-age=5",
            "CDN-Cache-Control": "public, max-age=5",
            "Cloudflare-CDN-Cache-Control": "public, max-age=5",
          }
        );
      } catch (e) {
        return jsonResp({ ok: false, error: String(e && e.message || e) }, 500, { "Cache-Control": "no-store" });
      }
    }
    if (kind === "imb" || kind === "px1m") {
      const isPrice = kind === "px1m";
      const now = Date.now();
      const day =
        (url.searchParams.get("day") || "").replace(/-/g, "") ||
        tradingDayKey(now);

      // 邊緣快取 (Cloudflare Edge Cache API): 8 秒內全球請求共用同一快取，不重複打 D1
      const cacheKey = new Request(url.origin + url.pathname + "?kind=" + kind + "&day=" + day, request);
      const cache = caches.default;
      let cachedResp = await cache.match(cacheKey);
      if (cachedResp) return cachedResp;

      const pack = isPrice ? await loadPricePack(env, day) : await loadPack(env, day);
      const resp = jsonResp(
        {
          dayKey: day,
          day: pack.day,
          night: pack.night,
          updatedAt: pack.updatedAt,
          source: "d1",
        },
        200,
        {
          "Cache-Control": "public, max-age=0, s-maxage=8",
          "CDN-Cache-Control": "public, max-age=8",
          "Cloudflare-CDN-Cache-Control": "public, max-age=8",
        }
      );

      ctx.waitUntil(cache.put(cacheKey, resp.clone()));
      return resp;
    }


    if (kind === "heal") {
      try {
        const probeKey = String(env.PROBE_KEY || "");
        // force 會跳過所有節流直接 dispatch workflow；沒有 PROBE_KEY 就只允許正常 heal
        const force = url.searchParams.get("force") === "1"
          && !!probeKey && url.searchParams.get("key") === probeKey;
        const reason = url.searchParams.get("reason") || "frontend-stale";
        let res;
        if (force) {
          res = await triggerGithubWorkflow(env, "force:" + reason);
          res = { ok: res.ok, trigger: res, forced: true };
        } else {
          res = await maybeHealGithub(env, reason);
        }
        return jsonResp({ ok: true, heal: res, at: new Date().toISOString() }, 200, {
          "Cache-Control": "no-store",
          "CDN-Cache-Control": "no-store",
          "Cloudflare-CDN-Cache-Control": "no-store",
        });
      } catch (e) {
        return jsonResp({ ok: false, error: String((e && e.message) || e) }, 500, { "Cache-Control": "no-store" });
      }
    }
    if (kind === "heal-status") {
      try {
        const ageRes = await fetchGhAgeMs();
        const lastDispatch = await getHealState(env, "last_dispatch");
        const lastCheck = await getHealState(env, "last_gh_check");
        // 富台巡檢的可觀測性（2026-10-23）：沒這個就無法證明 maybeHealTwn 真的在跑
        const twnCheck = await getHealState(env, "last_twn_check");
        const twnDispatch = await getHealState(env, "last_twn_dispatch");
        let twnTail = 0;
        try {
          twnTail = await twnTailMs();
        } catch (_) {}
        let logs = [];
        try {
          await ensureSchema(env.IMB_DB);
          const { results } = await env.IMB_DB.prepare("SELECT ts, reason, ok, detail FROM heal_log ORDER BY id DESC LIMIT 10").all();
          logs = results || [];
        } catch (_) {}
        return jsonResp(
          {
            ok: true,
            ghAge: ageRes,
            lastDispatch,
            lastCheck,
            twn: {
              open: twnSgxOpen(Date.now()),
              lastCheck: twnCheck,
              lastDispatch: twnDispatch,
              tailMs: twnTail || 0,
              tailAgeMs: twnTail ? Date.now() - twnTail : null,
              staleMs: isHolidayMs(Date.now()) ? HEAL_TWN_STALE_MS : HEAL_TWN_STALE_IDLE_MS,
              staleMsHoliday: HEAL_TWN_STALE_MS,
              staleMsIdle: HEAL_TWN_STALE_IDLE_MS,
            },
            logs,
            at: new Date().toISOString(),
          },
          200,
          { "Cache-Control": "no-store" }
        );
      } catch (e) {
        return jsonResp({ ok: false, error: String((e && e.message) || e) }, 500, { "Cache-Control": "no-store" });
      }
    }

    // 手動／外部守護：強制打奇摩並寫 D1（不走 5 秒 CDN 快取語意）
    if (kind === "poll") {
      await ensureHolidays();
      const wantChart = url.searchParams.get("chart") === "1";
      const res = await pollAndStore(env, wantChart ? { chart: true } : undefined);
      // 不要把失敗包成 ok:true：外部守護若只看 ok，上游全掛也會回報健康。
      // 子項（imb／imb3y／px）失敗不會冒到頂層，所以逐項看 ok !== false（2026-11-10 審查 S04#8）
      const subOk = (o) => !o || o.ok !== false;
      const pollOk = !!res && !res.skipped && !res.skippedQuote && !res.reason
        && subOk(res.imb) && subOk(res.imb3y) && subOk(res.px);
      return jsonResp({ ok: pollOk, ...res, at: new Date().toISOString() }, 200, {
        "Cache-Control": "no-store",
        "CDN-Cache-Control": "no-store",
        "Cloudflare-CDN-Cache-Control": "no-store",
      });
    }

    if (kind === "mis") {
      // 診斷：CF Worker 打期交所 MIS 是否通
      const m = await fetchMisQuote();
      return jsonResp({ ok: !!m, mis: m, debug: _misDebug, at: new Date().toISOString() }, 200, {
        "Cache-Control": "no-store",
        "CDN-Cache-Control": "no-store",
        "Cloudflare-CDN-Cache-Control": "no-store",
      });
    }

    const YH_HDR = {
      "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0.0.0",
      Referer: "https://tw.stock.yahoo.com/",
    };

    if (kind === "1m") {
      let r;
      try {
        r = await fetch(CHART1M, { headers: YH_HDR, signal: fetchSignal(UPSTREAM_TIMEOUT_MS) });
      } catch (e) {
        const nm = (e && e.name) || "";
        return jsonResp({
          ok: false,
          error: (nm === "TimeoutError" || nm === "AbortError") ? "upstream-timeout" : String((e && e.message) || e).slice(0, 120),
        }, 504, { "Cache-Control": "no-store", "CDN-Cache-Control": "no-store", "Cloudflare-CDN-Cache-Control": "no-store" });
      }
      const body = await r.text();
      return new Response(body, {
        status: r.status,
        headers: {
          "content-type": "application/json; charset=utf-8",
          "Cache-Control": "public, max-age=0",
          "CDN-Cache-Control": "public, max-age=10",
          "Cloudflare-CDN-Cache-Control": "public, max-age=10",
          ...CORS,
        },
      });
    }

    // 報價（root）：Yahoo（五檔／相關）＋期交所 MIS（官方即時價）。
    // Yahoo 台指期夜盤常滯後或 IP 選擇性 5xx；用 MIS 即時價覆蓋，五檔仍取 Yahoo orderbook。
    // 2026-11-05：Yahoo 逾時不再拖垮整條報價——超時視同「這輪沒有 Yahoo」，
    // 仍由下方 MIS 官方價回答（前端最快 2.5～4 秒就有價，不再卡在載入中）。
    const yP = Promise.resolve()
      .then(() => fetch(YAHOO, { headers: YH_HDR, signal: fetchSignal(UPSTREAM_TIMEOUT_MS) }))
      .catch((e) => ({
        ok: false,
        status: 0,
        _timeout: String((e && e.name) || e),
        json: async () => null,
        text: async () => "",
      }));
    const [yRes, mis] = await Promise.all([yP, fetchMisQuote()]);
    let rows = null;
    if (yRes.ok) {
      try { rows = await yRes.json(); } catch (e) { rows = null; }
    }
    if (mis && mis.px != null) {
      if (!Array.isArray(rows)) rows = [];
      let w = rows.find((x) => x && x.symbol === "WTX&");
      if (!w) { w = { symbol: "WTX&", symbolName: "台指期近一" }; rows.unshift(w); }
      const num = (v) => (v == null ? undefined : { raw: String(v) });
      w.price = num(mis.px);
      if (mis.bid != null) w.bid = num(mis.bid);
      if (mis.ask != null) w.ask = num(mis.ask);
      if (mis.diff != null) w.change = num(mis.diff);
      if (mis.rate != null) w.changePercent = mis.rate.toFixed(2) + "%";
      if (mis.ref != null) w.regularMarketPreviousClose = num(mis.ref);
      // 盤別防護：成交／量／時間仍覆蓋。OHLC 不寫 regularMarketOpen/High/Low
      //（前端不讀這三欄，改由 1 分 K patchQuoteOHLC；見報價覆蓋契約 §3）。
      if (mis.vol != null) w.volume = String(mis.vol);
      const iso = misTimeIso(mis.date, mis.time);
      if (iso) {
        // 覆寫前先留一份 Yahoo 自己的書時間：下面的 yahooBookFresh() 若讀被覆寫後的欄位，
        // 等於拿自己剛寫進去的值當證據，這道「不送殘值五檔」的守門就永遠 pass
        if (w._yahooOwnTime === undefined) w._yahooOwnTime = w.regularMarketTime;
        w.regularMarketTime = iso;
      }
    }
    const nowMs = Date.now();
    await ensureHolidays();
    if (Array.isArray(rows)) {
      // 不退回 rows[0]：SymbolID 沒對上時 rows[0] 可能是別的契約（WMT&/WCDF&），
      // 拿它當台指期會讓五檔、內外盤、1 分 K 全部寫錯
      const wtx = rows.find((x) => x && x.symbol === "WTX&");
      if (wtx) {
        const ownBookTime = wtx._yahooOwnTime !== undefined ? wtx._yahooOwnTime : wtx.regularMarketTime;
        if (!yahooBookFresh({ regularMarketTime: ownBookTime }, nowMs)) {
          // 假日／來源日≠交易鍵：不把殘值五檔與內外盤送給前端 applyLive。
          delete wtx.orderbook;
          wtx.inMarket = null;
          wtx.outMarket = null;
        } else if (!bookStraddlesPx(wtx.orderbook, rawNum(wtx.price), 40)) {
          // 五檔與成交價脫節（夜盤尾 Yahoo 書停更）：只丟書，內外盤照常。
          delete wtx.orderbook;
        }
      }
    }
    if (!Array.isArray(rows)) {
      // 兩條上游同時失效（Yahoo 超時／MIS 也抓不到）：退回最近一次成功的報價，
      // 5 分鐘內有效。寧可晚幾秒，也不要讓前端卡在「載入中」。
      if (_lastRootGood && nowMs - _lastRootGood.ts < 5 * 60 * 1000) {
        return new Response(_lastRootGood.json, {
          status: 200,
          headers: {
            "content-type": "application/json; charset=utf-8",
            "Cache-Control": "public, max-age=0",
            "CDN-Cache-Control": "public, max-age=5",
            "Cloudflare-CDN-Cache-Control": "public, max-age=5",
            "X-TXF-Stale": "1",
            ...CORS,
          },
        });
      }
      const body = await yRes.text().catch(() => "");
      return new Response(body, {
        status: yRes.status || 504,
        headers: {
          "content-type": "application/json; charset=utf-8",
          "Cache-Control": "public, max-age=0",
          "CDN-Cache-Control": "public, max-age=5",
          "Cloudflare-CDN-Cache-Control": "public, max-age=5",
          ...CORS,
        },
      });
    }
    const outJson = JSON.stringify(rows);
    // 記住最後一次成功報價，供兩條上游同時失效時降級使用
    _lastRootGood = { json: outJson, ts: nowMs };
    return new Response(outJson, {
      status: 200,
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
    const nowMs = Date.now();
    // 富台 1 分 K 修復：SGX 有盤就要檢查，不受台指休市熔斷影響（颱風假照樣要跑）
    ctx.waitUntil(
      maybeHealTwn(env).catch((e) =>
        console.error("twn heal failed", String((e && e.message) || e))
      )
    );
    await ensureHolidays(); // 休市日 SSOT（模組快取 12h）
    // 0 毫秒極速跳脫：週末、休市大空窗、颱風假熔斷中，完全不啟動任何非同步工作，CPU 耗時 0ms
    if (!shouldRunCron(nowMs)) return;

    const isTradeSession = !!sessionOf(nowMs);
    const minute = new Date(nowMs).getUTCMinutes();
    // 每 5 分鐘順手跑一次尾端 chart backfill；平常分鐘只跑即時報價與五檔，極致省 CPU
    const needChart = minute % 5 === 0;
    ctx.waitUntil(
      (async () => {
        if (isTradeSession) {
          // 方案 A：pollAndStore 內已走 Yahoo 主線。
          // 2026-11-05：Yahoo 缺值時不再用富邦補寫（口徑差 ~2%，插進累計會造成跳升後又回落），
          // 失敗就跳過這一分鐘，等下一輪 Yahoo 自己回來。
          try {
            const pr = await pollAndStore(env, { chart: needChart });
            const r3 = pr && pr.imb3y;
            if (r3 && !r3.ok) console.log("imb3 skip", r3.reason);
          } catch (e) {
            console.error("poll failed", String((e && e.message) || e));
          }
        }
        try {
          const healRes = await maybeHealGithub(env, "cron-stale");
          if (healRes && healRes.stale) console.log("heal cron triggered", JSON.stringify(healRes).slice(0, 300));
        } catch (e) { console.error("heal cron failed", String((e && e.message) || e)); }
        try {
          const chipsRes = await maybeHealChips(env);
          if (chipsRes && !chipsRes.skipped) console.log("chips cron triggered", JSON.stringify(chipsRes).slice(0, 300));
        } catch (e) { console.error("chips cron failed", String((e && e.message) || e)); }
        try {
          await maybeRefreshTmf(env);
        } catch (e) {
          console.error("tmf cron failed", String((e && e.message) || e));
        }
      })()
    );
  },
};

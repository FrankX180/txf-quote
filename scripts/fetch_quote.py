# 奇摩 stockList WTX&：報價 + 五檔 → data/snapshot.json
# 勿打 query1.finance.yahoo.com。
import json
import re
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
TZ = timezone(timedelta(hours=8))
URL = (
    "https://tw.stock.yahoo.com/_td-stock/api/resource/"
    "StockServices.stockList;symbols=WTX%26,WCDF%26,WCCF%26"
)
# 備源：自家 Cloudflare Worker 直接 proxy 同一支 Yahoo stockList（同格式）。
# Yahoo 對此端點會按來源 IP 選擇性回 500（本機／GitHub Runner 中、CF 邊緣不中），
# 直連失敗時改走 Worker，等於借道 Cloudflare IP 繞過。
WORKER = "https://wtx.blok.trading/"
# 期交所 MIS 官方即時報價（REST）。注意：MIS 自身掛在 Cloudflare 後面，
# CF Worker 打它會 CF-to-CF 520、瀏覽器直連被 CORS 擋；只有 Actions（Azure）／本機能通。
MIS = "https://mis.taifex.com.tw/futures/api/getQuoteList"
MIS_MONTHS = "ABCDEFGHIJKL"  # MIS 月份字母 A=1月..L=12月（非期貨慣用碼）
HDR = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0.0.0",
    "Referer": "https://tw.stock.yahoo.com/",
}
KEEP_POINTS = 800


def _fetch_once(url: str) -> list:
    req = urllib.request.Request(url, headers=HDR)
    with urllib.request.urlopen(req, timeout=25) as r:
        j = json.loads(r.read().decode("utf-8", "replace"))
    return j if isinstance(j, list) else [j]


def get_list(retries: int = 3) -> list:
    """先直連 Yahoo；失敗改走自家 Worker（借 CF IP 繞過 IP 選擇性 5xx）。
    4xx（除 429）是請求本身有問題，重試無意義，直接換來源。
    200 也可能帶空／異常 payload，一律驗證有無 symbol 才收，避免污染 snapshot。"""
    last_err = None
    for src_name, url in (("yahoo", URL), ("worker", WORKER)):
        for i in range(retries):
            try:
                rows = _fetch_once(url)
            except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, OSError) as e:
                last_err = e
                code = getattr(e, "code", None)
                if code is not None and 400 <= code < 500 and code != 429:
                    break  # 4xx 無意義，換來源
                if i < retries - 1:
                    wait = 0.6 * (2 ** i)
                    print(
                        "WARN get_list[%s] attempt %d/%d failed: %s; retry in %.1fs"
                        % (src_name, i + 1, retries, e, wait),
                        flush=True,
                    )
                    time.sleep(wait)
                continue
            if any(isinstance(x, dict) and x.get("symbol") for x in rows):
                if src_name != "yahoo":
                    print("WARN get_list via %s fallback" % src_name, flush=True)
                return rows
            last_err = ValueError("invalid quote payload from %s" % src_name)
            print("WARN get_list[%s] returned empty/invalid payload; try next source" % src_name, flush=True)
            break  # 換來源
    raise last_err


def _mis_quote(sess):
    """取台指期近月即時報價（MIS 官方）。盤別 → MarketType（day=0/night=1），失敗再試另一個。"""
    want = "0" if sess == "day" else "1"
    for m in (want, "0" if want == "1" else "1"):
        try:
            body = {"MarketType": m, "SymbolType": "F", "KindID": "1", "CID": "", "ExpireMonth": "", "PageNo": 1}
            req = urllib.request.Request(
                MIS,
                data=json.dumps(body).encode("utf-8"),
                headers={
                    "Content-Type": "application/json;charset=UTF-8",
                    "Origin": "https://mis.taifex.com.tw",
                    "Referer": "https://mis.taifex.com.tw/futures/RegularSession/EquityIndices/FuturesDomestic/",
                    "User-Agent": HDR["User-Agent"],
                },
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=25) as r:
                j = json.loads(r.read().decode("utf-8", "replace"))
            ql = (j.get("RtData") or {}).get("QuoteList") or []

            def score(sid):
                mm = re.match(r"^TXF([A-Z])(\d)-M$", sid or "")
                if not mm:
                    return 10 ** 9
                mi = MIS_MONTHS.find(mm.group(1))
                return 10 ** 9 if mi < 0 else int(mm.group(2)) * 100 + mi

            cand = sorted(
                [x for x in ql if re.match(r"^TXF[A-Z]\d-M$", x.get("SymbolID") or "")],
                key=lambda x: score(x.get("SymbolID")),
            )
            best = next((x for x in cand if x.get("CLastPrice")), None)
            if best:
                best["_mt"] = m
                best["_want"] = want
                return best
        except Exception as e:  # noqa: BLE001
            print("WARN mis MT%s %s" % (m, e), flush=True)
    return None


def apply_mis(rows, sess):
    """用 MIS 官方價覆盖 WTX& 列的報價欄位（五檔 orderbook 仍留 Yahoo）。"""
    mis = _mis_quote(sess)
    if not mis:
        return rows
    if not rows:
        rows = [{"symbol": "WTX&", "symbolName": "台指期近一"}]
    w = pick(rows, "WTX&") or rows[0]

    def val(k):
        v = mis.get(k)
        return None if v in (None, "") else str(v)

    if val("CLastPrice") is not None:
        w["price"] = {"raw": val("CLastPrice")}
    if val("CBidPrice1") is not None:
        w["bid"] = {"raw": val("CBidPrice1")}
    if val("CAskPrice1") is not None:
        w["ask"] = {"raw": val("CAskPrice1")}
    if val("CDiff") is not None:
        w["change"] = {"raw": val("CDiff")}
    if val("CDiffRate") is not None:
        w["changePercent"] = val("CDiffRate") + "%"
    if val("CRefPrice") is not None:
        w["regularMarketPreviousClose"] = {"raw": val("CRefPrice")}
    if val("CTotalVolume") is not None:
        w["volume"] = val("CTotalVolume")
    # MIS 官方開高低／漲跌停＝當盤值：僅在「本次查詢盤別＝目標盤別」時採用，
    # 避免休市／日收後 fallback 取到他盤值污染當盤 OHLC（下游 ohlc_from_kline 會回填）。
    if sess in ("day", "night") and mis.get("_mt") == mis.get("_want"):
        for mis_key, slim_key in (("COpenPrice", "misOpen"), ("CHighPrice", "misHigh"), ("CLowPrice", "misLow")):
            if val(mis_key) is not None:
                w[slim_key] = val(mis_key)
        # 漲跌停（前端未接，先落地備用）
        if val("CCeilPrice") is not None:
            w["misCeil"] = val("CCeilPrice")
        if val("CFloorPrice") is not None:
            w["misFloor"] = val("CFloorPrice")
    dt, tt = str(mis.get("CDate") or ""), str(mis.get("CTime") or "")
    if len(dt) >= 8 and len(tt) >= 6:
        w["regularMarketTime"] = "%s-%s-%sT%s:%s:%s+08:00" % (
            dt[0:4], dt[4:6], dt[6:8], tt[0:2], tt[2:4], tt[4:6])
    if not w.get("symbolName"):
        w["symbolName"] = "台指期近一"
    print("MIS applied px=%s t=%s" % (val("CLastPrice"), w.get("regularMarketTime")), flush=True)
    return rows


def pick(rows, symbol):
    for d in rows:
        if d.get("symbol") == symbol:
            return d
    return None


def slim_mini(d):
    if not d:
        return None
    last = raw(d.get("price"))
    ref = raw(d.get("regularMarketPreviousClose"))
    diff = raw(d.get("change"))
    if diff is None and last is not None and ref is not None:
        diff = last - ref
    rate = None
    cp = d.get("changePercent")
    if isinstance(cp, str) and cp.endswith("%"):
        try:
            rate = float(cp.replace("%", ""))
        except ValueError:
            rate = None
    if rate is None and diff is not None and ref:
        rate = (diff / ref) * 100
    return {
        "symbol": d.get("symbol") or "",
        "name": d.get("symbolName") or "",
        "last": fmt_num(last, 0 if last and last >= 100 else 1),
        "diff": fmt_num(diff, 0 if last and last >= 100 else 1),
        "rate": "" if rate is None else ("%.2f" % rate),
    }


def raw(v):
    if v is None:
        return None
    if isinstance(v, dict):
        v = v.get("raw")
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def fmt_num(v, nd=0):
    x = raw(v)
    if x is None:
        return ""
    if nd == 0:
        return str(int(round(x)))
    return ("%." + str(nd) + "f") % x


def lots(v):
    x = raw(v)
    if x is None:
        return ""
    return str(int(round(x)))


def k_to_lots(v):
    x = raw(v)
    if x is None:
        return ""
    return str(int(round(x * 1000)))


def parse_mkt_time(d):
    t = d.get("regularMarketTime")
    if isinstance(t, dict):
        t = t.get("raw") or t.get("fmt")
    if isinstance(t, (int, float)) and t > 1e9:
        dt = datetime.fromtimestamp(int(t), TZ)
        return dt
    if isinstance(t, str) and t:
        try:
            return datetime.fromisoformat(t.replace("Z", "+00:00")).astimezone(TZ)
        except ValueError:
            pass
    return datetime.now(TZ)


def slim(d):
    last = raw(d.get("price"))
    ref = raw(d.get("regularMarketPreviousClose"))
    diff = raw(d.get("change"))
    if diff is None and last is not None and ref is not None:
        diff = last - ref
    rate = None
    cp = d.get("changePercent")
    if isinstance(cp, str) and cp.endswith("%"):
        try:
            rate = float(cp.replace("%", ""))
        except ValueError:
            rate = None
    if rate is None and diff is not None and ref:
        rate = (diff / ref) * 100
    dt = parse_mkt_time(d)
    q = {
        "SymbolID": d.get("symbol") or "WTX&",
        "DispCName": d.get("symbolName") or "台指期近一",
        "DispEName": "WTX&",
        "CLastPrice": fmt_num(last),
        # 奇摩 regularMarketOpen/High/Low 是混盤，勿寫入；由 apply_mis 蓋入官方當盤值，
        # 或（MIS 無值時）由 ohlc_from_kline 以 1 分 K 回填。
        "COpenPrice": fmt_num(d.get("misOpen")),
        "CHighPrice": fmt_num(d.get("misHigh")),
        "CLowPrice": fmt_num(d.get("misLow")),
        "CCeilPrice": fmt_num(d.get("misCeil")),
        "CFloorPrice": fmt_num(d.get("misFloor")),
        "CRefPrice": fmt_num(ref),
        "CDiff": fmt_num(diff),
        "CDiffRate": "" if rate is None else ("%.2f" % rate),
        "CTotalVolume": lots(d.get("volume")) or k_to_lots(d.get("volumeK")),
        "CDate": dt.strftime("%Y%m%d"),
        "CTime": dt.strftime("%H%M%S"),
        "inMarket": k_to_lots(d.get("inMarket")),
        "outMarket": k_to_lots(d.get("outMarket")),
        "marketStatus": d.get("marketStatus") or "",
    }
    book = d.get("orderbook") or []
    for i in range(5):
        lvl = book[i] if i < len(book) else {}
        q["CBidPrice" + str(i + 1)] = fmt_num(lvl.get("bid"))
        q["CBidSize" + str(i + 1)] = lots(lvl.get("bidVol"))
        q["CAskPrice" + str(i + 1)] = fmt_num(lvl.get("ask"))
        q["CAskSize" + str(i + 1)] = lots(lvl.get("askVol"))
    return q


def book_fields(q):
    if not q:
        return {}
    out = {}
    for i in range(1, 6):
        for k in ("CBidPrice", "CBidSize", "CAskPrice", "CAskSize"):
            key = k + str(i)
            if key in q:
                out[key] = q[key]
    for k in ("inMarket", "outMarket"):
        if k in q:
            out[k] = q[k]
    return out


def copy_book(dst, src):
    if not dst or not src:
        return dst
    for k, v in book_fields(src).items():
        dst[k] = v
    return dst


def strip_book(q):
    if not q:
        return q
    o = dict(q)
    for i in range(1, 6):
        for k in ("CBidPrice", "CBidSize", "CAskPrice", "CAskSize"):
            o.pop(k + str(i), None)
    return o


def book_real(q):
    """True five levels? Yahoo returns a degraded book after the day close:
    bid1 = flat price, same-side tick gaps > 10, sizes 0/1, some levels "-".
    """
    if not q:
        return False
    bids = []
    asks = []
    for i in range(1, 6):
        b = raw(q.get("CBidPrice" + str(i)))
        bs = raw(q.get("CBidSize" + str(i)))
        a = raw(q.get("CAskPrice" + str(i)))
        az = raw(q.get("CAskSize" + str(i)))
        if b is not None and bs and bs > 0:
            bids.append(b)
        if a is not None and az and az > 0:
            asks.append(a)
    if len(bids) < 2 or len(asks) < 2:
        return False
    bids.sort(reverse=True)
    asks.sort()
    if not (bids[0] < asks[0]) or asks[0] - bids[0] > 20:
        return False
    for i in range(1, len(bids)):
        if bids[i - 1] - bids[i] > 10:
            return False
    for i in range(1, len(asks)):
        if asks[i] - asks[i - 1] > 10:
            return False
    return True

def sess_now(dt=None):
    d = dt or datetime.now(TZ)
    h = d.hour * 100 + d.minute
    wd = d.weekday()
    if wd == 6:
        return None
    if wd == 5 and h >= 510:
        return None
    if 845 <= h <= 1345:
        return "day"
    if h >= 1455 or h < 510:
        return "night"
    return None


def load_prev_snap():
    p = DATA / "snapshot.json"
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}


def append_hist(path: Path, q, now_iso):
    hist = []
    if path.exists():
        hist = json.loads(path.read_text(encoding="utf-8"))
    if not q or not q.get("CLastPrice"):
        return
    pt = {
        "t": now_iso,
        "date": q.get("CDate"),
        "time": q.get("CTime"),
        "px": float(q["CLastPrice"]),
        "vol": int(q["CTotalVolume"] or 0),
        "bid": q.get("CBidPrice1"),
        "ask": q.get("CAskPrice1"),
    }
    if (
        hist
        and hist[-1].get("date") == pt["date"]
        and hist[-1].get("time") == pt["time"]
        and hist[-1].get("px") == pt["px"]
    ):
        return
    hist.append(pt)
    hist = hist[-KEEP_POINTS:]
    path.write_text(json.dumps(hist, ensure_ascii=False), encoding="utf-8")


def ohlc_from_kline(base_q, sess="day"):
    """Overlay session OHLC from 1m (fallback 5m/15m); keep caller's book fields."""
    out = dict(base_q)
    kpath = DATA / "kline-minute.json"
    if not kpath.exists():
        return out
    try:
        pack = json.loads(kpath.read_text(encoding="utf-8"))
        prefix = "day" if sess == "day" else "night"
        drows = pack.get(prefix + "_1m") or pack.get(prefix + "_5m") or pack.get(prefix + "_15m") or []
        dates = []
        for b in reversed(drows):
            if b.get("date") and b["date"] not in dates:
                dates.append(b["date"])
                break
        part = [b for b in drows if dates and b.get("date") == dates[0]]
        if not part:
            return out
        highs = [b.get("high") for b in part if b.get("high") is not None]
        lows = [b.get("low") for b in part if b.get("low") is not None]
        if not highs or not lows:
            return out
        # MIS 官方開高低已有值就不覆寫（MIS 無值才用 K 線 fallback）
        if not out.get("COpenPrice"):
            out["COpenPrice"] = fmt_num(part[0].get("open"))
        if not out.get("CHighPrice"):
            out["CHighPrice"] = fmt_num(max(highs))
        if not out.get("CLowPrice"):
            out["CLowPrice"] = fmt_num(min(lows))
        # 日盤收盤後才覆寫 last；盤中保留報價 last
        if sess == "day" and base_q.get("CLastPrice") in (None, ""):
            out["CLastPrice"] = fmt_num(part[-1].get("close"))
        last = raw(out.get("CLastPrice"))
        ref = raw(out.get("CRefPrice"))
        if last is not None and ref is not None:
            out["CDiff"] = fmt_num(last - ref)
            out["CDiffRate"] = "%.2f" % ((last - ref) / ref * 100) if ref else ""
    except Exception:
        pass
    return out


def day_ohlc_from_kline(base_q):
    return ohlc_from_kline(base_q, "day")


def main():
    from when import want_yahoo_quote
    if not want_yahoo_quote():
        print("SKIP quote: closed")
        return
    DATA.mkdir(exist_ok=True)
    now = datetime.now(TZ)
    now_iso = now.strftime("%Y-%m-%d %H:%M:%S")
    rows = None
    try:
        rows = get_list()
    except Exception as e:
        print("WARN get_list failed (%s); try MIS-only" % e, flush=True)
        rows = []
    # 以 MIS 官方即時價覆蓋（Actions 可打 MIS；CF Worker 不行）
    rows = apply_mis(rows, sess_now(now))
    if not rows:
        # 兩源都掛：不寫壞資料、不中斷其他 step；有舊 snapshot 就沿用
        if (DATA / "snapshot.json").exists():
            print("WARN no quote source (yahoo+mis); keep previous snapshot", flush=True)
            return
        raise SystemExit("no quote source")
    d = pick(rows, "WTX&") or (rows[0] if rows else {})
    q = slim(d)
    related = [
        slim_mini(pick(rows, "WCDF&")),
        slim_mini(pick(rows, "WCCF&")),
    ]
    related = [x for x in related if x]
    prev = load_prev_snap()
    cur = sess_now(now)
    # 日／夜五檔分開：只寫當盤 orderbook；另一盤沿用上一版（且拒絕與當盤同價的污染本）
    def keep_other(prev_q, live_q):
        if not prev_q:
            return strip_book(dict(live_q))
        o = dict(prev_q)
        if (
            o.get("CBidPrice1")
            and o.get("CBidPrice1") == live_q.get("CBidPrice1")
            and o.get("CAskPrice1") == live_q.get("CAskPrice1")
        ):
            return strip_book(o)
        return o

    if cur == "day":
        day_q = day_ohlc_from_kline(dict(q))
        copy_book(day_q, q)
        night_q = keep_other(prev.get("night"), q)
    elif cur == "night":
        night_q = ohlc_from_kline(dict(q), "night")
        copy_book(night_q, q)
        if prev.get("day"):
            day_q = day_ohlc_from_kline(dict(prev["day"]))
            copy_book(day_q, prev["day"])
        else:
            day_q = strip_book(day_ohlc_from_kline(dict(q)))
    else:
        # 休市（日收後～夜開前等）：仍用最新一口更新「應顯示盤」收／量／五檔
        # 舊邏輯只抄 prev，會卡在中午舊價（例 12:24=45937）
        h = now.hour * 100 + now.minute
        if h >= 1458 or h < 845:
            night_q = ohlc_from_kline(dict(q), "night")
            copy_book(night_q, q)
            if prev.get("day"):
                day_q = day_ohlc_from_kline(dict(prev["day"]))
                copy_book(day_q, prev["day"])
            else:
                day_q = strip_book(day_ohlc_from_kline(dict(q)))
        else:
            # 日收後～夜開前：更新日盤收／量／五檔，但昨收鎖定上一版（奇摩 previousClose 已滾夜盤）
            day_q = day_ohlc_from_kline(dict(q))
            prev_day = prev.get("day") or {}
            if book_real(q):
                copy_book(day_q, q)
            elif prev_day:
                copy_book(day_q, prev_day)
            if prev_day.get("CRefPrice"):
                day_q["CRefPrice"] = prev_day["CRefPrice"]
                last = raw(day_q.get("CLastPrice"))
                ref = raw(day_q.get("CRefPrice"))
                if last is not None and ref:
                    day_q["CDiff"] = fmt_num(last - ref)
                    day_q["CDiffRate"] = "%.2f" % ((last - ref) / ref * 100)
            if prev.get("night"):
                night_q = dict(prev["night"])
            else:
                night_q = strip_book(dict(q))
    snap = {
        "fetchedAt": now_iso,
        "source": "yahoo tw.stock StockServices.stockList WTX& WCDF& WCCF&",
        "session": cur or "",
        "night": night_q,
        "day": day_q,
        "related": related,
    }
    (DATA / "snapshot.json").write_text(
        json.dumps(snap, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    if cur == "night":
        append_hist(DATA / "history-night.json", night_q, now_iso)
    elif cur == "day":
        append_hist(DATA / "history-day.json", day_q, now_iso)
    active = night_q if cur == "night" else day_q
    print(
        "OK",
        now_iso,
        "sess",
        cur or "-",
        q.get("CLastPrice"),
        "bid1",
        active.get("CBidPrice1"),
        "ask1",
        active.get("CAskPrice1"),
        "levels",
        sum(1 for i in range(1, 6) if active.get("CBidPrice" + str(i))),
        q.get("marketStatus"),
        "related",
        len(related),
    )


if __name__ == "__main__":
    main()

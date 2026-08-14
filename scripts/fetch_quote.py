# 奇摩 stockList WTX&：報價 + 五檔 → data/snapshot.json
# 勿打 query1.finance.yahoo.com。
import json
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
HDR = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0.0.0",
    "Referer": "https://tw.stock.yahoo.com/",
}
KEEP_POINTS = 800


def get_list() -> list:
    req = urllib.request.Request(URL, headers=HDR)
    with urllib.request.urlopen(req, timeout=25) as r:
        j = json.loads(r.read().decode("utf-8", "replace"))
    return j if isinstance(j, list) else [j]


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
        "COpenPrice": fmt_num(d.get("regularMarketOpen")),
        "CHighPrice": fmt_num(d.get("regularMarketDayHigh")),
        "CLowPrice": fmt_num(d.get("regularMarketDayLow")),
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


def day_ohlc_from_kline(base_q):
    """Overlay day OHLC from minute pack; keep caller's book fields."""
    out = dict(base_q)
    kpath = DATA / "kline-minute.json"
    if not kpath.exists():
        return out
    try:
        pack = json.loads(kpath.read_text(encoding="utf-8"))
        drows = pack.get("day_5m") or pack.get("day_15m") or pack.get("day_1m") or []
        dates = []
        for b in reversed(drows):
            if b.get("date") and b["date"] not in dates:
                dates.append(b["date"])
                break
        part = [b for b in drows if dates and b.get("date") == dates[0]]
        if not part:
            return out
        out["COpenPrice"] = fmt_num(part[0].get("open"))
        out["CHighPrice"] = fmt_num(max(b.get("high") for b in part))
        out["CLowPrice"] = fmt_num(min(b.get("low") for b in part))
        out["CLastPrice"] = fmt_num(part[-1].get("close"))
        last = raw(out["CLastPrice"])
        ref = raw(out.get("CRefPrice"))
        if last is not None and ref is not None:
            out["CDiff"] = fmt_num(last - ref)
            out["CDiffRate"] = "%.2f" % ((last - ref) / ref * 100) if ref else ""
    except Exception:
        pass
    return out


def main():
    from when import want_yahoo_quote
    if not want_yahoo_quote():
        print("SKIP quote: closed")
        return
    DATA.mkdir(exist_ok=True)
    now = datetime.now(TZ)
    now_iso = now.strftime("%Y-%m-%d %H:%M:%S")
    rows = get_list()
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
        night_q = dict(q)
        if prev.get("day"):
            day_q = day_ohlc_from_kline(dict(prev["day"]))
            copy_book(day_q, prev["day"])
        else:
            day_q = strip_book(day_ohlc_from_kline(dict(q)))
    else:
        day_q = dict(prev.get("day") or strip_book(q))
        night_q = dict(prev.get("night") or strip_book(q))
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

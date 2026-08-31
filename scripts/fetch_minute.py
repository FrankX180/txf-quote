# 盤中：奇摩 1/5/15 分。收盤後：富邦 DJ 1 分補齊。
import json
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

from when import want_fubon, want_yahoo_minute

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "kline-minute.json"
TZ = timezone(timedelta(hours=8))
SYM = "WTX%26"
KEEP_DAYS = 10
HDR = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0.0.0",
    "Referer": "https://tw.stock.yahoo.com/",
}
FUBON = "https://fubon-ebrokerdj.fbs.com.tw/Z/ZM/ZMB/CZMB.djbcd?a=FITXN&b=-1&c=1&D=12000"
FUBON_HDR = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0.0.0",
    "Referer": "https://fubon-ebrokerdj.fbs.com.tw/",
}


def get(url: str) -> dict:
    req = urllib.request.Request(url, headers=HDR)
    with urllib.request.urlopen(req, timeout=25) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


def classify(ts: int):
    dt = datetime.fromtimestamp(int(ts), TZ)
    hm = dt.hour * 100 + dt.minute
    # date 一律 YYYYMMDD，與報價 CDate／前端一致
    if hm >= 1500 or hm < 500:
        d0 = dt.date() - timedelta(days=1) if hm < 500 else dt.date()
        sdate = d0.strftime("%Y%m%d")
        return "night", sdate, dt
    if 845 <= hm <= 1345:
        return "day", dt.strftime("%Y%m%d"), dt
    return None, None, dt


def bars_from_chart(chart: dict):
    ts_list = chart.get("timestamp") or []
    q = ((chart.get("indicators") or {}).get("quote") or [{}])[0]
    opens, highs, lows, closes = q.get("open") or [], q.get("high") or [], q.get("low") or [], q.get("close") or []
    vols = q.get("volume") or []
    night, day = [], []
    for i, ts in enumerate(ts_list):
        c = closes[i] if i < len(closes) else None
        if c is None:
            continue
        o = opens[i] if i < len(opens) and opens[i] is not None else c
        h = highs[i] if i < len(highs) and highs[i] is not None else c
        l = lows[i] if i < len(lows) and lows[i] is not None else c
        v = vols[i] if i < len(vols) and vols[i] is not None else 0
        sess, sdate, dt = classify(ts)
        if not sess:
            continue
        row = {
            "timestamp": int(ts) * 1000,
            "open": float(o),
            "high": float(h),
            "low": float(l),
            "close": float(c),
            "volume": int(round(float(v) * 1000)),
            "date": sdate,
            "session": sess,
        }
        (night if sess == "night" else day).append(row)
    return night, day


def fetch_1m():
    url = (
        "https://tw.stock.yahoo.com/_td-stock/api/resource/"
        f"StockServices.chart;symbol={SYM};period=1m;range=1d"
    )
    j = get(url)
    return bars_from_chart(j)


def merge_bars(*groups):
    by_ts = {}
    for rows in groups:
        for b in rows or []:
            by_ts[b["timestamp"]] = b
    return sorted(by_ts.values(), key=lambda x: x["timestamp"])


def fix_future_ts(rows):
    """Yahoo 夜盤標次交易日：平日最多 -1 天、週五夜最多 -2 天。
    禁止無限回移：舊夜盤被扣到今晚會在同一分鐘疊 KEEP_DAYS 根（千點刷針）。
    SSOT: 01_Docs/Yahoo-台指期K線日期慣例.md"""
    if not rows:
        return rows
    limit = int(datetime.now(TZ).timestamp()) + 120
    out = []
    for b in rows:
        ts = int(b["timestamp"]) // 1000
        n = 0
        while ts > limit and n < 2:
            ts -= 86400
            n += 1
        if ts > limit:
            continue
        sess, sdate, _ = classify(ts)
        if not sess:
            continue
        b = dict(b)
        b["timestamp"] = ts * 1000
        b["date"] = sdate
        b["session"] = sess
        out.append(b)
    return collapse_minute(out)


def collapse_minute(rows):
    """同一分鐘只留一根（回移後舊夜盤會撞槽）。"""
    by = {}
    for b in rows or []:
        slot = (int(b["timestamp"]) // 60000) * 60000
        prev = by.get(slot)
        if prev is None:
            by[slot] = b
            continue
        pv, nv = float(prev.get("volume") or 0), float(b.get("volume") or 0)
        if nv >= pv:
            by[slot] = b
    return [by[k] for k in sorted(by)]


def trim_days(rows, n=KEEP_DAYS):
    dates = []
    for b in reversed(rows):
        d = b.get("date")
        if d and d not in dates:
            dates.append(d)
        if len(dates) >= n:
            break
    keep = set(dates)
    return [b for b in rows if b.get("date") in keep]


def load_old():
    if not OUT.exists():
        return [], []
    old = json.loads(OUT.read_text(encoding="utf-8"))
    return old.get("night_1m") or old.get("night") or [], old.get("day_1m") or old.get("day") or []


def fetch_fubon_1m():
    req = urllib.request.Request(FUBON, headers=FUBON_HDR)
    with urllib.request.urlopen(req, timeout=40) as r:
        text = r.read().decode("utf-8", "replace")
    chunks = text.split(" ")
    if len(chunks) < 5:
        return [], []
    codes = [x.strip() for x in chunks[0].split(",") if len(x.strip()) == 6 and x.strip().isdigit()]
    opens = chunks[1].split(",")
    highs = chunks[2].split(",")
    lows = chunks[3].split(",")
    closes = chunks[4].split(",")
    vols = chunks[5].split(",") if len(chunks) > 5 else []
    now = datetime.now(TZ)
    y, m = now.year, now.month
    prev_dd = None
    stamps = []
    for code in reversed(codes):
        dd, hh, mm = int(code[:2]), int(code[2:4]), int(code[4:6])
        if prev_dd is not None and dd > prev_dd:
            m -= 1
            if m < 1:
                m, y = 12, y - 1
        prev_dd = dd
        try:
            stamps.append(datetime(y, m, dd, hh, mm, tzinfo=TZ))
        except ValueError:
            stamps.append(None)
    stamps.reverse()
    night, day = [], []
    n = min(len(codes), len(closes))
    for i in range(n):
        dt = stamps[i] if i < len(stamps) else None
        if dt is None:
            continue
        try:
            c = float(closes[i])
        except (TypeError, ValueError):
            continue
        def f(arr):
            try:
                return float(arr[i])
            except (TypeError, ValueError, IndexError):
                return c
        try:
            v = int(round(float(vols[i]))) if i < len(vols) else 0
        except (TypeError, ValueError):
            v = 0
        sess, sdate, _ = classify(int(dt.timestamp()))
        if not sess:
            continue
        row = {
            "timestamp": int(dt.timestamp()) * 1000,
            "open": f(opens),
            "high": f(highs),
            "low": f(lows),
            "close": c,
            "volume": v,
            "date": sdate,
            "session": sess,
        }
        (night if sess == "night" else day).append(row)
    return night, day


def fetch_apac(period: str):
    enc = "%5B%22WTX%26%22%5D"
    url = (
        "https://tw.stock.yahoo.com/_td-stock/api/resource/"
        f"FinanceChartService.ApacLibraCharts;period={period};symbols={enc}"
        "?intl=tw&lang=zh-Hant-TW&region=TW&site=finance&tz=Asia/Taipei&returnMeta=true"
    )
    j = get(url)
    block = j.get("data") or []
    chart = (block[0] or {}).get("chart") or {} if block else {}
    return bars_from_chart(chart)


def main():
    do_y = want_yahoo_minute()
    do_f = want_fubon()
    if not do_y and not do_f:
        print("SKIP minute: closed")
        return
    on, od = load_old()
    yn, yd = [], []
    src = []
    if do_y:
        yn, yd = fetch_1m()
        src.append("yahoo 1m")
    fn, fd = [], []
    if do_f:
        fn, fd = fetch_fubon_1m()
        src.append("fubon 1m")
    n1 = fix_future_ts(trim_days(merge_bars(on, yn, fn)))
    d1 = fix_future_ts(trim_days(merge_bars(od, yd, fd)))
    n5, d5, n15, d15 = [], [], [], []
    old = json.loads(OUT.read_text(encoding="utf-8")) if OUT.exists() else {}
    if do_y:
        n5, d5 = fetch_apac("5m")
        n15, d15 = fetch_apac("15m")
        src.append("yahoo 5m 15m")
        n5, d5, n15, d15 = (fix_future_ts(x) for x in (n5, d5, n15, d15))
    else:
        n5, d5 = old.get("night_5m") or [], old.get("day_5m") or []
        n15, d15 = old.get("night_15m") or [], old.get("day_15m") or []
    payload = {
        "fetchedAt": datetime.now(TZ).strftime("%Y-%m-%d %H:%M:%S"),
        "source": " / ".join(src),
        "contract": "WTX&",
        "night_1m": n1,
        "day_1m": d1,
        "night_5m": n5,
        "day_5m": d5,
        "night_15m": n15,
        "day_15m": d15,
    }
    OUT.parent.mkdir(exist_ok=True)
    OUT.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    print(
        "OK 1m", len(n1), len(d1),
        "5m", len(n5), len(d5),
        "15m", len(n15), len(d15),
        "bytes", OUT.stat().st_size,
    )


if __name__ == "__main__":
    main()

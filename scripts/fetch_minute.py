# 小台（MXF）1/5/15 分 K。
#
# 2026-10-29 改小台的重要事實（實測）：
#   · Yahoo 奇摩沒有小台（WTXM& 停更於 2026-06-23、MXF&/MTX& 皆 404）。
#   · 富邦 DJ CZMB.djbcd?a=FIMTXN 有真小台 1 分 K，但**只有日盤**（最後一筆 13:44，無夜盤）。
#   · MIS 無分時 K API（getChartData 等全 404）。
# ⇒ 折衷架構：日盤 1 分 K 用富邦真小台；夜盤沿用 Yahoo 大台（WTX&，同一標的，
#   兩者價差僅流動性差異 1~3 點）。5/15 分一律由 1 分聚合。
import json
import time
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

from when import want_fubon, want_yahoo_minute
from holiday_guard import load_holidays

HOLIDAYS = load_holidays()

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "kline-minute.json"
TZ = timezone(timedelta(hours=8))
KEEP_DAYS = 10
CONTRACT = "MXF&"
# FIMTXN＝富邦 DJ 小台近月（只有日盤）；FITXN＝大台近月
FUBON = "https://fubon-ebrokerdj.fbs.com.tw/Z/ZM/ZMB/CZMB.djbcd?a=FIMTXN&b=-1&c=1&D=12000"
FUBON_HDR = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0.0.0",
    "Referer": "https://fubon-ebrokerdj.fbs.com.tw/",
}
# 夜盤唯一來源：Yahoo 大台（小台無夜盤分時源）
YH_1M = (
    "https://tw.stock.yahoo.com/_td-stock/api/resource/"
    "StockServices.chart;symbol=WTX%26;period=1m;range=1d"
)
YH_HDR = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0.0.0",
    "Referer": "https://tw.stock.yahoo.com/",
}
# 備源：自家 Worker 代理同一支（CF IP 可過 Yahoo 的 IP 選擇性 5xx）
WORKER_1M = "https://wtx.blok.trading/?kind=1m"


def classify(ts: int):
    dt = datetime.fromtimestamp(int(ts), TZ)
    hm = dt.hour * 100 + dt.minute
    if hm >= 1500 or hm < 500:
        d0 = dt.date() - timedelta(days=1) if hm < 500 else dt.date()
        return "night", d0.strftime("%Y%m%d"), dt
    if 845 <= hm <= 1345:
        return "day", dt.strftime("%Y%m%d"), dt
    return None, None, dt


def get_json(url, headers=None):
    req = urllib.request.Request(url, headers=headers or YH_HDR)
    with urllib.request.urlopen(req, timeout=25) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


def fetch_fubon_1m():
    """富邦小台 1 分 K（僅日盤）。"""
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


def bars_from_chart(chart):
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
            "volume": int(round(float(v))),
            "date": sdate,
            "session": sess,
        }
        (night if sess == "night" else day).append(row)
    return night, day


def fetch_yahoo_night_1m():
    """Yahoo 大台 1 分 K，只取夜盤段（小台夜盤無源）。"""
    try:
        j = get_json(YH_1M)
    except Exception as e:
        print("WARN yahoo 1m failed (%s); via worker" % e, flush=True)
        j = get_json(WORKER_1M)
    n, _d = bars_from_chart(j)
    return n


def merge_bars(*groups):
    by_ts = {}
    for rows in groups:
        for b in rows or []:
            by_ts[b["timestamp"]] = b
    return sorted(by_ts.values(), key=lambda x: x["timestamp"])


def agg_bars(rows, minutes):
    """1 分 K 聚合成 N 分 K。時間桶對齊整分鐘（台北 +8h 為 5/15 的倍數，epoch floor 等價）。"""
    if not rows:
        return []
    step = minutes * 60000
    by = {}
    for b in rows:
        slot = (int(b["timestamp"]) // step) * step
        k = (slot, b.get("date"), b.get("session"))
        x = by.get(k)
        if x is None:
            by[k] = {
                "timestamp": slot,
                "open": b["open"],
                "high": b["high"],
                "low": b["low"],
                "close": b["close"],
                "volume": int(b.get("volume") or 0),
                "date": b.get("date"),
                "session": b.get("session"),
            }
        else:
            x["high"] = max(x["high"], b["high"])
            x["low"] = min(x["low"], b["low"])
            x["close"] = b["close"]
            x["volume"] += int(b.get("volume") or 0)
    return [by[k] for k in sorted(by)]


def fix_future_ts(rows):
    """夜盤標次交易日回移保護（僅 Yahoo 夜盤需要）。
    SSOT: 01_Docs/Yahoo-台指期K線日期慣例.md"""
    if not rows:
        return rows
    limit = int(datetime.now(TZ).timestamp()) + 120
    out = []
    for b in rows:
        ts = int(b["timestamp"]) // 1000
        n = 0
        while ts > limit and n < 10:
            ts -= 86400
            n += 1
        if ts > limit:
            continue
        m = 0
        while m < 10:
            dt = datetime.fromtimestamp(ts, TZ)
            h = dt.hour * 100 + dt.minute
            if not (h >= 1500 or h < 510):
                break
            od = dt.date() - timedelta(days=1) if h < 510 else dt.date()
            if od.weekday() < 5 and od.strftime("%Y%m%d") not in HOLIDAYS:
                break
            ts -= 86400
            m += 1
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


def main():
    do_y = want_yahoo_minute()
    do_f = want_fubon()
    if not do_y and not do_f:
        print("SKIP minute: closed")
        return
    on, od = load_old()
    fn, fd = [], []
    src = []
    if do_f or do_y:
        for attempt in range(2):
            try:
                fn, fd = fetch_fubon_1m()
                break
            except Exception as e:
                print("WARN fubon 1m attempt%d failed (%s)" % (attempt + 1, e), flush=True)
                if attempt == 0:
                    time.sleep(3)
        if fd:
            src.append("fubon 1m FIMTXN (day)")
    yn = []
    if do_y:
        try:
            yn = fetch_yahoo_night_1m()
            if yn:
                src.append("yahoo 1m WTX& (night only)")
        except Exception as e:
            print("WARN yahoo night 1m failed (%s); keep old night" % e, flush=True)
    if not (fn or fd or yn):
        print("SKIP minute: no data, keep old")
        return
    # 日盤＝富邦真小台；夜盤＝Yahoo 大台（小台無夜盤源）
    n1 = fix_future_ts(trim_days(merge_bars(on, yn)))
    d1 = fix_future_ts(trim_days(merge_bars(od, fd)))
    n5 = fix_future_ts(trim_days(agg_bars(n1, 5)))
    d5 = fix_future_ts(trim_days(agg_bars(d1, 5)))
    n15 = fix_future_ts(trim_days(agg_bars(n1, 15)))
    d15 = fix_future_ts(trim_days(agg_bars(d1, 15)))
    src.append("agg 5m 15m")
    payload = {
        "fetchedAt": datetime.now(TZ).strftime("%Y-%m-%d %H:%M:%S"),
        "source": " / ".join(src),
        "contract": CONTRACT,
        "note": "日盤=FIMTXN 真小台；夜盤=WTX& 大台（小台無夜盤分時源，價差 1~3 點）",
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

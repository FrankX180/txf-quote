# 奇摩期貨 StockServices / ApacLibra → data/kline-minute.json
# 台指近月 WTX&。勿打 query1.finance.yahoo.com。
import json
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "kline-minute.json"
TZ = timezone(timedelta(hours=8))
SYM = "WTX%26"
HDR = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0.0.0",
    "Referer": "https://tw.stock.yahoo.com/",
}


def get(url: str) -> dict:
    req = urllib.request.Request(url, headers=HDR)
    with urllib.request.urlopen(req, timeout=25) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


def classify(ts: int):
    dt = datetime.fromtimestamp(int(ts), TZ)
    hm = dt.hour * 100 + dt.minute
    if hm >= 1500 or hm < 500:
        sdate = (dt.date() - timedelta(days=1)).isoformat() if hm < 500 else dt.date().isoformat()
        return "night", sdate, dt
    if 845 <= hm <= 1345:
        return "day", dt.date().isoformat(), dt
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
    n1, d1 = fetch_1m()
    n5, d5 = fetch_apac("5m")
    n15, d15 = fetch_apac("15m")
    payload = {
        "fetchedAt": datetime.now(TZ).strftime("%Y-%m-%d %H:%M:%S"),
        "source": "yahoo tw.stock StockServices/ApacLibra WTX&",
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

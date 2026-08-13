# 期交所前 30 日逐筆成交 → 近月 TX 1 分 K → data/kline-minute.json
import io
import json
import urllib.request
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "kline-minute.json"
TZ = timezone(timedelta(hours=8))
UA = {"User-Agent": "Mozilla/5.0 TXF-quote-page"}
DAYS = 6
MONTH = "202608"
BASE = "https://www.taifex.com.tw/file/taifex/Dailydownload/DailydownloadCSV/Daily_{}.zip"


def download_day(ymd: str):
    url = BASE.format(f"{ymd[:4]}_{ymd[4:6]}_{ymd[6:8]}")
    req = urllib.request.Request(url, headers=UA)
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            blob = r.read()
    except Exception as e:
        print("skip", ymd, type(e).__name__, e)
        return None
    if blob[:2] != b"PK":
        print("skip not-zip", ymd, len(blob))
        return None
    zf = zipfile.ZipFile(io.BytesIO(blob))
    raw = zf.read(zf.namelist()[0])
    return raw.decode("cp950", "replace")


def sess_of(hhmm: str):
    if hhmm >= "1500" or hhmm < "0500":
        return "night"
    if "0845" <= hhmm <= "1345":
        return "day"
    return None


def parse_ticks(text: str, bars: dict):
    for ln in text.splitlines()[1:]:
        p = [x.strip() for x in ln.split(",")]
        if len(p) < 6:
            continue
        if p[1] != "TX" or not p[2].startswith(MONTH):
            continue
        d, tm, px, vol = p[0], p[3].zfill(6), p[4], p[5]
        hhmm = tm[:4]
        sess = sess_of(hhmm)
        if not sess:
            continue
        try:
            price = float(px)
            qty = int(float(vol))
        except ValueError:
            continue
        key = (d, hhmm, sess)
        b = bars.get(key)
        if not b:
            bars[key] = {
                "date": f"{d[:4]}-{d[4:6]}-{d[6:8]}",
                "hhmm": hhmm,
                "session": sess,
                "open": price,
                "high": price,
                "low": price,
                "close": price,
                "volume": qty,
            }
        else:
            if price > b["high"]:
                b["high"] = price
            if price < b["low"]:
                b["low"] = price
            b["close"] = price
            b["volume"] += qty


def to_ts(date_s: str, hhmm: str) -> int:
    y, m, d = int(date_s[:4]), int(date_s[5:7]), int(date_s[8:10])
    hh, mm = int(hhmm[:2]), int(hhmm[2:4])
    return int(datetime(y, m, d, hh, mm, tzinfo=TZ).timestamp() * 1000)


def main():
    today = datetime.now(TZ).date()
    bars = {}
    got = 0
    for i in range(0, 12):
        if got >= DAYS:
            break
        day = today - timedelta(days=i)
        ymd = day.strftime("%Y%m%d")
        text = download_day(ymd)
        if not text:
            continue
        parse_ticks(text, bars)
        got += 1
        print("ok", ymd, "bars", len(bars))
    night, day = [], []
    for (d, hhmm, sess), b in sorted(bars.items()):
        row = {
            "timestamp": to_ts(b["date"], b["hhmm"]),
            "open": b["open"],
            "high": b["high"],
            "low": b["low"],
            "close": b["close"],
            "volume": b["volume"],
            "date": b["date"],
            "session": sess,
        }
        (night if sess == "night" else day).append(row)
    payload = {
        "fetchedAt": datetime.now(TZ).strftime("%Y-%m-%d %H:%M:%S"),
        "contract": "TX",
        "delivery_month": MONTH,
        "night": night,
        "day": day,
    }
    OUT.parent.mkdir(exist_ok=True)
    OUT.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    print("OK night", len(night), "day", len(day))


if __name__ == "__main__":
    main()

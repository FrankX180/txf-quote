# MoneyDJ 期貨 FITX 日／週 K + 結算日月 K → data/kline-daily.json
# 月 K：依 TAIFEX 結算日切段（上期結算翌日～本期結算日），雲端預算；前端只併當日 H/L/C。
import json
import urllib.request
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "kline-daily.json"
SETTLE_PATH = ROOT / "data" / "taifex_settlement_dates.json"
TZ = timezone(timedelta(hours=8))
HDR = {"User-Agent": "Mozilla/5.0"}
BASE = (
    "https://pscnetsecrwd.moneydj.com/b2brwdCommon/jsondata/00/00/00/twstockdata.xdjjson"
    "?x=afterhours-options-common-10&a=FITX&b=0&c={per}&d={n}"
)
MONTH_FROM = date(2013, 1, 1)


def get(per: str, n: int):
    url = BASE.format(per=per, n=n)
    req = urllib.request.Request(url, headers=HDR)
    with urllib.request.urlopen(req, timeout=40) as r:
        j = json.loads(r.read().decode("utf-8", "replace"))
    return (j.get("ResultSet") or {}).get("Result") or []


def fnum(v):
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def to_bars(rows):
    out = []
    for r in reversed(rows or []):
        ds = str(r.get("V1") or "").replace("/", "-")
        c = fnum(r.get("V5"))
        if not ds or c is None:
            continue
        o = fnum(r.get("V2"))
        h = fnum(r.get("V3"))
        l = fnum(r.get("V4"))
        v = fnum(r.get("V6"))
        if o is None:
            o = c
        if h is None:
            h = max(o, c)
        if l is None:
            l = min(o, c)
        y, m, d = [int(x) for x in ds.split("-")[:3]]
        ts = int(datetime(y, m, d, 13, 45, tzinfo=TZ).timestamp() * 1000)
        out.append(
            {
                "timestamp": ts,
                "open": o,
                "high": h,
                "low": l,
                "close": c,
                "volume": int(v or 0),
                "date": ds,
            }
        )
    return out


def third_wednesday(y: int, m: int) -> date:
    d = date(y, m, 1)
    # weekday Mon=0 … Wed=2
    while d.weekday() != 2:
        d += timedelta(days=1)
    return d + timedelta(weeks=2)


def load_settlement_dates():
    raw = json.loads(SETTLE_PATH.read_text(encoding="utf-8"))
    flat = []
    for y_str, months in (raw.get("settlement_dates") or {}).items():
        for _mm, ds in (months or {}).items():
            try:
                flat.append(date.fromisoformat(str(ds)[:10]))
            except ValueError:
                continue
    flat = sorted(set(flat))
    # 2013-01 月 K 需要 2012-12 結算作前界
    if flat and flat[0] >= MONTH_FROM:
        y0, m0 = flat[0].year, flat[0].month
        if m0 == 1:
            prev = third_wednesday(y0 - 1, 12)
        else:
            prev = third_wednesday(y0, m0 - 1)
        if prev not in flat:
            flat.insert(0, prev)
            flat.sort()
    return flat


def month_bars_from_daily(daily, settlements):
    """結算月 K：區間 = (上期結算日+1)～本期結算日（含）。自 2013-01 結算起。"""
    if not daily or not settlements:
        return []
    days = sorted(
        [b for b in daily if b.get("date")],
        key=lambda b: b["date"],
    )
    out = []
    for i in range(1, len(settlements)):
        settle = settlements[i]
        if settle < date(2013, 1, 16):
            continue
        start = settlements[i - 1] + timedelta(days=1)
        end = settle
        chunk = []
        for b in days:
            try:
                bd = date.fromisoformat(str(b["date"])[:10])
            except ValueError:
                continue
            if bd < start:
                continue
            if bd > end:
                break
            chunk.append(b)
        if not chunk:
            continue
        o = chunk[0]["open"]
        h = max(x["high"] for x in chunk)
        l = min(x["low"] for x in chunk)
        c = chunk[-1]["close"]
        vol = sum(int(x.get("volume") or 0) for x in chunk)
        y, m, d = settle.year, settle.month, settle.day
        ts = int(datetime(y, m, d, 13, 45, tzinfo=TZ).timestamp() * 1000)
        out.append(
            {
                "timestamp": ts,
                "open": o,
                "high": h,
                "low": l,
                "close": c,
                "volume": vol,
                "date": settle.isoformat(),
                "start": start.isoformat(),
                "end": end.isoformat(),
                "settle": settle.isoformat(),
                "days": len(chunk),
                "source": "settlement",
            }
        )
    return out


def main():
    from when import want_daily_k

    if not want_daily_k():
        print("SKIP daily k")
        return
    if not SETTLE_PATH.is_file():
        raise SystemExit(f"missing settlement file: {SETTLE_PATH}")

    daily = to_bars(get("D", 9000))
    week = to_bars(get("W", 2000))
    settlements = load_settlement_dates()
    month = month_bars_from_daily(daily, settlements)

    payload = {
        "fetchedAt": datetime.now(TZ).strftime("%Y-%m-%d %H:%M:%S"),
        "source": "moneydj daily/week + TAIFEX settlement month",
        "contract": "FITX",
        "monthRule": "prev_settle+1d .. settle (inclusive); from 2013-01",
        "daily": daily,
        "week": week,
        "month": month,
        "night": daily,
        "day": daily,
    }
    OUT.parent.mkdir(exist_ok=True)
    OUT.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    # 驗證例：2026-08 結算月 = 7/16～8/19
    aug = next((b for b in month if b.get("settle") == "2026-08-19"), None)
    print(
        "OK daily",
        len(daily),
        daily[0]["date"] if daily else "",
        daily[-1]["date"] if daily else "",
        "week",
        len(week),
        "month",
        len(month),
        "first",
        month[0]["date"] if month else "",
        "last",
        month[-1]["date"] if month else "",
    )
    if aug:
        print(
            "CHECK 2026-08",
            "start",
            aug["start"],
            "end",
            aug["end"],
            "days",
            aug["days"],
            "O",
            aug["open"],
            "H",
            aug["high"],
            "L",
            aug["low"],
            "C",
            aug["close"],
        )
    else:
        print("WARN no 2026-08-19 month bar yet")


if __name__ == "__main__":
    main()

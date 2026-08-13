# MoneyDJ 期貨 FITX 日／週／月 K → data/kline-daily.json
# 不併奇摩、不併本機庫。c=D/W/M 各打一次。
import json
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "kline-daily.json"
TZ = timezone(timedelta(hours=8))
HDR = {"User-Agent": "Mozilla/5.0"}
BASE = (
    "https://pscnetsecrwd.moneydj.com/b2brwdCommon/jsondata/00/00/00/twstockdata.xdjjson"
    "?x=afterhours-options-common-10&a=FITX&b=0&c={per}&d={n}"
)


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


def main():
    daily = to_bars(get("D", 9000))
    week = to_bars(get("W", 2000))
    month = to_bars(get("M", 500))
    payload = {
        "fetchedAt": datetime.now(TZ).strftime("%Y-%m-%d %H:%M:%S"),
        "source": "moneydj afterhours-options-common-10 FITX",
        "contract": "FITX",
        "daily": daily,
        "week": week,
        "month": month,
        "night": daily,
        "day": daily,
    }
    OUT.parent.mkdir(exist_ok=True)
    OUT.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    print(
        "OK daily", len(daily), daily[0]["date"] if daily else "", daily[-1]["date"] if daily else "",
        "week", len(week), "month", len(month),
    )


if __name__ == "__main__":
    main()

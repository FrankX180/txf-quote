# CyndiTD TXF_1m.db（bars 1分K）→ data/kline-minute.json
import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "kline-minute.json"
DB = Path(r"E:\CyndiTD\Program\data\TXF_1m.db")
TZ = timezone(timedelta(hours=8))
LOOKBACK_DAYS = 10
KEEP_SESSIONS = 6


def classify(ts: str):
    dt = datetime.strptime(ts[:19], "%Y-%m-%d %H:%M:%S")
    hm = dt.hour * 100 + dt.minute
    if hm >= 1500 or hm < 500:
        if hm < 500:
            sdate = (dt.date() - timedelta(days=1)).isoformat()
        else:
            sdate = dt.date().isoformat()
        return "night", sdate, dt
    if 845 <= hm <= 1345:
        return "day", dt.date().isoformat(), dt
    return None, None, dt


def main():
    if not DB.exists():
        print("SKIP no DB", DB)
        return
    cutoff = (datetime.now(TZ) - timedelta(days=LOOKBACK_DAYS)).strftime("%Y-%m-%d %H:%M:%S")
    conn = sqlite3.connect(f"file:{DB.as_posix()}?mode=ro", uri=True)
    rows = conn.execute(
        "SELECT ts, open, high, low, close, volume FROM bars WHERE ts >= ? ORDER BY ts",
        (cutoff,),
    ).fetchall()
    conn.close()
    night, day = [], []
    for ts, o, h, l, c, v in rows:
        sess, sdate, dt = classify(ts)
        if not sess:
            continue
        bar = {
            "timestamp": int(dt.replace(tzinfo=TZ).timestamp() * 1000),
            "open": float(o),
            "high": float(h),
            "low": float(l),
            "close": float(c),
            "volume": int(v or 0),
            "date": sdate,
            "session": sess,
        }
        (night if sess == "night" else day).append(bar)

    def trim(seq):
        dates = []
        for b in reversed(seq):
            if b["date"] not in dates:
                dates.append(b["date"])
            if len(dates) >= KEEP_SESSIONS:
                break
        keep = set(dates)
        return [b for b in seq if b["date"] in keep]

    night, day = trim(night), trim(day)
    payload = {
        "fetchedAt": datetime.now(TZ).strftime("%Y-%m-%d %H:%M:%S"),
        "source": str(DB),
        "contract": "TXF",
        "night": night,
        "day": day,
    }
    OUT.parent.mkdir(exist_ok=True)
    OUT.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    print("OK night", len(night), "day", len(day), "from", cutoff)


if __name__ == "__main__":
    main()

# TX 近月日K（盤後／一般）→ data/kline-daily.json
import json
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

sys.path.insert(0, r"E:\_Project\BLOK\backend")
from config import DATABASE_URL  # noqa: E402
import psycopg2  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "kline-daily.json"
TZ = timezone(timedelta(hours=8))


def main():
    conn = psycopg2.connect(DATABASE_URL)
    cur = conn.cursor()
    cur.execute(
        """
        SELECT delivery_month FROM futures_daily
        WHERE contract = 'TX' AND session = '盤後' AND open_price IS NOT NULL
          AND trade_date = (SELECT MAX(trade_date) FROM futures_daily WHERE contract = 'TX')
        ORDER BY volume DESC NULLS LAST
        LIMIT 1
        """
    )
    month = cur.fetchone()[0]
    print("month", month)
    cur.execute(
        """
        SELECT trade_date, session, open_price, high_price, low_price, close_price, volume
        FROM futures_daily
        WHERE contract = 'TX' AND delivery_month = %s
          AND open_price IS NOT NULL
        ORDER BY trade_date, session
        """,
        (month,),
    )
    night, day = [], []
    for d, sess, o, h, l, c, v in cur.fetchall():
        if hasattr(d, "year"):
            y, m, dd = d.year, d.month, d.day
        else:
            parts = str(d)[:10].split("-")
            y, m, dd = int(parts[0]), int(parts[1]), int(parts[2])
        ts = int(datetime(y, m, dd, 15 if sess == "盤後" else 9, tzinfo=TZ).timestamp() * 1000)
        bar = {
            "timestamp": ts,
            "open": float(o),
            "high": float(h),
            "low": float(l),
            "close": float(c),
            "volume": int(v or 0),
            "date": d.isoformat() if hasattr(d, "isoformat") else str(d)[:10],
            "session": sess,
        }
        if sess == "盤後":
            night.append(bar)
        elif sess == "一般":
            day.append(bar)
    conn.close()
    payload = {
        "fetchedAt": datetime.now(TZ).strftime("%Y-%m-%d %H:%M:%S"),
        "contract": "TX",
        "delivery_month": month,
        "night": night,
        "day": day,
    }
    OUT.parent.mkdir(exist_ok=True)
    OUT.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    print("OK night", len(night), "day", len(day))


if __name__ == "__main__":
    main()

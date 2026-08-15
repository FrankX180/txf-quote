# MoneyDJ 期貨 FITX 日／週 K + 結算日月 K → data/kline-daily.json
# 月 K：依 TAIFEX 結算日切段（上期結算翌日～本期結算日），雲端預算；前端只併當日 H/L/C。
# MoneyDJ per=D：曆日 OHLC（是否含夜盤尚未對拍 TAIFEX；量 V6 幾乎全空，月 K volume 不可靠）。
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
# 最後一根月 K 的 end 若已過去超過此天數 → 視為結算日表斷鏈，非零 exit
STALE_MONTH_DAYS = 45
# 表尾至少要覆蓋到「今日 + N 天」（不足則用第三週三推估）
HORIZON_DAYS = 400


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
    # MoneyDJ FITX 日 K：每根 = 曆日一條；夜盤是否入棱不由本 API 文件定義。
    # V6（量）實測幾乎全 0，保留欄位但不依賴。
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
    while d.weekday() != 2:
        d += timedelta(days=1)
    return d + timedelta(weeks=2)


def _ym_add(y: int, m: int, n: int):
    m2 = m + n
    y2 = y + (m2 - 1) // 12
    m2 = (m2 - 1) % 12 + 1
    return y2, m2


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
    # 尾端：若表不夠遮到今日+HORIZON，用第三週三推估補（遇假日未順延，僅防靜默斷鏈）
    today = datetime.now(TZ).date()
    need_until = today + timedelta(days=HORIZON_DAYS)
    while flat and flat[-1] < need_until:
        ly, lm = flat[-1].year, flat[-1].month
        ny, nm = _ym_add(ly, lm, 1)
        est = third_wednesday(ny, nm)
        if est <= flat[-1]:
            break
        flat.append(est)
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
        # MoneyDJ 量幾乎全 0；仍加總但不當作可信量能
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

    # 量統計：若幾乎全 0 印出提示（不當失敗）
    nz = sum(1 for b in daily if int(b.get("volume") or 0) > 0)
    vol_note = (
        "MoneyDJ V6 volume mostly empty; month volume not meaningful"
        if nz < max(3, len(daily) // 100)
        else "ok"
    )

    payload = {
        "fetchedAt": datetime.now(TZ).strftime("%Y-%m-%d %H:%M:%S"),
        "source": "moneydj daily/week + TAIFEX settlement month",
        "contract": "FITX",
        "monthRule": "prev_settle+1d .. settle (inclusive); from 2013-01",
        "dailySessionNote": "MoneyDJ calendar daily OHLC; night-session inclusion not verified vs TAIFEX pure day",
        "volumeNote": vol_note,
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
        "vol_nz",
        nz,
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

    # MAJOR：最後月 K end 已過去 >45 天 → 結算日表斷鏈，紅燈
    today = datetime.now(TZ).date()
    if not month:
        raise SystemExit("FAIL empty month bars")
    last_end = date.fromisoformat(str(month[-1].get("end") or month[-1]["date"])[:10])
    age = (today - last_end).days
    if age > STALE_MONTH_DAYS:
        raise SystemExit(
            f"FAIL month stale: last end {last_end.isoformat()} age={age}d > {STALE_MONTH_DAYS}d; update taifex_settlement_dates.json"
        )
    print("settle_ok last_end", last_end.isoformat(), "age_days", age)


if __name__ == "__main__":
    main()

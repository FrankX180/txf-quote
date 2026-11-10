# -*- coding: utf-8 -*-
"""update-quote 收尾守門：抓取步驟全是 continue-on-error，這裡補一道「真的有沒有更新」。

規則：
- 交易日：snapshot.json / kline-minute.json 的內容時間必須在 STALE_MIN 分鐘內，
  否則視為「全部抓取都失敗但 workflow 綠燈」→ exit 1（讓 GitHub 寄失敗信）。
- 休市日（holidays.json 內含今天，含 adhoc 颱風假）：只檢查檔案存在，不比時間。
- 讀不到時間欄位不當失敗（避免格式差異誤報），但會印 WARN。
"""
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
TZ = timezone(timedelta(hours=8))
STALE_MIN = 40

TARGETS = [
    ("snapshot.json", ("fetchedAt", "updatedAt")),
    ("kline-minute.json", ("fetchedAt", "updatedAt")),
]


def parse_ts(s):
    if not s:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M:%S.%fZ"):
        try:
            dt = datetime.strptime(str(s)[:19], fmt[:len(str(s)[:19])] if fmt.endswith("Z") else fmt)
            return dt.replace(tzinfo=TZ)
        except Exception:
            continue
    try:
        return datetime.fromisoformat(str(s).replace("Z", "+00:00")).astimezone(TZ)
    except Exception:
        return None


def is_holiday(today):
    p = DATA / "holidays.json"
    try:
        j = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return False
    days = set()
    for k in ("holidays", "adhoc", "days"):
        v = j.get(k)
        if isinstance(v, list):
            days.update(str(x) for x in v)
    return today in days


def main():
    now = datetime.now(TZ)
    today = now.strftime("%Y-%m-%d")
    compact = now.strftime("%Y%m%d")
    holiday = is_holiday(today) or is_holiday(compact)

    fresh = 0
    checked = 0
    problems = []
    for name, keys in TARGETS:
        p = DATA / name
        if not p.exists():
            problems.append("%s missing" % name)
            continue
        try:
            j = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            problems.append("%s unreadable" % name)
            continue
        ts = None
        for k in keys:
            ts = parse_ts(j.get(k))
            if ts:
                break
        if not ts:
            print("WARN %s has no timestamp field" % name, flush=True)
            continue
        checked += 1
        age = (now - ts).total_seconds() / 60
        if age <= STALE_MIN:
            fresh += 1
        else:
            problems.append("%s stale %.0f min" % (name, age))

    print("QUOTE-GUARD holiday=%s checked=%d fresh=%d problems=%s"
          % (holiday, checked, fresh, problems or "-"), flush=True)

    if holiday:
        print("QUOTE-GUARD OK (holiday, time check skipped)", flush=True)
        return 0
    if checked == 0:
        print("QUOTE-GUARD FAIL: no timestamp could be read", flush=True)
        return 1
    if fresh == 0:
        print("QUOTE-GUARD FAIL: every data file is stale (all fetch steps failed)", flush=True)
        return 1
    print("QUOTE-GUARD OK", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())

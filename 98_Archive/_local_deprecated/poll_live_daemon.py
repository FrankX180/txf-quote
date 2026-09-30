# 開盤時每 15 秒打 Worker ?kind=poll → 寫 D1 內外盤差（不需有人開網頁）
# 用法：& R:\PythonProgram\Python312\python.exe scripts\poll_live_daemon.py
from datetime import datetime, timedelta, timezone
import json
import os
import time
import urllib.request
import urllib.error

TZ = timezone(timedelta(hours=8))
URL = "https://wtx.19850926.xyz/?kind=poll"
INTERVAL = 15
HOL_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "holidays.json"
)
_HOL = None


def holidays():
    """休市日 SSOT（data/holidays.json）；讀不到就當作沒有假日，不阻擋正常盤。"""
    global _HOL
    if _HOL is None:
        try:
            with open(HOL_PATH, encoding="utf-8") as fh:
                _HOL = {str(x) for x in (json.load(fh).get("holidays") or [])}
        except Exception:  # noqa: BLE001
            _HOL = set()
    return _HOL


def in_session(now=None):
    """此刻是否有盤（與前端 nightSessionLive 同語意）：
    凌晨 00:00–05:09 屬前一營業日夜盤，看前一日；其餘時段看當日。
    """
    d = now or datetime.now(TZ)
    hm = d.hour * 100 + d.minute
    ref = d - timedelta(days=1) if hm < 510 else d
    if ref.weekday() >= 5:  # 週六／週日
        return False
    if ref.strftime("%Y%m%d") in holidays():
        return False
    if 845 <= hm <= 1345:
        return True
    if hm >= 1458 or hm < 510:
        return True
    return False


def once():
    req = urllib.request.Request(
        URL,
        headers={
            "User-Agent": "txf-poll-daemon/1",
            "Origin": "https://frankx180.github.io",
        },
    )
    with urllib.request.urlopen(req, timeout=25) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


def main():
    print("poll_live_daemon start interval=%ss url=%s" % (INTERVAL, URL), flush=True)
    while True:
        now = datetime.now(TZ)
        if not in_session(now):
            # 休市睡 30 秒
            time.sleep(30)
            continue
        try:
            j = once()
            imb = j.get("imb") if isinstance(j.get("imb"), dict) else {}
            print(
                now.strftime("%H:%M:%S"),
                "ok" if j.get("ok") else "ng",
                imb.get("sess") or j.get("sess") or j.get("reason") or "",
                "d",
                imb.get("d") if imb.get("d") is not None else j.get("d"),
                flush=True,
            )
        except Exception as e:
            print(now.strftime("%H:%M:%S"), "ERR", e, flush=True)
        time.sleep(INTERVAL)


if __name__ == "__main__":
    main()

# 開盤時每 5 秒打 Worker ?kind=poll → 寫 D1 內外盤差（不需有人開網頁）
# 用法：& R:\PythonProgram\Python312\python.exe scripts\poll_live_daemon.py
from datetime import datetime, timedelta, timezone
import json
import time
import urllib.request
import urllib.error

TZ = timezone(timedelta(hours=8))
URL = "https://wtx.19850926.xyz/?kind=poll"
INTERVAL = 5


def in_session(now=None):
    d = now or datetime.now(TZ)
    hm = d.hour * 100 + d.minute
    wd = d.weekday()  # 0=Mon
    if wd == 6:
        return False
    if wd == 5 and hm >= 510:
        return False
    if 845 <= hm <= 1345:
        return True
    if hm >= 1455 or hm < 510:
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

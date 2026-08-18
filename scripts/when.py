# 台北時段閘門。GITHUB_ACTIONS 且非手動時才擋。
import os
from datetime import datetime, timedelta, timezone

TZ = timezone(timedelta(hours=8))


def now_tw():
    return datetime.now(TZ)


def hm(dt=None):
    d = dt or now_tw()
    return d.hour * 100 + d.minute


def forced():
    if os.environ.get("TXF_FORCE") == "1":
        return True
    if os.environ.get("GITHUB_EVENT_NAME") == "workflow_dispatch":
        return True
    if not os.environ.get("GITHUB_ACTIONS"):
        return True
    return False


def weekday(dt=None):
    return (dt or now_tw()).weekday()  # 0=Mon


def in_session(dt=None):
    d = dt or now_tw()
    h = hm(d)
    wd = d.weekday()
    if wd == 6:
        return False
    if wd == 5:
        return h < 510
    if 840 <= h <= 1350:
        return True
    if h >= 1455 or h < 510:
        return True
    return False


def want_yahoo_quote(dt=None):
    return forced() or in_session(dt)


def want_yahoo_minute(dt=None):
    return forced() or in_session(dt)


def want_fubon(dt=None):
    if forced():
        return True
    d = dt or now_tw()
    h = hm(d)
    wd = d.weekday()
    if wd == 6:
        return False
    if 1350 <= h <= 1440:
        return True
    if 500 <= h <= 830:
        return True
    return False


def want_daily_k(dt=None):
    """日／週／月 K 雲端更新視窗（縮流量：收盤定案 + 凌晨短窗）。"""
    if forced():
        return True
    d = dt or now_tw()
    h = hm(d)
    wd = d.weekday()
    # 周末不抓（夜盤周末已收）
    if wd >= 5:
        return False
    # 日盤收後定案（含結算日 13:45 後寫完整月 K）
    if 1350 <= h <= 1600:
        return True
    # 凌晨短窗：補算昨收（不必整段 05:00–08:40）
    if 500 <= h <= 530:
        return True
    return False


def want_uncovered(dt=None):
    if forced():
        return True
    d = dt or now_tw()
    h = hm(d)
    if d.weekday() >= 5:
        return False
    return 1500 <= h <= 2000


def want_tmf_retail(dt=None):
    """微台與法人同窗；另加平日 08:00–10:00 補洞（前一日若晚上抓失敗可自癒）。"""
    if forced():
        return True
    d = dt or now_tw()
    h = hm(d)
    if d.weekday() >= 5:
        return False
    if 1500 <= h <= 2000:
        return True
    return 800 <= h <= 1000

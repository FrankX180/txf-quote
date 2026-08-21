# 台北時段閘門。GITHUB_ACTIONS 且非手動時才擋。
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

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
    """日盤法人 14:30–15:00；夜盤成交 平日/週六 07:30–10:00。
    當日日盤籌碼已齊 → 下午窗不再重抓；暫行夜盤列已齊 → 早上窗不再重抓。
    報價／五檔不走這扇門。"""
    if forced():
        return True
    d = dt or now_tw()
    h = hm(d)
    wd = d.weekday()
    if wd == 6:
        return False
    # 週六早上：補周五夜盤（07:30 起，讓早盤開盤前圖表已有）
    if wd == 5:
        if not (730 <= h <= 1000):
            return False
        return not night_chips_ready(d)
    if 1430 <= h <= 1500:
        return not day_chips_ready(d)
    if 730 <= h <= 1000:
        return not night_chips_ready(d)
    return False


def _uncovered_payload():
    p = Path(__file__).resolve().parents[1] / "data" / "uncovered.json"
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None


def _ymd8(s):
    return "".join(ch for ch in str(s or "") if ch.isdigit())[:8]


def day_chips_ready(dt=None):
    """今日（台北交易日）日盤法人列已在 uncovered.json。"""
    d = dt or now_tw()
    if d.weekday() >= 5:
        return False
    today = d.strftime("%Y%m%d")
    j = _uncovered_payload()
    if not j:
        return False
    date8 = _ymd8(j.get("date"))
    if date8 != today:
        return False
    hist0 = (j.get("history") or [{}])[0]
    if _ymd8(hist0.get("date")) != today:
        return False
    # 有三大法人淨額即視為日盤籌碼已到位（完整交易日）
    return hist0.get("foreign") is not None and hist0.get("trust") is not None


def night_chips_ready(dt=None):
    """暫行夜盤列已在檔：night.date >= 今日（日盤尚未出時給早盤看）。"""
    d = dt or now_tw()
    today = d.strftime("%Y%m%d")
    j = _uncovered_payload()
    if not j:
        return False
    # 若日盤已是今日，夜盤暫行列本就不該再抓
    if _ymd8(j.get("date")) == today and day_chips_ready(d):
        return True
    night = j.get("night") or {}
    nd = _ymd8(night.get("date"))
    if not nd or nd < today:
        # 週六早上補周五：允許 night 為昨／周五
        if d.weekday() == 5:
            return bool(nd) and night.get("foreignDelta") is not None
        return False
    return night.get("foreignDelta") is not None or night.get("foreign") is not None


def want_tmf_retail(dt=None):
    """微台與法人同窗；日盤已齊則下午不再重抓。"""
    if forced():
        return True
    d = dt or now_tw()
    h = hm(d)
    if d.weekday() >= 5:
        return False
    if 1430 <= h <= 1500:
        return not day_chips_ready(d)
    if 730 <= h <= 1000:
        return not night_chips_ready(d)
    return False

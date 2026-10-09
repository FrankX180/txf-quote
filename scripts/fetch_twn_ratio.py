# -*- coding: utf-8 -*-
# 自建富台換算係數：ratio = 台指 ÷ 富台（兩市同時刻）；rateAdj = wise 即時匯率 ÷ 基準。
# 目的：擺脫對第三方 GAS 的依賴（GAS 降為備援）。→ data/twn-ratio.json
#
# 取樣原則（最重要）：ratio 只取「兩市同時有報價」的那一刻，且台指與富台在**同一次執行**內抓，
# 時間差僅秒級；嚴禁拿不同日的日 K 收盤硬比（會踩「錯配時刻」，誤差可達數百點）。
import json
import urllib.request
import urllib.error
from datetime import datetime, timezone, timedelta
from pathlib import Path

from holiday_guard import is_closed as holiday_closed
from fetch_quote import sess_now, _mis_quote

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
TZ = timezone(timedelta(hours=8))
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0.0.0"
WORKER_TWN = "https://wtx.blok.trading/?kind=twn"
WISE = "https://wise.com/rates/live?source=USD&target=TWD"
BASE_RATE = 31.932  # 匯率基準常數（沿用康和基準，定義 rateAdj=即時/基準）


def _get_json(url, headers, timeout=25):
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


def twn_last():
    """富台指(SGX TWN) 最新價：走自家 Worker（它已代抓 HiStock），不直連第三方。"""
    j = _get_json(WORKER_TWN, {"User-Agent": UA})
    pts = j.get("points") or []
    for p in reversed(pts):
        try:
            v = float(p[1])
        except (TypeError, ValueError, IndexError):
            continue
        if v > 0:
            return v
    return None


def tx_last(session):
    """台指期近月即時價（期交所 MIS，Actions/本機皆可打）。"""
    q = _mis_quote(session) or {}
    try:
        v = float(q.get("CLastPrice"))
        return v if v > 0 else None
    except (TypeError, ValueError):
        return None


def wise_usd_twd():
    j = _get_json(WISE, {"User-Agent": UA})
    return float(j.get("value"))


def main():
    DATA.mkdir(exist_ok=True)
    now = datetime.now(TZ)
    now_iso = now.strftime("%Y-%m-%d %H:%M:%S")
    p = DATA / "twn-ratio.json"
    prev = {}
    if p.exists():
        try:
            prev = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            prev = {}

    out = dict(prev)
    out["updatedAt"] = now_iso

    # --- 匯率修正（每次執行都更新 live）---
    try:
        live = wise_usd_twd()
        out["liveRate"] = live
        out["baseRate"] = BASE_RATE
        out["rateAdj"] = round(live / BASE_RATE, 9)
    except Exception as e:  # noqa: BLE001
        print("WARN wise failed: %s" % e, flush=True)

    # --- ratio（只在兩市同時開盤時更新；休市沿用上一筆）---
    sess = sess_now(now)
    if sess in ("day", "night") and not holiday_closed(now):
        tx = tx_last(sess)
        twn = twn_last()
        if tx and twn:
            out["ratio"] = round(tx / twn, 6)
            out["txPx"] = tx
            out["twnPx"] = twn
            out["ratioSession"] = sess
            out["ratioAt"] = now_iso
            out.pop("seed", None)  # 真值落地後清掉接續標記
            print("OK ratio=%.6f tx=%.2f twn=%.2f sess=%s" % (out["ratio"], tx, twn, sess), flush=True)
        else:
            print("WARN ratio skip (tx=%s twn=%s)" % (tx, twn), flush=True)
    else:
        print("INFO ratio frozen (sess=%s holiday=%s)" % (sess or "-", holiday_closed(now)), flush=True)

    # --- 台指前日結算（供頁面算漲跌）＝日盤收盤 ---
    # 正確來源＝夜盤參考價 night.CRefPrice（＝日盤收盤；實測 49357，與期交所/GAS 一致）；
    # 退回 day.CLastPrice（1 分收，可能差數點）。
    try:
        snap = json.loads((DATA / "snapshot.json").read_text(encoding="utf-8"))
        dc = (snap.get("night") or {}).get("CRefPrice") or (snap.get("day") or {}).get("CLastPrice")
        if dc:
            out["dayClose"] = int(float(dc))
    except Exception:
        pass

    p.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print("WROTE %s %s" % (p, json.dumps(out, ensure_ascii=False)), flush=True)


if __name__ == "__main__":
    main()

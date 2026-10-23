# -*- coding: utf-8 -*-
"""富台換算係數：ratio = 台指 ÷ 富台（同一時刻）。

修正（2026-10-23，老公指出的颱風假估值錯誤）：
  舊版休市時仍拿 MIS 的 CLastPrice 去除富台即時價。休市日台指已收盤，
  CLastPrice 是「上一交易日收盤價」，富台卻還在跑 → 拿舊價除新價，
  比值被灌水（10/23 實測 11.7546 vs 同時刻真值 11.5019，+2.2%，估值多約 1,000 點）。
  現在：台指報價日期必須是今天才取樣，否則沿用舊值。寧可舊，不可錯。

設計原則（老公 10-23）：
  - 不用匯率：兩邊都是當下報價，比值本身就是換算關係。
  - 不分日盤／夜盤：只留一個比值，減少分支與維護。
  - 休市日不取樣：休市時台指本來就沒有新報價，取樣必然錯刻。

輸出 → data/twn-ratio.json：ratio、txPx、twnPx、ratioAt、dayClose
"""
import json
import sys
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

from holiday_guard import is_closed as holiday_closed
from fetch_quote import sess_now, _mis_quote

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
TZ = timezone(timedelta(hours=8))
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0.0.0"
WORKER_TWN = "https://wtx.blok.trading/?kind=twn"
RATIO_MIN, RATIO_MAX = 8.0, 16.0  # 常識範圍；超出視為資料錯刻，不寫入


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


def tx_quote(session):
    """台指期近月報價（期交所 MIS）。回傳 (價格, 報價日期字串)。"""
    q = _mis_quote(session) or {}
    try:
        v = float(q.get("CLastPrice"))
    except (TypeError, ValueError):
        return None, ""
    return (v if v > 0 else None), str(q.get("CDate") or "")


def main():
    DATA.mkdir(exist_ok=True)
    now = datetime.now(TZ)
    now_iso = now.strftime("%Y-%m-%d %H:%M:%S")
    today = now.strftime("%Y%m%d")
    p = DATA / "twn-ratio.json"

    prev = {}
    if p.exists():
        try:
            prev = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            prev = {}

    out = dict(prev)
    out["updatedAt"] = now_iso
    # 舊版欄位一律清掉（匯率、分盤倍數都已廢除）
    for k in ("liveRate", "baseRate", "rateAdj", "ratioDay", "ratioNight", "seed"):
        out.pop(k, None)

    # --- ratio：只有「台指今天真的有報價」且「非休市」才取樣 ---
    sess = sess_now(now)
    if sess not in ("day", "night"):
        print("SKIP outside session (%s)" % (sess or "-"), flush=True)
    elif holiday_closed(now):
        print("SKIP holiday: 台指無今日報價，取樣必然錯刻", flush=True)
    else:
        tx, qdate = tx_quote(sess)
        twn = twn_last()
        if tx is None or not twn:
            print("WARN skip (tx=%s twn=%s)" % (tx, twn), flush=True)
        elif qdate != today:
            print("WARN skip: TX 報價日 %s != 今天 %s（休市或延遲，避免錯刻）" % (qdate or "-", today), flush=True)
        else:
            r = tx / twn
            if RATIO_MIN < r < RATIO_MAX:
                out["ratio"] = round(r, 6)
                out["txPx"] = tx
                out["twnPx"] = twn
                out["ratioSession"] = sess
                out["ratioAt"] = now_iso
                print("OK ratio=%.6f tx=%.2f twn=%.2f sess=%s" % (r, tx, twn, sess), flush=True)
            else:
                print("WARN ratio out of range %.6f (tx=%.2f twn=%.2f)" % (r, tx, twn), flush=True)

    # --- 台指前日結算價（供頁面算漲跌；期交所漲跌係以前日結算價為基準）---
    # 正確來源＝夜盤參考價 night.CRefPrice（＝期交所當日結算價）
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
    try:
        main()
    except Exception as e:
        print("FAIL %s" % str(e).encode("ascii", "replace").decode())
        sys.exit(1)

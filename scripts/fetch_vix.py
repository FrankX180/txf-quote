# VIXTWN 日收盤 → data/vix.json
# SSOT：本檔＝FuturesHTML 的 VIX 歷史唯一來源，不讀任何外部專案，也不受本機開機影響。
# 來源：期交所官網 VIX 月檔（log2data/YYYYMMnew.txt，只保留近月）＝權威日收盤；
#       MIS 即時 getQuoteListVIX 僅補官網還沒有的日期（通常＝今日）。
# 官網月檔只滾動保留近 ~3 個月 → 長歷史靠本檔每日累積（冪等併入，不覆蓋既有日期）。
import json
from datetime import datetime
from pathlib import Path

from fetch_uncovered import (
    TZ,
    fetch_taifex_vix_map,
    fetch_mis_vix_last,
)

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "vix.json"
SEED = ROOT / "data" / "uncovered.json"
VIX_SANE_MAX = 100.0  # VIX 合理上限；超過視為污染值不採用


def _want_vix():
    """收盤後定案窗（台北 15:00–18:00，容忍 Actions 排程延遲）；GITHUB_ACTIONS 且非手動才擋。"""
    import os

    if os.environ.get("TXF_FORCE") == "1":
        return True
    if os.environ.get("GITHUB_EVENT_NAME") == "workflow_dispatch":
        return True
    if not os.environ.get("GITHUB_ACTIONS"):
        return True
    d = datetime.now(TZ)
    if d.weekday() >= 5:
        return False
    h = d.hour * 100 + d.minute
    return 1500 <= h <= 1800


def _iso8(s):
    return "".join(ch for ch in str(s or "") if ch.isdigit())[:8]


def _add(m, d, v):
    k = _iso8(d)
    if len(k) != 8:
        return
    try:
        v = float(v)
    except (TypeError, ValueError):
        return
    if 0 < v <= VIX_SANE_MAX:
        m[k] = v


def _load_existing():
    m = {}
    if OUT.exists():
        try:
            blob = json.loads(OUT.read_text(encoding="utf-8"))
        except Exception:
            blob = {}
        for r in blob.get("history") or []:
            _add(m, r.get("date"), r.get("close"))
        latest = blob.get("latest") or {}
        _add(m, latest.get("date"), latest.get("close"))
    return m


def _seed_from_uncovered():
    """首次建立：既有 uncovered.json 的 vix 欄當長歷史種子（僅合理值）。"""
    m = {}
    if not SEED.exists():
        return m
    try:
        blob = json.loads(SEED.read_text(encoding="utf-8"))
    except Exception:
        return m
    for r in blob.get("history") or []:
        _add(m, r.get("date"), r.get("vix"))
    _add(m, (blob.get("today") or {}).get("date"), (blob.get("today") or {}).get("vix"))
    return m


def main():
    if not _want_vix():
        print("SKIP vix (outside window)")
        return

    merged = _load_existing()
    if not merged:
        seeded = _seed_from_uncovered()
        if seeded:
            merged = seeded
            print(f"seed from {SEED.name}: {len(seeded)} dates")

    # 官網月檔＝權威日收盤：覆蓋修正（含近 ~3 個月）
    official = {}
    try:
        official = fetch_taifex_vix_map(3)
    except Exception as e:
        print("WARN vix month", e)
    for d, v in (official or {}).items():
        _add(merged, d, v)
    # MIS 即時：只補官網月檔還沒有的日期（通常＝今日）；
    # 已寫入的日期不再被 MIS 覆蓋，否則即時值跳動會造成每 5 分重寫。
    try:
        mis = fetch_mis_vix_last()
        if mis:
            filled = {
                _iso8(d): v
                for d, v in mis.items()
                if _iso8(d) and _iso8(d) not in merged
            }
            for d, v in filled.items():
                _add(merged, d, v)
            print("OK mis vix", mis, "| new", list(filled))
    except Exception as e:
        print("WARN mis vix", e)

    if not merged:
        print("no vix data")
        return

    hist = [
        {"date": f"{d[:4]}-{d[4:6]}-{d[6:]}", "close": merged[d]}
        for d in sorted(merged, reverse=True)
    ]
    new_latest = hist[0]

    body = {
        "source": "taifex_vix+mis",
        "unit": "VIXTWN",
        "note": "官網月檔僅近月，長歷史由本檔每日累積；不讀任何外部專案",
        "latest": new_latest,
        "history": hist,
    }

    # 冪等：新 payload 與磁碟完全相同 → 不重寫
    if OUT.exists():
        try:
            old = json.loads(OUT.read_text(encoding="utf-8"))
            if all(old.get(k) == body[k] for k in body):
                print(f"vix up to date ({new_latest['date']}={new_latest['close']}); no rewrite")
                return
        except Exception:
            pass

    payload = {
        "fetchedAt": datetime.now(TZ).strftime("%Y-%m-%d %H:%M:%S"),
        **body,
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"OK vix {len(hist)} dates, latest={new_latest}")


if __name__ == "__main__":
    main()

# 從 Worker 拉當日內外盤差分鐘序列 → data/imb-YYYYMMDD.json（收盤歸檔用）
from pathlib import Path
from datetime import datetime, timezone, timedelta
import json
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
TZ = timezone(timedelta(hours=8))
LIVE = "https://wtx.blok.trading/?kind=imb"


def day_key(dt=None):
    d = dt or datetime.now(TZ)
    if d.hour < 6:
        d = d - timedelta(days=1)
    return d.strftime("%Y%m%d")


def main():
    from when import forced, in_session

    # 盤中可寫；收盤窗口與強制也跑（歸檔）
    if not forced() and not in_session():
        # 收盤後仍允許：05:00-08:40、13:50-14:55
        now = datetime.now(TZ)
        h = now.hour * 100 + now.minute
        wd = now.weekday()
        if wd == 6:
            print("SKIP imb: sunday")
            return
        if not (500 <= h <= 840 or 1350 <= h <= 1455 or 510 <= h <= 600):
            # 仍抓一次當日檔方便 Actions 每 5 分有機會落檔
            pass

    dk = day_key()
    url = LIVE + "&day=" + dk
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "txf-fetch-imb/1",
            "Origin": "https://frankx180.github.io",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            pack = json.loads(r.read().decode())
    except Exception as e:
        # 不可以「印完就 return」：workflow 會綠燈，而 imb-YYYYMMDD.json 永遠缺那一段
        print("FAIL imb", e)
        raise SystemExit(1)

    DATA.mkdir(exist_ok=True)
    day = pack.get("day") or []
    night = pack.get("night") or []
    path = DATA / ("imb-" + dk + ".json")
    # Worker 的 loadPack 在 D1 異常時回 empty 且 HTTP 200；照寫會把 GitHub 上
    # 還有資料的當日歸檔清空並 commit 上線（2026-11-10 審查 S04#9）
    if not day and not night and path.exists():
        try:
            old = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            old = {}
        if (old.get("day") or old.get("night")):
            print("FAIL imb: worker empty but %s has data; keep old" % path.name, flush=True)
            raise SystemExit(1)
    out = {
        "dayKey": pack.get("dayKey") or dk,
        "day": day,
        "night": night,
        "updatedAt": pack.get("updatedAt"),
        "fetchedAt": datetime.now(TZ).strftime("%Y-%m-%d %H:%M:%S"),
        "source": "worker r2",
    }
    path.write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")
    # 最新指標
    (DATA / "imb-latest.json").write_text(
        json.dumps(out, ensure_ascii=False), encoding="utf-8"
    )
    print(
        "OK imb",
        dk,
        "day",
        len(out["day"]),
        "night",
        len(out["night"]),
        path.name,
    )


if __name__ == "__main__":
    main()

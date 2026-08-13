# Fetch TAIFEX MIS getQuoteList (day + night TXF front month) for GitHub Pages.
import json
import urllib.request
from datetime import datetime, timezone, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
TZ = timezone(timedelta(hours=8))
API = "https://mis.taifex.com.tw/futures/api/getQuoteList"
HEADERS = {
    "Content-Type": "application/json;charset=UTF-8",
    "Origin": "https://mis.taifex.com.tw",
    "Referer": "https://mis.taifex.com.tw/futures/AfterHoursSession/EquityIndices/FuturesDomestic/",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) TXF-quote-page",
}

KEEP_POINTS = 800


def post(body: dict) -> dict:
    req = urllib.request.Request(
        API,
        data=json.dumps(body).encode("utf-8"),
        method="POST",
        headers=HEADERS,
    )
    with urllib.request.urlopen(req, timeout=25) as r:
        return json.loads(r.read().decode("utf-8"))


def pick_txf(quote_list):
    txf = [
        q
        for q in quote_list
        if str(q.get("SymbolID", "")).startswith("TXF")
        and (
            str(q.get("SymbolID")).endswith("-M")
            or str(q.get("SymbolID")).endswith("-F")
        )
    ]
    with_px = [q for q in txf if n_price(q.get("CLastPrice"))]
    return (with_px or txf or [None])[0]


def n_price(v):
    try:
        return float(v) > 0
    except (TypeError, ValueError):
        return False


def slim(q):
    if not q:
        return None
    keys = [
        "SymbolID",
        "DispCName",
        "DispEName",
        "CLastPrice",
        "COpenPrice",
        "CHighPrice",
        "CLowPrice",
        "CRefPrice",
        "CDiff",
        "CDiffRate",
        "CTotalVolume",
        "CBidPrice1",
        "CBidSize1",
        "CAskPrice1",
        "CAskSize1",
        "CDate",
        "CTime",
    ]
    return {k: q.get(k, "") for k in keys}


def append_hist(path: Path, q, now_iso):
    hist = []
    if path.exists():
        hist = json.loads(path.read_text(encoding="utf-8"))
    if not q or not q.get("CLastPrice"):
        return
    pt = {
        "t": now_iso,
        "date": q.get("CDate"),
        "time": q.get("CTime"),
        "px": float(q["CLastPrice"]),
        "vol": int(q["CTotalVolume"] or 0),
        "bid": q.get("CBidPrice1"),
        "ask": q.get("CAskPrice1"),
    }
    if hist and hist[-1].get("date") == pt["date"] and hist[-1].get("time") == pt["time"] and hist[-1].get("px") == pt["px"]:
        return
    # new session date: keep only same CDate + previous overflow
    if hist and pt["date"] and hist[-1].get("date") and hist[-1]["date"] != pt["date"]:
        # night session crosses midnight: keep if last date is previous calendar day
        pass
    hist.append(pt)
    hist = hist[-KEEP_POINTS:]
    path.write_text(json.dumps(hist, ensure_ascii=False), encoding="utf-8")


def main():
    DATA.mkdir(exist_ok=True)
    now = datetime.now(TZ)
    now_iso = now.strftime("%Y-%m-%d %H:%M:%S")
    night = post(
        {"MarketType": "1", "SymbolType": "F", "KindID": "1", "CID": "", "ExpireMonth": ""}
    )
    day = post(
        {"MarketType": "0", "SymbolType": "F", "KindID": "1", "CID": "", "ExpireMonth": ""}
    )
    if night.get("RtCode") != "0" or day.get("RtCode") != "0":
        raise SystemExit(
            "API fail night=%s day=%s" % (night.get("RtCode"), day.get("RtCode"))
        )
    nq = pick_txf(night.get("RtData", {}).get("QuoteList") or [])
    dq = pick_txf(day.get("RtData", {}).get("QuoteList") or [])
    snap = {
        "fetchedAt": now_iso,
        "source": "https://mis.taifex.com.tw/futures/api/getQuoteList",
        "night": slim(nq),
        "day": slim(dq),
    }
    (DATA / "snapshot.json").write_text(
        json.dumps(snap, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    append_hist(DATA / "history-night.json", slim(nq), now_iso)
    append_hist(DATA / "history-day.json", slim(dq), now_iso)
    print("OK", now_iso, "night", (slim(nq) or {}).get("CLastPrice"), "day", (slim(dq) or {}).get("CLastPrice"))


if __name__ == "__main__":
    main()

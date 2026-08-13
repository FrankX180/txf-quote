# 奇摩期貨未平倉頁 → data/uncovered.json（台指 FITX）
import json
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "uncovered.json"
TZ = timezone(timedelta(hours=8))
URL = "https://tw.stock.yahoo.com/future/futures_uncovered.html"
HDR = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0.0.0",
    "Referer": "https://tw.stock.yahoo.com/",
}


def extract(html: str, marker: str):
    i = html.find(marker)
    if i < 0:
        return []
    i = html.find('"list":', i)
    i = html.find("[", i)
    depth = 0
    for j in range(i, len(html)):
        if html[j] == "[":
            depth += 1
        elif html[j] == "]":
            depth -= 1
            if depth == 0:
                return json.loads(html[i : j + 1])
    return []


def num(v):
    if isinstance(v, dict):
        v = v.get("sort", v.get("raw"))
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def main():
    from when import want_uncovered
    if not want_uncovered():
        print("SKIP uncovered")
        return
    req = urllib.request.Request(URL, headers=HDR)
    with urllib.request.urlopen(req, timeout=25) as r:
        html = r.read().decode("utf-8", "replace")
    inst_raw = [
        x for x in extract(html, "FutureUncoveredTable_insInvestorsOpen") if x.get("marketCode") == "FITX"
    ]
    top_raw = [
        x
        for x in extract(html, "FutureUncoveredTable_top10PositionTrader")
        if x.get("marketCode") == "FITX"
    ]
    inst = [
        {
            "name": x.get("insInventorName") or "",
            "long": num(x.get("longPosition")),
            "short": num(x.get("shortPosition")),
            "net": num(x.get("net")),
        }
        for x in inst_raw
    ]
    top = [
        {
            "type": x.get("type") or "",
            "long": num(x.get("longPosition")),
            "longPct": num(x.get("longRate")),
            "short": num(x.get("shortPosition")),
            "shortPct": num(x.get("shortRate")),
            "open": num(x.get("openPosition")),
        }
        for x in top_raw
    ]
    date = ""
    if inst_raw:
        date = str(inst_raw[0].get("date") or "")[:10]
    payload = {
        "fetchedAt": datetime.now(TZ).strftime("%Y-%m-%d %H:%M:%S"),
        "date": date,
        "source": "yahoo futures_uncovered FITX",
        "inst": inst,
        "top": top,
    }
    OUT.parent.mkdir(exist_ok=True)
    OUT.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    print("OK inst", len(inst), "top", len(top), "date", date)


if __name__ == "__main__":
    main()

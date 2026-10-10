# -*- coding: utf-8 -*-
"""颱風假（臨時休市）偵測 → 寫進 data/holidays.json

國定假日由 fetch_holidays.py 預先從證交所日曆取得；颱風假是臨時公告，日曆不會有。
判斷：營業日 09:15 後，鴻海期貨（Yahoo WDHF&）與台積電期貨（WCDF&）今天都沒有日盤成交 → 視為臨時休市。
  - 只看股票期貨：無夜盤，最後成交時間不會被前一晚夜盤污染。
  - 兩檔都無成交才判定；抓取失敗／資料異常一律不判定（fail-safe：寧可漏判，不誤判開市日）。
  - 之後同日再跑若見成交 → 自動撤銷（誤判可自癒）。
輸出：holidays.json 新增 "adhoc": {YYYYMMDD: 說明}，並併入 "holidays" 清單（前端／Actions／Worker 不必改）。
"""
import json
import sys
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "holidays.json"
TZ = timezone(timedelta(hours=8))
URL = ("https://tw.stock.yahoo.com/_td-stock/api/resource/"
       "StockServices.stockList;symbols=WDHF%26,WCDF%26")
SYMS = ("WDHF&", "WCDF&")
REASON = "臨時休市（颱風假）：鴻海期貨 WDHF&、台積電期 WCDF& 日盤無成交"


def last_trade_dates():
    req = urllib.request.Request(URL, headers={"User-Agent": "Mozilla/5.0", "Referer": "https://tw.stock.yahoo.com/"})
    with urllib.request.urlopen(req, timeout=20) as r:
        rows = json.loads(r.read().decode("utf-8", "replace"))
    out = {}
    for x in rows or []:
        s = x.get("symbol")
        t = x.get("regularMarketTime")
        if s in SYMS and t:
            out[s] = datetime.fromisoformat(t.replace("Z", "+00:00")).astimezone(TZ)
    return out


def main(now=None):
    now = now or datetime.now(TZ)
    ymd = now.strftime("%Y%m%d")
    hm = now.hour * 100 + now.minute
    doc = json.loads(OUT.read_text(encoding="utf-8"))
    adhoc = dict(doc.get("adhoc") or {})
    hol = set(doc.get("holidays") or [])
    if now.weekday() >= 5 or (ymd in hol and ymd not in adhoc):
        print("SKIP not a business day %s" % ymd)
        return
    if hm < 915 or hm > 1345:
        print("SKIP outside day session %d" % hm)
        return
    lt = last_trade_dates()
    if len(lt) != len(SYMS):
        print("SKIP incomplete data %s" % sorted(lt))
        return
    if any((now - t).days > 10 for t in lt.values()):
        print("SKIP stale data %s" % {k: v.isoformat() for k, v in lt.items()})
        return
    traded = [s for s, t in lt.items() if t.strftime("%Y%m%d") == ymd and t.hour * 100 + t.minute >= 845]
    changed = False
    if traded:
        if ymd in adhoc:
            adhoc.pop(ymd)
            hol.discard(ymd)
            changed = True
            print("REVOKE %s traded=%s" % (ymd, traded))
        else:
            print("OPEN %s traded=%s" % (ymd, traded))
    elif ymd not in adhoc:
        adhoc[ymd] = REASON
        hol.add(ymd)
        changed = True
        print("CLOSED %s last=%s" % (ymd, {k: v.strftime("%m-%d %H:%M") for k, v in lt.items()}))
    else:
        print("CLOSED(already) %s" % ymd)
    if changed:
        doc["adhoc"] = dict(sorted(adhoc.items()))
        doc["holidays"] = sorted(hol)
        OUT.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print("FAIL %s" % str(e).encode("ascii", "replace").decode())
        sys.exit(1)

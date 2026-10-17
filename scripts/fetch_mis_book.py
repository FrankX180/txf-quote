"""抓 TAIFEX MIS 官方最佳五檔 -> data/mis-book.json（休市時前端只讀此檔，不連 MIS）。

來源：wss://mis.taifex.com.tw/futures/rt/（SockJS，訂閱後推完整五檔 QID 101-122）。
規則：日(-F)／夜(-M)各存一份；新資料必須「有買一且成交時間不早於舊檔」才覆蓋，否則保留舊檔。
失敗不擲例外（exit 0），讓 workflow 其他步驟照跑。
"""
import json
import os
import random
import string
import sys
import time
from datetime import datetime, timezone, timedelta

import websocket  # pip: websocket-client

OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "mis-book.json")
WS_BASE = "wss://mis.taifex.com.tw/futures/rt/"
QNAME = {
    "101": "CBidPrice1", "103": "CBidPrice2", "105": "CBidPrice3", "107": "CBidPrice4", "109": "CBidPrice5",
    "102": "CAskPrice1", "104": "CAskPrice2", "106": "CAskPrice3", "108": "CAskPrice4", "110": "CAskPrice5",
    "113": "CBidSize1", "115": "CBidSize2", "117": "CBidSize3", "119": "CBidSize4", "121": "CBidSize5",
    "114": "CAskSize1", "116": "CAskSize2", "118": "CAskSize3", "120": "CAskSize4", "122": "CAskSize5",
    "125": "CLastPrice", "143": "CTime", "144": "CDate", "404": "CTotalVolume",
}
TW = timezone(timedelta(hours=8))


def rnd(n):
    return "".join(random.choice(string.ascii_letters + string.digits) for _ in range(n))


def sym_base(now):
    """近月契約：第三個週三之後算次月（與 index.html misSymBase 同規則）。"""
    y, m = now.year, now.month
    first_wd = (datetime(y, m, 1).weekday() + 1) % 7  # JS getDay: 0=日
    first_wed = 1 + ((3 - first_wd + 7) % 7)
    if now.day > first_wed + 14:
        m += 1
        if m > 12:
            m, y = 1, y + 1
    return "TXF" + chr(64 + m) + str(y % 10)


def collect(syms, seconds=10):
    got = {}
    t0 = time.time()
    ws = None
    for attempt in range(4):  # MIS 前端偶回 522，換連線重試
        try:
            ws = websocket.create_connection(WS_BASE + rnd(3) + "/" + rnd(8) + "/websocket", timeout=6, origin="https://mis.taifex.com.tw")
            break
        except Exception as e:
            print("connect fail #%d: %s" % (attempt + 1, str(e)[:80]))
            time.sleep(1)
    if ws is None:
        return got
    try:
        ws.settimeout(3)
        while time.time() - t0 < seconds:
            try:
                d = ws.recv()
            except websocket.WebSocketTimeoutException:
                continue
            if d == "o":
                ws.send(json.dumps([json.dumps({"type": "subscribe", "symbols": syms})]))
                ws.send(json.dumps([json.dumps({"type": "refresh"})]))
            elif d.startswith("a"):
                for it in json.loads(d[1:]):
                    o = json.loads(it) if isinstance(it, str) else it
                    if o.get("type") != "quote":
                        continue
                    q = o.get("quote") or {}
                    sym = str(q.get("symbol") or "")
                    vals = q.get("values") or {}
                    f = {QNAME[k]: v for k, v in vals.items() if k in QNAME and v not in (None, "")}
                    if sym in syms and f.get("CLastPrice") is not None:
                        got.setdefault(sym, {}).update(f)
            if len(got) == len(syms) and all(g.get("CBidPrice5") for g in got.values()):
                break
    finally:
        try:
            ws.close()
        except Exception:
            pass
    return got


def stamp(f):
    return str(f.get("CDate", "")) + str(f.get("CTime", "")).zfill(6)


def main():
    base = sym_base(datetime.now(TW))
    syms = [base + "-F", base + "-M"]
    old = {}
    try:
        old = json.load(open(OUT, encoding="utf-8"))
    except Exception:
        old = {}
    got = collect(syms)
    if not got:
        print("no MIS data; keep old file")
        return 0
    out = {"fetchedAt": datetime.now(TW).strftime("%Y-%m-%d %H:%M:%S"), "source": "TAIFEX MIS ws", "day": old.get("day"), "night": old.get("night")}
    changed = False
    for sym, f in got.items():
        sk = "day" if sym.endswith("-F") else "night"
        if f.get("CBidPrice1") is None:
            continue
        o = out.get(sk)
        if o and o.get("sym") == sym and stamp(o.get("f", {})) > stamp(f):
            continue  # 新資料比舊檔還舊，不倒退
        out[sk] = {"sym": sym, "f": f, "ts": int(time.time() * 1000)}
        changed = True
    if changed:
        os.makedirs(os.path.dirname(OUT), exist_ok=True)
        json.dump(out, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print("ok changed=%s syms=%s" % (changed, list(got.keys())))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:
        print("fetch_mis_book error:", e)
        sys.exit(0)

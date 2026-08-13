"""TAIFEX SockJS relay. Bind 127.0.0.1:8720. Cloudflare: txf.19850926.xyz"""
import asyncio
import json
import random
import string
import urllib.request
from datetime import datetime, timezone, timedelta

import websockets
from websockets.asyncio.server import serve

HOST = "127.0.0.1"
PORT = 8720
TZ = timezone(timedelta(hours=8))
QNAME = {
    "101": "CBidPrice1", "103": "CBidPrice2", "105": "CBidPrice3",
    "107": "CBidPrice4", "109": "CBidPrice5",
    "102": "CAskPrice1", "104": "CAskPrice2", "106": "CAskPrice3",
    "108": "CAskPrice4", "110": "CAskPrice5",
    "113": "CBidSize1", "115": "CBidSize2", "117": "CBidSize3",
    "119": "CBidSize4", "121": "CBidSize5",
    "114": "CAskSize1", "116": "CAskSize2", "118": "CAskSize3",
    "120": "CAskSize4", "122": "CAskSize5",
    "125": "CLastPrice", "126": "COpenPrice", "129": "CRefPrice",
    "130": "CHighPrice", "131": "CLowPrice", "143": "CTime", "144": "CDate",
    "404": "CTotalVolume", "413": "CSingleVolume",
    "743": "CBestBidPrice", "744": "CBestAskPrice",
    "745": "CBestBidSize", "746": "CBestAskSize",
    "1019": "DispCName", "1020": "DispEName",
}
API = "https://mis.taifex.com.tw/futures/api/getQuoteList"
HDR = {
    "Content-Type": "application/json;charset=UTF-8",
    "Origin": "https://mis.taifex.com.tw",
    "Referer": "https://mis.taifex.com.tw/futures/AfterHoursSession/EquityIndices/FuturesDomestic/",
    "User-Agent": "Mozilla/5.0 TXF-relay",
}

state = {"night": {}, "day": {}, "symbols": {"night": None, "day": None}, "fetchedAt": ""}
clients = set()
hist_night = []
hist_day = []
KEEP = 900


def rid(n):
    return "".join(random.choices(string.ascii_letters + string.digits, k=n))


def post_list(market):
    body = json.dumps(
        {"MarketType": market, "SymbolType": "F", "KindID": "1", "CID": "", "ExpireMonth": ""}
    ).encode()
    req = urllib.request.Request(API, data=body, method="POST", headers=HDR)
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read().decode())


def pick_txf(quotes):
    rows = [
        q
        for q in quotes
        if str(q.get("SymbolID", "")).startswith("TXF")
        and (str(q.get("SymbolID")).endswith("-M") or str(q.get("SymbolID")).endswith("-F"))
    ]
    good = []
    for q in rows:
        try:
            if float(q.get("CLastPrice") or 0) > 0:
                good.append(q)
        except ValueError:
            pass
    return (good or rows or [None])[0]


def refresh_symbols():
    night = post_list("1")
    day = post_list("0")
    nq = pick_txf((night.get("RtData") or {}).get("QuoteList") or [])
    dq = pick_txf((day.get("RtData") or {}).get("QuoteList") or [])
    if nq:
        state["symbols"]["night"] = nq["SymbolID"]
        merge_rest("night", nq)
    if dq:
        state["symbols"]["day"] = dq["SymbolID"]
        merge_rest("day", dq)
    state["fetchedAt"] = datetime.now(TZ).strftime("%Y-%m-%d %H:%M:%S")
    print("symbols", state["symbols"], flush=True)


def merge_rest(side, q):
    d = state[side]
    for k, v in q.items():
        if v not in (None, ""):
            d[k] = v
    d["SymbolID"] = q.get("SymbolID")


def apply_qid(side, values):
    d = state[side]
    for qid, val in values.items():
        name = QNAME.get(str(qid), str(qid))
        d[name] = val
    last = d.get("CLastPrice")
    if last:
        append_hist(side, d)


def append_hist(side, d):
    bucket = hist_night if side == "night" else hist_day
    try:
        px = float(d.get("CLastPrice"))
    except (TypeError, ValueError):
        return
    pt = {
        "t": datetime.now(TZ).strftime("%Y-%m-%d %H:%M:%S"),
        "date": d.get("CDate"),
        "time": d.get("CTime"),
        "px": px,
        "vol": int(float(d.get("CTotalVolume") or 0)),
    }
    if bucket and bucket[-1].get("time") == pt["time"] and bucket[-1].get("px") == pt["px"]:
        return
    bucket.append(pt)
    if len(bucket) > KEEP:
        del bucket[:-KEEP]


def payload():
    return {
        "type": "state",
        "fetchedAt": state["fetchedAt"],
        "night": state["night"],
        "day": state["day"],
        "histNight": hist_night[-400:],
        "histDay": hist_day[-400:],
    }


async def broadcast():
    if not clients:
        return
    msg = json.dumps(payload(), ensure_ascii=False)
    dead = []
    for ws in list(clients):
        try:
            await ws.send(msg)
        except Exception:
            dead.append(ws)
    for ws in dead:
        clients.discard(ws)


async def upstream():
    while True:
        try:
            refresh_symbols()
            symbols = [s for s in state["symbols"].values() if s]
            if not symbols:
                await asyncio.sleep(5)
                continue
            url = f"wss://mis.taifex.com.tw/futures/rt/{rid(3)}/{rid(8)}/websocket"
            print("upstream", url, symbols, flush=True)
            async with websockets.connect(
                url,
                origin="https://mis.taifex.com.tw",
                additional_headers=[
                    ("User-Agent", "Mozilla/5.0"),
                    ("Referer", "https://mis.taifex.com.tw/futures/"),
                ],
                ping_interval=20,
                ping_timeout=20,
                max_size=2**22,
            ) as ws:
                async for raw in ws:
                    if raw == "o":
                        sub = {"type": "subscribe", "symbols": symbols}
                        await ws.send(json.dumps([json.dumps(sub, separators=(",", ":"))]))
                        await ws.send(json.dumps([json.dumps({"type": "refresh"})]))
                        continue
                    if raw == "h":
                        continue
                    if not isinstance(raw, str) or not raw.startswith("a"):
                        continue
                    arr = json.loads(raw[1:])
                    changed = False
                    for item in arr:
                        obj = json.loads(item) if isinstance(item, str) else item
                        if obj.get("type") != "quote":
                            continue
                        q = obj.get("quote") or {}
                        sym = q.get("symbol") or (q.get("values") or {}).get("55")
                        values = q.get("values") or {}
                        if sym == state["symbols"].get("night"):
                            apply_qid("night", values)
                            changed = True
                        elif sym == state["symbols"].get("day"):
                            apply_qid("day", values)
                            changed = True
                    if changed:
                        state["fetchedAt"] = datetime.now(TZ).strftime("%Y-%m-%d %H:%M:%S")
                        await broadcast()
        except Exception as e:
            print("upstream err", type(e).__name__, e, flush=True)
            await asyncio.sleep(2)


async def client_ws(ws):
    clients.add(ws)
    try:
        await ws.send(json.dumps(payload(), ensure_ascii=False))
        async for _ in ws:
            pass
    finally:
        clients.discard(ws)


async def process_request(connection, request):
    upgrade = request.headers.get("Upgrade", "")
    if upgrade.lower() == "websocket":
        return None
    path = request.path.split("?", 1)[0]
    if path in ("/", "/health"):
        return connection.respond(200, "ok")
    if path == "/quote":
        return connection.respond(200, json.dumps(payload(), ensure_ascii=False))
    return connection.respond(404, "no")


async def main():
    print(f"relay http://{HOST}:{PORT}  (tunnel txf.19850926.xyz)", flush=True)
    async with serve(
        client_ws,
        HOST,
        PORT,
        process_request=process_request,
        origins=None,
    ):
        await upstream()


if __name__ == "__main__":
    asyncio.run(main())

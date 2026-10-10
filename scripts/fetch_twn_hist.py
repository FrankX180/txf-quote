# -*- coding: utf-8 -*-
# 富台（SGX TWN1!）1 分 K 歷史存檔 → data/twn-hist.json
#
# 動機：HiStock 只保留最近約一個半盤，台股休市（連假）時 SGX 仍有日／夜盤，沒人存就永遠斷在那裡。
# 來源：TradingView 公開 WebSocket（免登入；延遲約 10 分，供歷史回補足夠；最新一小段由前端用 HiStock 接）。
# 與 update-quote 分開、不套休市閘門：SGX 在台灣假日照常交易，必須照抓照存。
# 輸出：{"updatedAt", "symbol", "bars": [[epoch_sec, close], ...]}，與舊檔以時間戳合併，保留 14 天。
import json
import random
import re
import string
import sys
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

import websocket  # pip install websocket-client

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "twn-hist.json"
TZ = timezone(timedelta(hours=8))
SYMBOL = "SGX:TWN1!"
N_BARS = 4000
KEEP_SEC = 14 * 86400


def _msg(m, p):
    s = json.dumps({"m": m, "p": p}, separators=(",", ":"))
    return "~m~%d~m~%s" % (len(s), s)


def fetch_bars(timeout=25):
    ws = websocket.create_connection(
        "wss://data.tradingview.com/socket.io/websocket",
        origin="https://www.tradingview.com", timeout=timeout)
    cs = "cs_" + "".join(random.choices(string.ascii_lowercase, k=12))
    try:
        ws.send(_msg("set_auth_token", ["unauthorized_user_token"]))
        ws.send(_msg("chart_create_session", [cs, ""]))
        ws.send(_msg("resolve_symbol", [cs, "sds_sym_1", '={"symbol":"%s","session":"extended"}' % SYMBOL]))
        ws.send(_msg("create_series", [cs, "sds_1", "s1", "sds_sym_1", "1", N_BARS, ""]))
        t0 = time.time()
        while time.time() - t0 < timeout:
            d = ws.recv()
            if d.startswith("~m~") and re.fullmatch(r"~m~\d+~m~~h~\d+", d):
                ws.send(d)  # heartbeat echo
                continue
            for part in re.split(r"~m~\d+~m~", d):
                if not part.startswith("{"):
                    continue
                j = json.loads(part)
                if j.get("m") in ("critical_error", "symbol_error", "series_error"):
                    raise RuntimeError("TV error: %s" % part[:200])
                if j.get("m") == "timescale_update":
                    s = j["p"][1].get("sds_1", {}).get("s")
                    if s:
                        return [(int(b["v"][0]), float(b["v"][4])) for b in s]
        raise RuntimeError("TV timeout: no bars")
    finally:
        try:
            ws.close()
        except Exception:
            pass


def main():
    bars = fetch_bars()
    old = {}
    if OUT.exists():
        try:
            for t, c in json.loads(OUT.read_text(encoding="utf-8")).get("bars", []):
                old[int(t)] = float(c)
        except Exception:
            old = {}
    for t, c in bars:
        if c > 0:
            old[t] = c
    cut = max(old) - KEEP_SEC
    merged = sorted((t, c) for t, c in old.items() if t >= cut)
    out = {
        "updatedAt": datetime.now(TZ).strftime("%Y-%m-%d %H:%M:%S"),
        "symbol": SYMBOL,
        "note": "TradingView 1 分 K 收盤（延遲約 10 分）；epoch 秒；前端與 HiStock 即時尾巴拼接",
        "bars": [[t, c] for t, c in merged],
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(out, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    f = lambda t: datetime.fromtimestamp(t, TZ).strftime("%m-%d %H:%M")
    print("OK bars=%d new=%d range=%s..%s last=%.2f" % (len(merged), len(bars), f(merged[0][0]), f(merged[-1][0]), merged[-1][1]), flush=True)


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print("FAIL %s" % str(e).encode("ascii", "replace").decode(), flush=True)
        sys.exit(1)

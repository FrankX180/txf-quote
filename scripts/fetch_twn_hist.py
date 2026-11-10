# -*- coding: utf-8 -*-
# 富台（SGX TWN1!）1 分 K 歷史存檔 → data/twn-hist.json
#
# 動機：HiStock 只保留最近約一個半盤，台股休市（連假）時 SGX 仍有日／夜盤，沒人存就永遠斷在那裡。
# 來源：TradingView 公開 WebSocket（免登入；延遲約 10 分，供歷史回補足夠；最新一小段由前端用 HiStock 接）。
# 與 update-quote 分開、不套休市閘門：SGX 在台灣假日照常交易，必須照抓照存。
# 輸出：{"updatedAt", "symbol", "bars": [[epoch_sec, close], ...]}，與舊檔以時間戳合併，保留 14 天。
import json
import os
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
META = ROOT / "data" / "twn-hist-meta.json"
TZ = timezone(timedelta(hours=8))
SYMBOL = "SGX:TWN1!"
N_BARS = 4000
KEEP_SEC = 14 * 86400


def _msg(m, p):
    s = json.dumps({"m": m, "p": p}, separators=(",", ":"))
    return "~m~%d~m~%s" % (len(s), s)


# TradingView 的 WS 歷史資料端點會整組輪替：2026-11-10 22:05 實測 data.* 完全不再推
# timescale_update（只回 session_id／series_loading／heartbeat，等到超時），同分鐘
# prodata.* 0.8 秒、widgetdata.* 3.3 秒就回 4000 根。故改成清單，重試時逐個換端點。
WS_URLS = [
    "wss://prodata.tradingview.com/socket.io/websocket",
    "wss://widgetdata.tradingview.com/socket.io/websocket",
    "wss://data.tradingview.com/socket.io/websocket",
]


def fetch_bars(timeout=25, url=None):
    ws = websocket.create_connection(
        url or os.environ.get("TV_WS_URL") or WS_URLS[0],
        origin="https://www.tradingview.com", timeout=timeout)
    cs = "cs_" + "".join(random.choices(string.ascii_lowercase, k=12))
    try:
        ws.send(_msg("set_auth_token", ["unauthorized_user_token"]))
        ws.send(_msg("chart_create_session", [cs, ""]))
        ws.send(_msg("resolve_symbol", [cs, "sds_sym_1", '={"symbol":"%s","session":"extended"}' % SYMBOL]))
        ws.send(_msg("create_series", [cs, "sds_1", "s1", "sds_sym_1", "1", N_BARS, ""]))
        t0 = time.time()
        debug = os.environ.get("TV_DEBUG") == "1"
        best = None
        grace_until = 0.0
        while True:
            remain = timeout - (time.time() - t0)
            if remain <= 0 or (grace_until and time.time() > grace_until):
                break
            # recv 是逐次計時：不把剩餘預算壓進 settimeout，單次 attempt 會爆到兩倍 timeout
            # （原註解宣稱 25 秒、實際可跑到 50 秒；Rumi 2026-11-10 審查）
            # 寬限期間再收緊上限，否則 grace_until 只是迴圈頂端檢查、單次 recv 仍可阻塞 10 秒
            cap = 10
            if grace_until:
                cap = min(cap, max(0.2, grace_until - time.time()))
            ws.settimeout(max(0.2, min(remain, cap)))
            try:
                d = ws.recv()
            except websocket.WebSocketTimeoutException:
                continue
            if debug:
                print("TVDBG %s" % repr(d[:300]).encode("ascii", "replace").decode(), flush=True)
            # heartbeat 可能與其他訊息黏在同一個 frame，逐一抽出來回。
            # 長度前綴不能寫死：心心計數到兩位數時 frame 會變成 ~m~5~m~~h~10。
            for hb in re.finditer(r"~m~(\d+)~m~~h~(\d+)", d):
                ws.send("~m~%s~m~~h~%s" % (hb.group(1), hb.group(2)))
            for part in re.split(r"~m~\d+~m~", d):
                if not part.startswith("{"):
                    continue
                j = json.loads(part)
                if j.get("m") in ("critical_error", "symbol_error", "series_error"):
                    raise RuntimeError("TV error: %s" % part[:200])
                if j.get("m") == "timescale_update":
                    s = j["p"][1].get("sds_1", {}).get("s")
                    if s:
                        got = [(int(b["v"][0]), float(b["v"][4])) for b in s]
                        if best is None or len(got) >= len(best):
                            best = got
                        # 再收 1.5 秒：若 TV 把歷史拆成多則訊息，只取第一則會靜默截斷
                        grace_until = time.time() + 1.5
        if best:
            return best
        raise RuntimeError("TV timeout: no bars")
    finally:
        try:
            ws.close()
        except Exception:
            pass


def fetch_bars_retry(attempts=3, timeout=25):
    # 單次連線太脆：2026-11-05 16:08Z 一次 TV 沒回 timescale_update（25 秒空等）就整個 job 失敗、
    # 寄失敗信給老公。TV 是公開未授權端點，偶發不給資料是常態，重試兩次即可吃掉。
    # 連兩次以上失敗才視為真異常（仍 exit 1，不吞掉資料缺口）。
    # 2026-11-10 21:20 起連四次失敗，log 全部是 25 秒空等。曾誤判為「首連慢」而放寬到 45 秒，
    # 結果 21:50 那輪仍三次空等 → 真正原因是 data.tradingview.com 這個端點不給歷史資料了。
    # 現改成每次重試換一個端點（prodata → widgetdata → data），單次 25 秒就够。
    last = None
    for i in range(1, attempts + 1):
        url = WS_URLS[(i - 1) % len(WS_URLS)]
        try:
            bars = fetch_bars(timeout=timeout, url=url)
            if bars:
                print("OK endpoint=%s" % url, flush=True)
                return bars
            raise RuntimeError("TV returned 0 bars")
        except Exception as e:
            last = e
            print("WARN TV attempt %d/%d %s failed: %s"
                  % (i, attempts, url, str(e).encode("ascii", "replace").decode()), flush=True)
            if i < attempts:
                time.sleep(3)
    raise last


def _load_bars_from_git():
    """本機舊檔讀不回時，從 git HEAD 取回同一份（CI 上「改名保留」會在 runner 結束時消失）。"""
    import subprocess
    try:
        r = subprocess.run(["git", "show", "HEAD:data/twn-hist.json"],
                           cwd=str(ROOT), capture_output=True, timeout=60)
        if r.returncode != 0 or not r.stdout:
            return {}
        data = json.loads(r.stdout.decode("utf-8", "replace"))
        return {int(t): float(c) for t, c in data.get("bars", [])}
    except Exception:
        return {}


def main():
    bars = fetch_bars_retry()
    old = {}
    if OUT.exists():
        # 讀不回舊檔：把它改名留下（不删、可回溯）再繼續，不要 hard fail。
        # hard fail 會和 twn-hist.yml 的 cancel-in-progress 形成死循環（被取消→截斷檔→每次 raise→
        # worker 每 8 分 dispatch 一次永遠好不了）；改名保留就够了，舊檔不會被默默覆蓋。
        try:
            for t, c in json.loads(OUT.read_text(encoding="utf-8")).get("bars", []):
                old[int(t)] = float(c)
        except Exception as e:
            # 「改名保留」在 CI 上等於丟掉（runner 結束就沒了），而 commit 只帶新檔
            # （4000 根約 2.8 天），14 天歷史會被静默縮水。先從 git HEAD 取回，再不行就讓 job 紅燈。
            bad = OUT.with_name(OUT.name + ".bad-" + datetime.now(TZ).strftime("%Y%m%d-%H%M%S"))
            try:
                OUT.replace(bad)
            except Exception:
                pass
            old = _load_bars_from_git()
            if old:
                print("WARN local twn-hist unreadable (%s); recovered %d bars from git HEAD"
                      % (e, len(old)), flush=True)
            else:
                raise RuntimeError("twn-hist unreadable and git HEAD unavailable: %s" % e)
    for t, c in bars:
        if c > 0:
            old[t] = c
    # 錨點取 min(最後一根, 現在)：TV 若吐未來時間戳，用 max() 會把合法歷史整段砍掉
    cut = min(max(old), int(time.time())) - KEEP_SEC
    merged = sorted((t, c) for t, c in old.items() if t >= cut)
    out = {
        "updatedAt": datetime.now(TZ).strftime("%Y-%m-%d %H:%M:%S"),
        "symbol": SYMBOL,
        "note": "TradingView 1 分 K 收盤（延遲約 10 分）；epoch 秒；前端與 HiStock 即時尾巴拼接",
        "bars": [[t, c] for t, c in merged],
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    # 原子寫：tmp → os.replace。twn-hist.yml 是 cancel-in-progress，run 被砍在中途時
    # 直接覆寫主檔會留下截斷 JSON（之後每次讀都炸），改名換檔才不會壞到主檔。
    def _atomic_write(path, text):
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(text, encoding="utf-8")
        os.replace(tmp, path)
    _atomic_write(OUT, json.dumps(out, ensure_ascii=False, separators=(",", ":")))
    # 小檔：只給 Worker 的缺口巡檢讀（幾十 bytes），避免每次抓 235KB 完整檔（2026-10-23）
    _atomic_write(META, json.dumps({"lastTs": merged[-1][0], "count": len(merged), "updatedAt": out["updatedAt"]},
                                   ensure_ascii=False, separators=(",", ":")))
    f = lambda t: datetime.fromtimestamp(t, TZ).strftime("%m-%d %H:%M")
    print("OK bars=%d new=%d range=%s..%s last=%.2f" % (len(merged), len(bars), f(merged[0][0]), f(merged[-1][0]), merged[-1][1]), flush=True)


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print("FAIL %s" % str(e).encode("ascii", "replace").decode(), flush=True)
        sys.exit(1)

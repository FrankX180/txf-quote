# -*- coding: utf-8 -*-
"""富邦逐筆 → 補 D1 內外盤缺口（Yahoo 為主、富邦為輔）。

流程：讀 D1 當盤 imb → 沒缺口就結束（不登入）→ 有缺口才 apikey_login 換行情 token、
立刻 logout → 純 HTTP 抓整盤逐筆（bid/ask 判內外盤，夾在中間用 tick rule）→
以前後 Yahoo 真值為錨換算後補進缺的分鐘。

- 絕不覆寫 Yahoo 真值：本腳本寫的列 ts = t + 59999 當標記，只更新自己寫過的列。
- 雲端只放：FUBON_ID、FUBON_API_KEY、FUBON_CERT_B64（憑證 base64）、FUBON_CERT_PASS（選填）、
  CLOUDFLARE_API_KEY。**不放交易密碼**。本機沒設 env 時讀 CyndiTD config 方便測試。
- 用法：python scripts/fubon_imb_fill.py [--dry-run] [--day YYYYMMDD --session day|night]
"""
import base64
import datetime
import json
import os
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import deploy_worker as dw  # noqa: E402  (CF key / D1 id)

TPE = datetime.timezone(datetime.timedelta(hours=8))
MD = "https://api.fugle.tw/marketdata/v1.0/futopt/intraday/"
MARK = 59999  # ts = t + MARK：本腳本補的定稿列（前後都有 Yahoo 錨）
PROV = 59998  # ts = t + PROV：尾端暫定列（Yahoo 還沒回來，之後重算）
OWN = (MARK, PROV)
MONTHS = "ABCDEFGHIJKL"  # 期貨月份碼 A=1月..L=12月
DRY = "--dry-run" in sys.argv


def arg(name):
    return sys.argv[sys.argv.index(name) + 1] if name in sys.argv else None


# ── 盤別／交易日（對齊 worker sessionOf / tradingDayKey）──
def session_window(now):
    """回傳 (day_key, session, start, end)；非盤中回傳最近一場（收盤 30 分內仍可補）。"""
    t = now.astimezone(TPE)
    hm = t.hour * 100 + t.minute
    d = t.date()
    if hm < 600:  # 凌晨屬前一日夜盤
        s = datetime.datetime.combine(d - datetime.timedelta(days=1), datetime.time(15, 0), TPE)
        return (s.strftime("%Y%m%d"), "night", s, s + datetime.timedelta(hours=14))
    if hm < 1500:
        s = datetime.datetime.combine(d, datetime.time(8, 45), TPE)
        return (s.strftime("%Y%m%d"), "day", s, s + datetime.timedelta(hours=5))
    s = datetime.datetime.combine(d, datetime.time(15, 0), TPE)
    return (s.strftime("%Y%m%d"), "night", s, s + datetime.timedelta(hours=14))


def near_month_symbol(day_key, session):
    """近月台指期代碼：第三個週三結算；結算日夜盤起換下月。"""
    d = datetime.datetime.strptime(day_key, "%Y%m%d").date()
    first = d.replace(day=1)
    third_wed = first + datetime.timedelta(days=(2 - first.weekday()) % 7 + 14)
    y, m = d.year, d.month
    if d > third_wed or (d == third_wed and session == "night"):
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return "TXF%s%d" % (MONTHS[m - 1], y % 10)


# ── D1 ──
def d1(sql, params=None):
    db_id = d1.db_id = getattr(d1, "db_id", None) or dw.ensure_d1()
    r = dw.call("POST", f"https://api.cloudflare.com/client/v4/accounts/{dw.AID}/d1/database/{db_id}/query",
                {"sql": sql, "params": params or []})
    if not (r and r.get("success")):
        raise SystemExit("D1 FAIL %s" % (r and r.get("errors")))
    return r["result"][0]["results"]


# ── 富邦：登入換 token（用完即 logout；卡住就砍掉整個進程，連線隨進程關閉）──
LOGIN_TIMEOUT = 60


def fubon_token():
    """登入＋換 token＋logout 放在執行緒；逾時直接 os._exit，避免殭屍連線佔配額。"""
    box = {}

    def run():
        try:
            box["tok"] = _login_exchange()
        except BaseException as e:  # noqa: BLE001
            box["err"] = e

    th = threading.Thread(target=run, daemon=True)
    th.start()
    th.join(LOGIN_TIMEOUT)
    if th.is_alive():
        print("FAIL fubon login hung >%ds; force exit (sockets close with process)" % LOGIN_TIMEOUT, flush=True)
        os._exit(3)
    if "err" in box:
        raise SystemExit("fubon login FAIL: %s" % box["err"])
    return box["tok"]


def get_token(symbol):
    """先用 D1 快取的 token（登出後仍有效）；失效才重新登入。登入次數越少越穩。"""
    d1("CREATE TABLE IF NOT EXISTS fubon_tok (id INTEGER PRIMARY KEY CHECK (id = 1), tok TEXT NOT NULL, ts INTEGER NOT NULL)")
    row = d1("SELECT tok FROM fubon_tok WHERE id = 1")
    if row:
        try:
            urllib.request.urlopen(urllib.request.Request(MD + "quote/" + symbol, headers={
                "X-SDK-TOKEN": row[0]["tok"], "User-Agent": "Mozilla/5.0"}), timeout=15).read()
            print("token: cached OK")
            return row[0]["tok"]
        except urllib.error.HTTPError as e:
            if e.code not in (401, 403):
                print("token check HTTP", e.code, "-> relogin")
        except Exception as e:  # noqa: BLE001
            print("token check err", str(e)[:80], "-> relogin")
    tok = fubon_token()
    d1("INSERT INTO fubon_tok (id, tok, ts) VALUES (1, ?, ?) ON CONFLICT(id) DO UPDATE SET tok=excluded.tok, ts=excluded.ts",
       [tok, int(time.time() * 1000)])
    print("token: fresh login")
    return tok


def _login_exchange():
    pid = os.environ.get("FUBON_ID")
    key = os.environ.get("FUBON_API_KEY")
    cert_b64 = os.environ.get("FUBON_CERT_B64")
    cert_pass = os.environ.get("FUBON_CERT_PASS") or None
    cert_path = None
    if not (pid and key and cert_b64):  # 本機測試 fallback
        cfg_dir = Path(r"E:\CyndiTD\Program\config\fubon")
        c = json.loads((cfg_dir / "config.json").read_text(encoding="utf-8"))["fubon"]
        pid = pid or c["id"]
        key = key or c.get("api_key")
        cert_pass = cert_pass or c.get("cert_password") or None
        cert_path = str(cfg_dir / Path(c["cert_path"]).name)
    else:
        fd, cert_path = tempfile.mkstemp(suffix=".pfx")
        with os.fdopen(fd, "wb") as fh:
            fh.write(base64.b64decode(cert_b64))
    from fubon_neo.sdk import FubonSDK

    sdk = FubonSDK()
    try:
        r = sdk.apikey_login(pid, key, cert_path, cert_pass)
        if not getattr(r, "is_success", False):
            raise SystemExit("fubon login FAIL: %s" % getattr(r, "message", r))
        tok = sdk.exchange_realtime_token()
        return tok if isinstance(tok, str) else getattr(tok, "data", tok)
    finally:
        try:
            sdk.logout()
        except Exception:
            pass
        if os.environ.get("FUBON_CERT_B64") and cert_path:
            try:
                os.remove(cert_path)
            except OSError:
                pass


def fetch_trades(tok, symbol, session):
    H = {"X-SDK-TOKEN": tok, "User-Agent": "Mozilla/5.0"}
    q = "&session=afterhours" if session == "night" else ""
    out, off = [], 0
    while True:
        u = "%strades/%s?limit=500&offset=%d%s" % (MD, symbol, off, q)
        for attempt in range(3):
            try:
                d = json.loads(urllib.request.urlopen(urllib.request.Request(u, headers=H), timeout=20).read()).get("data") or []
                break
            except urllib.error.HTTPError as e:
                if e.code == 429 and attempt < 2:
                    time.sleep(20)
                    continue
                raise
        out += d
        if len(d) < 500:
            return out
        off += 500
        time.sleep(0.25)  # 富邦 REST 約 300 次/分


def minute_cum(trades, start_ms, end_ms):
    """逐筆 → 每分鐘累計 (inn, outv)；bid/ask 判定，夾在中間用 tick rule。"""
    trades = sorted(trades, key=lambda x: (x["time"], x.get("serial", 0)))
    inn = outv = 0
    last_px, last_side = None, 0
    per = {}
    for x in trades:
        px, sz = x["price"], x["size"]
        if x.get("ask") and px >= x["ask"]:
            side = 1
        elif x.get("bid") and px <= x["bid"]:
            side = -1
        elif last_px is not None and px != last_px:
            side = 1 if px > last_px else -1
        else:
            side = last_side
        if side > 0:
            outv += sz
        elif side < 0:
            inn += sz
        last_px, last_side = px, side or last_side
        per[(x["time"] // 1000) // 60000 * 60000] = (inn, outv)
    cum, cur = {}, None
    for t in range(start_ms, end_ms, 60000):
        cur = per.get(t, cur)
        if cur is not None:
            cum[t] = cur
    return cum


def main():
    now = datetime.datetime.now(TPE)
    day_key, sess, start, end = session_window(now)
    if arg("--day"):
        day_key, sess = arg("--day"), arg("--session")
        start = datetime.datetime.strptime(day_key, "%Y%m%d").replace(
            hour=15 if sess == "night" else 8, minute=0 if sess == "night" else 45, tzinfo=TPE)
        end = start + datetime.timedelta(hours=14 if sess == "night" else 5)
    start_ms = int(start.timestamp() * 1000)
    upto_ms = min(int(end.timestamp() * 1000), (int(now.timestamp() * 1000) // 60000 - 3) * 60000)  # 留 3 分給 Worker 寫入，免誤判缺口
    if upto_ms <= start_ms:
        print("session not started", day_key, sess)
        return
    if now > end + datetime.timedelta(minutes=30) and not arg("--day"):
        print("session ended >30min, skip", day_key, sess)
        return

    rows = d1("SELECT t, d, inn, outv, ts FROM imb WHERE day_key=? AND session=? ORDER BY t", [day_key, sess])
    real = {r["t"]: r for r in rows if (r["ts"] or 0) % 60000 not in OWN and (r["inn"] or 0) > 0 and (r["outv"] or 0) > 0}
    final = {r["t"] for r in rows if (r["ts"] or 0) % 60000 == MARK}
    prov = {r["t"] for r in rows if (r["ts"] or 0) % 60000 == PROV}
    if not real:
        print("no Yahoo anchor yet", day_key, sess)
        return
    first_real = min(real)
    # 只補「D1 完全沒有」或自己的暫定列；Yahoo 壞列（0 值）不可覆寫，列入會害每輪都登入
    have = {r["t"] for r in rows}
    want = [t for t in range(max(first_real, start_ms), upto_ms + 1, 60000) if t not in have or t in prov]
    print("session", day_key, sess, "real", len(real), "final", len(final), "prov", len(prov), "gaps", len(want))
    if not want:
        return

    sym = near_month_symbol(day_key, sess)
    tok = get_token(sym)
    trades = fetch_trades(tok, sym, sess)
    cum = minute_cum(trades, start_ms, upto_ms + 60000)
    print("fubon", sym, "trades", len(trades), "vol", sum(x["size"] for x in trades))

    keys = sorted(t for t in real if t in cum)
    fill = []
    for t in want:
        if t not in cum:
            continue
        prev = max((k for k in keys if k < t), default=None)
        nxt = min((k for k in keys if k > t), default=None)
        if prev is None:
            continue
        P = real[prev]
        if nxt is not None:
            N = real[nxt]
            kin = (N["inn"] - P["inn"]) / (cum[nxt][0] - cum[prev][0]) if cum[nxt][0] > cum[prev][0] else 1.0
            kout = (N["outv"] - P["outv"]) / (cum[nxt][1] - cum[prev][1]) if cum[nxt][1] > cum[prev][1] else 1.0
        else:
            kin = kout = None  # 尾端缺口（Yahoo 還沒回來）：直接疊富邦增量，標暫定
        mark = MARK if kin is not None else PROV
        inn = round(P["inn"] + (cum[t][0] - cum[prev][0]) * (kin or 1.0))
        outv = round(P["outv"] + (cum[t][1] - cum[prev][1]) * (kout or 1.0))
        fill.append((t, outv - inn, inn, outv, mark))
    print("fill", len(fill), "sample", [(datetime.datetime.fromtimestamp(t / 1000, TPE).strftime("%H:%M"), d) for t, d, _, _, _ in fill[:3]])
    if DRY or not fill:
        print("DRY-RUN" if DRY else "nothing")
        return
    for i in range(0, len(fill), 25):
        vals = ",".join("('%s','%s',%d,%d,%d,%d,%d)" % (day_key, sess, t, d, a, b, t + m) for t, d, a, b, m in fill[i:i + 25])
        d1("INSERT INTO imb (day_key, session, t, d, inn, outv, ts) VALUES " + vals +
           " ON CONFLICT(day_key, session, t) DO UPDATE SET d=excluded.d, inn=excluded.inn, outv=excluded.outv, ts=excluded.ts"
           " WHERE imb.ts % 60000 IN (%d, %d)" % OWN)
    print("OK upserted", len(fill))


if __name__ == "__main__":
    main()

# -*- coding: utf-8 -*-
"""重建當日大小微台內外盤（imb3）完整序列。

為什麼需要：Worker 的 syncImb3 只能從「部署後第一次執行」開始累積，
上線當天（或 Worker 換版後）基準不完整，合併口數會遠小於實際成交量。
本腳本用交易所逐筆資料，從該盤別開盤重算到現在，覆寫 imb3 並校正 imb3_cur 游標。

⚠️ 2026-11-02 修正：
- 游標必須用連續月別名（TXF1!／MXF1!／TMF1!），與 Worker 的查詢字串一致，否則 Worker 讀不到游標。
- 不再先 DELETE 再寫（刪了但寫失敗會把資料清空）；改為純 ON CONFLICT 覆寫。
- D1 單一語句最多 100 個 SQL 變數 → 每批 12 列 × 8 欄。
- 總量看最後一分鐘的累計值，不可把每分鐘累計值再加總。

用法：
  python scripts/imb3_backfill.py                 # 目前盤別
  python scripts/imb3_backfill.py --day 20261102 --session night
需要：FUBON_ID／FUBON_API_KEY／FUBON_CERT_B64（或本機 config）、CLOUDFLARE_API_KEY
"""
import datetime
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import fubon_imb_fill as f  # noqa: E402

ROOTS = [("TXF", 20), ("MXF", 5), ("TMF", 1)]  # 單位：1/20 大台口


def main():
    now = datetime.datetime.now(f.TPE)
    day_key, sess, start, end = f.session_window(now)
    if f.arg("--day"):
        day_key, sess = f.arg("--day"), f.arg("--session") or "day"
        start = datetime.datetime.strptime(day_key, "%Y%m%d").replace(
            hour=15 if sess == "night" else 8,
            minute=0 if sess == "night" else 45,
            tzinfo=f.TPE,
        )
        end = start + datetime.timedelta(hours=14 if sess == "night" else 5)

    start_ms = int(start.timestamp() * 1000)
    end_ms = int(min(end.timestamp(), now.timestamp()) * 1000)
    if end_ms <= start_ms:
        print("session not started", day_key, sess)
        return

    tok = f._login_exchange()
    per = {}
    curs = []
    for root, w in ROOTS:
        sym = f.near_month_symbol(day_key, sess).replace("TXF", root, 1)
        alias = root + "1!"
        try:
            trades = f.fetch_trades(tok, sym, sess)
        except Exception as e:  # noqa: BLE001
            print("FETCH_FAIL", sym, str(e)[:80])
            continue
        trades = [x for x in trades if start_ms <= x["time"] / 1000.0 <= end_ms]
        cum = f.minute_cum(trades, start_ms, end_ms)
        vol = sum((x.get("size") or 0) for x in trades)
        for t, (inn, outv) in cum.items():
            p = per.setdefault(t, [0, 0])
            p[0] += inn * w
            p[1] += outv * w
        lt = max(cum) if cum else None
        fin = cum[lt] if lt else (0, 0)
        serial = max((x.get("serial") or 0) for x in trades) if trades else 0
        curs.append((alias, serial, int(fin[0] * w), int(fin[1] * w)))
        print("OK", sym, "trades", len(trades), "vol", vol,
              "in20", int(fin[0] * w), "out20", int(fin[1] * w),
              "taiex_gross", round((fin[0] + fin[1]) * w / 20, 1))

    if not per:
        print("nothing to write")
        return

    lt = max(per)
    tin, tout = per[lt]
    print("LAST_BAR gross_taiex", round((tin + tout) / 20, 1),
          "net_taiex", round((tout - tin) / 20, 1), "minutes", len(per))

    rows = [(day_key, sess, int(t), int(v[1] - v[0]), int(v[0]), int(v[1]), 0,
             int(now.timestamp() * 1000)) for t, v in sorted(per.items())]
    B = 12  # D1：單句最多 100 個綁定變數，12 × 8 = 96
    for i in range(0, len(rows), B):
        chunk = rows[i:i + B]
        ph = ",".join(["(?,?,?,?,?,?,?,?)"] * len(chunk))
        flat = [x for r in chunk for x in r]
        f.d1(
            "INSERT INTO imb3 (day_key, session, t, d20, in20, out20, n, ts) VALUES " + ph +
            " ON CONFLICT(day_key, session, t) DO UPDATE SET d20=excluded.d20, in20=excluded.in20, "
            "out20=excluded.out20, ts=excluded.ts",
            flat,
        )
    print("inserted", len(rows))

    for alias, serial, in20, out20 in curs:
        f.d1(
            "INSERT INTO imb3_cur (day_key, session, sym, serial, in20, out20, last_px, last_side, upd) "
            "VALUES (?,?,?,?,?,?,?,?,?) ON CONFLICT(day_key, session, sym) DO UPDATE SET "
            "serial=excluded.serial, in20=excluded.in20, out20=excluded.out20, upd=excluded.upd",
            [day_key, sess, alias, serial, in20, out20, None, None, int(now.timestamp() * 1000)],
        )
    print("cur upserted", [c[0] for c in curs])


if __name__ == "__main__":
    main()

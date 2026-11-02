# -*- coding: utf-8 -*-
"""重建當日大小微台內外盤（imb3）完整序列。

為什麼需要：Worker 的 syncImb3 只能從「部署後第一次執行」開始累積，
上線當天（或 Worker 換版後）基準不完整，合併口數會遠小於實際成交量。
本腳本用交易所逐筆資料，從該盤別開盤重算到現在，覆寫 imb3 並校正 imb3_cur 游標。

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
MONTHS = f.MONTHS


def sym_of(root, day_key, session):
    """近月合約：沿用 f.near_month_symbol 的第三個週三結算規則，換掉根代碼。"""
    return f.near_month_symbol(day_key, session).replace("TXF", root, 1)


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

    tok = f.get_token("TXF1!")
    per = {}
    curs = []
    for root, w in ROOTS:
        sym = sym_of(root, day_key, sess)
        try:
            trades = f.fetch_trades(tok, sym, sess)
        except Exception as e:  # noqa: BLE001
            print("FETCH_FAIL", sym, str(e)[:80])
            continue
        trades = [x for x in trades if start_ms <= x["time"] / 1000.0 <= end_ms]
        cum = f.minute_cum(trades, start_ms, end_ms)
        for t, (inn, outv) in cum.items():
            p = per.setdefault(t, [0, 0])
            p[0] += inn * w
            p[1] += outv * w
        last_serial = max((x.get("serial") or 0) for x in trades) if trades else 0
        if cum:
            last_t = max(cum)
            fin_in, fin_out = cum[last_t]
        else:
            fin_in = fin_out = 0
        vol = sum((x.get("size") or 0) for x in trades)
        curs.append((sym, last_serial, fin_in * w, fin_out * w))
        print("OK", sym, "trades", len(trades), "vol", vol,
              "in20", round(fin_in * w), "out20", round(fin_out * w))

    if not per:
        print("nothing to write")
        return

    # 用實際成交計算出的最後價位方向為準，重新寫入當日序列（覆寫本腳本管的 day/session）
    f.d1("DELETE FROM imb3 WHERE day_key=? AND session=?", [day_key, sess])
    rows = []
    for t in sorted(per):
        inn, outv = per[t]
        rows.append((day_key, sess, int(t), outv - inn, inn, outv, int(now.timestamp() * 1000)))
    for i in range(0, len(rows), 25):
        vals = ",".join("('%s','%s',%d,%d,%d,%d,%d)" % r for r in rows[i:i + 25])
        f.d1("INSERT INTO imb3 (day_key, session, t, d20, in20, out20, n, ts) VALUES " + vals +
             " ON CONFLICT(day_key, session, t) DO UPDATE SET d20=excluded.d20, in20=excluded.in20, out20=excluded.out20, ts=excluded.ts")

    for sym, serial, in20, out20 in curs:
        f.d1(
            "INSERT INTO imb3_cur (day_key, session, sym, serial, in20, out20, last_px, last_side, upd) "
            "VALUES (?,?,?,?,?,?,?,?,?) ON CONFLICT(day_key, session, sym) DO UPDATE SET "
            "serial=excluded.serial, in20=excluded.in20, out20=excluded.out20, upd=excluded.upd",
            [day_key, sess, sym, serial, in20, out20, None, None, int(now.timestamp() * 1000)],
        )

    tin = sum(v[0] for v in per.values())
    tout = sum(v[1] for v in per.values())
    print("WROTE", len(rows), "minute bars; in20", round(tin), "out20", round(tout),
          "net_taiex", round((tout - tin) / 20, 1), "gross_taiex", round((tout + tin) / 20, 1))


if __name__ == "__main__":
    main()

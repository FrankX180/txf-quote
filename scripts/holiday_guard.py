#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""GitHub Actions 用：判斷「此刻（台北）」市場是否休市，寫入 $GITHUB_OUTPUT 的 state。

state=closed → 週末或 data/holidays.json 內的非週末休市日；否則 state=open。
**夜盤歸屬**：假日只擋日盤與當晚夜盤；凌晨 00:00–05:09 仍屬前一營業日夜盤，
故此時要看「前一日」是否營業——否則 9/29（二）凌晨會因 9/28 休市仍被放行，
而 10/9（五，國慶）凌晨則會被誤擋掉 10/8 的夜盤尾巴。
資料缺失時 fail-open（視為 open），不因日曆檔問題擋掉正常交易日。
"""
import datetime
import json
import os
import sys

TPE = datetime.timezone(datetime.timedelta(hours=8))
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PATH = os.path.join(ROOT, "data", "holidays.json")


def load_holidays(path=PATH):
    try:
        with open(path, encoding="utf-8") as fh:
            return {str(x) for x in (json.load(fh).get("holidays") or [])}
    except Exception as exc:  # noqa: BLE001
        print("[warn] holidays.json 讀取失敗，視為開市: %s" % exc, file=sys.stderr)
        return set()


def is_closed(now=None, holidays=None):
    now = now or datetime.datetime.now(TPE)
    if now.tzinfo is None:
        now = now.replace(tzinfo=TPE)
    hm = now.hour * 100 + now.minute
    # 凌晨 00:00–05:09：屬前一營業日夜盤 → 看前一日；其餘時段看當日
    ref = now - datetime.timedelta(days=1) if hm < 510 else now
    if ref.weekday() >= 5:
        return True
    hol = load_holidays() if holidays is None else holidays
    return ref.strftime("%Y%m%d") in hol


def is_night_settle_window(now=None):
    """週六 05:10–10:00＝週五夜盤籌碼結算窗（期交所盤後三大法人約 06:00–08:00 上架）。

    2026-11-21 加。只給 update-quote 補抓夜盤籌碼用，**不改 is_closed 的全域語意**：
    改全域會讓 fetch_imb／fubon-imb fill／Fubon 1m 跟著在週六跑（審查 R2）。
    前一日（週五）本身休市（連假）則不算，那天沒有夜盤可補。
    """
    now = now or datetime.datetime.now(TPE)
    if now.tzinfo is None:
        now = now.replace(tzinfo=TPE)
    hm = now.hour * 100 + now.minute
    if now.weekday() != 5 or not (510 <= hm <= 1000):
        return False
    friday = now - datetime.timedelta(days=1)
    return friday.weekday() < 5 and friday.strftime("%Y%m%d") not in load_holidays()


def main():
    now = datetime.datetime.now(TPE)
    state = "closed" if is_closed(now) else "open"
    night_window = is_night_settle_window(now)
    print(
        "market=%s date=%s night_window=%s"
        % (state, now.strftime("%Y%m%d"), "true" if night_window else "false")
    )
    out = os.environ.get("GITHUB_OUTPUT")
    if out:
        with open(out, "a", encoding="utf-8") as fh:
            fh.write("state=%s\n" % state)
            fh.write("night_window=%s\n" % ("true" if night_window else "false"))


if __name__ == "__main__":
    main()

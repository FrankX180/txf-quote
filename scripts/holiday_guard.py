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


def main():
    now = datetime.datetime.now(TPE)
    state = "closed" if is_closed(now) else "open"
    print("market=%s date=%s" % (state, now.strftime("%Y%m%d")))
    out = os.environ.get("GITHUB_OUTPUT")
    if out:
        with open(out, "a", encoding="utf-8") as fh:
            fh.write("state=%s\n" % state)


if __name__ == "__main__":
    main()

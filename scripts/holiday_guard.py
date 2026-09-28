#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""GitHub Actions 用：判斷今日（台北）是否休市，寫入 $GITHUB_OUTPUT 的 state。

state=closed → 週末或 data/holidays.json 內的非週末休市日；否則 state=open。
資料缺失時 fail-open（視為 open），不因日曆檔問題擋掉正常交易日。
"""
import datetime
import json
import os
import sys

TPE = datetime.timezone(datetime.timedelta(hours=8))
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PATH = os.path.join(ROOT, "data", "holidays.json")

now = datetime.datetime.now(TPE)
ymd = now.strftime("%Y%m%d")
closed = now.weekday() >= 5
if not closed:
    try:
        with open(PATH, encoding="utf-8") as fh:
            closed = ymd in {str(x) for x in (json.load(fh).get("holidays") or [])}
    except Exception as exc:  # noqa: BLE001
        print("[warn] holidays.json 讀取失敗，視為開市: %s" % exc, file=sys.stderr)

state = "closed" if closed else "open"
print("market=%s date=%s" % (state, ymd))
out = os.environ.get("GITHUB_OUTPUT")
if out:
    with open(out, "a", encoding="utf-8") as fh:
        fh.write("state=%s\n" % state)

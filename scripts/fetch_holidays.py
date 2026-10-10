#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""產生 data/holidays.json —— 台股／台指期休市日 SSOT。

來源：臺灣證券交易所「市場開休市日期」（response=json）。
    https://www.twse.com.tw/holidaySchedule/holidaySchedule?response=json
期交所開休市與證交所同步，故以證交所為單一來源；此檔為系統唯一真相，
前端（index.html）、GitHub Actions、Cloudflare Worker 都讀同一份。

規則：
  1. 只留「非交易日」。公告中「開始交易日／最後交易日」（如封關、開紅盤）是交易日，剔除。
  2. 只留週一～週五（週末本來就休市，消費端另有 weekday 判斷）。
  3. 逐次**合併**既有 data/holidays.json，不覆蓋已抓到的年度 —— 新年度公告後自動納入，
     且舊年度不會因來源端換年度而消失。

用法：
  python scripts/fetch_holidays.py            # 抓取、合併、寫回 data/holidays.json
  python scripts/fetch_holidays.py --print    # 只印出不寫檔（除錯）
"""
import datetime
import json
import os
import sys
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
OUT = os.path.join(ROOT, "data", "holidays.json")

BASES = (
    "https://www.twse.com.tw/holidaySchedule/holidaySchedule",
    "https://www.twse.com.tw/rwd/zh/holidaySchedule/holidaySchedule",
)
UA = {"User-Agent": "Mozilla/5.0 (compatible; txf-quote holidays fetcher)"}
TRADING_MARKERS = ("開始交易", "最後交易")


def fetch_json(url, timeout=20):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8", "replace"))


def collect_rows():
    """嘗試多個端點與年度參數；回傳 (rows, years)。任一失敗都不致命。"""
    rows = {}
    years = set()
    this_year = datetime.datetime.now().year
    for base in BASES:
        for suffix in ("", "&queryYear=%d" % this_year, "&queryYear=%d" % (this_year + 1)):
            url = base + "?response=json" + suffix
            try:
                j = fetch_json(url)
            except Exception as exc:  # noqa: BLE001 - 任一來源失敗都要能繼續
                print("[warn] %s -> %s" % (url, exc), file=sys.stderr)
                continue
            year = j.get("queryYear")
            if isinstance(year, int):
                years.add(str(year))
            for row in (j.get("data") or []):
                if not row or len(row) < 2:
                    continue
                date = str(row[0]).strip()
                name = str(row[1]).strip()
                if len(date) == 10 and date[4] == "-":
                    rows[date] = name
    return rows, years


def normalize(rows):
    """→ sorted list of YYYYMMDD（非週末、非交易日）。"""
    out = set()
    for date, name in rows.items():
        if any(marker in name for marker in TRADING_MARKERS):
            continue
        y, mo, d = date.split("-")
        if datetime.date(int(y), int(mo), int(d)).weekday() >= 5:
            continue
        out.add(y + mo + d)
    return sorted(out)


def load_adhoc():
    """typhoon_check.py 寫入的臨時休市（颱風假）；證交所日曆沒有，年度覆蓋時必須保留。"""
    try:
        with open(OUT, encoding="utf-8") as fh:
            return dict(json.load(fh).get("adhoc") or {})
    except Exception:
        return {}


def load_existing():
    """回傳 {year: set(YYYYMMDD)}；舊格式（僅 holidays 扁平）也能吃。"""
    try:
        with open(OUT, encoding="utf-8") as fh:
            doc = json.load(fh)
        flat = {str(x) for x in (doc.get("holidays") or [])}
    except Exception:
        return {}
    by_year = {}
    for d in flat:
        if len(d) == 8:
            by_year.setdefault(d[:4], set()).add(d)
    return by_year


def main():
    rows, years = collect_rows()
    if not rows:
        print("[error] 未取得任何證交所日曆資料", file=sys.stderr)
        sys.exit(1)
    fresh = normalize(rows)
    by_year = load_existing()
    # 來源回報的 queryYear 對該年度是權威：整年覆蓋（改期不會殘留舊假），其他年度保留
    auth_years = sorted(y for y in years if any(d[:4] == y for d in fresh))
    for year in auth_years:
        year_list = [d for d in fresh if d[:4] == year]
        if year_list:
            by_year[year] = set(year_list)
    if not auth_years:
        # 來源沒帶 queryYear：保守起見以聯集方式併入
        for d in fresh:
            by_year.setdefault(d[:4], set()).add(d)
    adhoc = load_adhoc()
    merged = sorted({d for vals in by_year.values() for d in vals} | set(adhoc))
    merged_years = sorted(by_year)
    now = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=8)))
    doc = {
        "source": "TWSE holidaySchedule",
        "sourceUrl": BASES[0] + "?response=json",
        "generatedAt": now.strftime("%Y-%m-%dT%H:%M:%S+08:00"),
        "note": "非週末之市場休市日（YYYYMMDD）；開紅盤／封關等交易日與週末已排除。前端／Actions／Worker 皆讀此檔。",
        "years": merged_years,
        "holidays": merged,
    }
    if adhoc:
        doc["adhoc"] = dict(sorted(adhoc.items()))
    if "--print" in sys.argv:
        print(json.dumps(doc, ensure_ascii=False, indent=2))
        return
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as fh:
        json.dump(doc, fh, ensure_ascii=False, indent=2)
        fh.write("\n")
    print("[ok] holidays=%d years=%s" % (len(merged), years))


if __name__ == "__main__":
    main()

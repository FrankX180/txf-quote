# 微台(TMF)散戶多空比 → data/tmf_retail.json
# 來源：期交所三大法人 + 市場未沖銷；散戶淨=法人空−法人多；多空比=散戶淨/市場OI*100
# 公式對齊 BLOK：retail_net = market_oi 殘差；等價於 institution_short - institution_long
import csv
import io
import json
import os
import random
import ssl
import time
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "tmf_retail.json"
PROXY_FILE = Path(r"E:\_Project\股票資料庫\_Data\webshare_proxies.txt")
TZ = timezone(timedelta(hours=8))
HIST_N = 800
CHUNK_DAYS = 21
RECENT_DAYS = 21
MAX_WORKERS = 5

ssl_context = ssl.create_default_context()
ssl_context.check_hostname = False
ssl_context.verify_mode = ssl.CERT_NONE

HDR = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0.0.0",
    "Content-Type": "application/x-www-form-urlencoded",
    "Origin": "https://www.taifex.com.tw",
}


def nint(x):
    s = str(x).replace(",", "").strip()
    if not s or s == "-":
        return 0
    return int(float(s))


def load_proxies():
    """本機檔 or 雲端 env WEBSHARE_PROXIES / TAIFEX_PROXIES（一行一個 host:port:user:pass）。"""
    out = []
    env_raw = os.environ.get("WEBSHARE_PROXIES") or os.environ.get("TAIFEX_PROXIES") or ""
    lines = []
    if env_raw.strip():
        lines.extend(env_raw.replace("\r", "\n").split("\n"))
    if PROXY_FILE.exists():
        lines.extend(PROXY_FILE.read_text(encoding="utf-8", errors="replace").splitlines())
    seen = set()
    for ln in lines:
        ln = ln.strip()
        if not ln or ln.startswith("#"):
            continue
        parts = ln.split(":")
        if len(parts) >= 4:
            host, port, user, pwd = parts[0], parts[1], parts[2], ":".join(parts[3:])
            url = f"http://{user}:{pwd}@{host}:{port}"
        elif len(parts) == 2:
            url = f"http://{parts[0]}:{parts[1]}"
        else:
            continue
        if url in seen:
            continue
        seen.add(url)
        out.append(url)
    return out


def fetch_range_maps(start, end, proxies=None):
    """抓 [start,end]：先直連整段，失敗或缺日再切塊＋proxy。"""
    proxies = proxies if proxies is not None else load_proxies()
    im, om = {}, {}
    try:
        im, om = fetch_chunk(start, end, proxy=None)
        print("direct", ymd_slash(start), ymd_slash(end), "inst", len(im), "oi", len(om))
    except Exception as e:
        print("WARN direct", e)
    # Actions／缺日：切塊重抓並 merge
    need_chunk = (not im) or os.environ.get("GITHUB_ACTIONS") or (end - start).days > 10
    if need_chunk:
        for rg in chunk_ranges(start, end, 7):
            ok = False
            last_err = None
            # 先直連小塊
            for attempt in range(2):
                try:
                    a, b = fetch_chunk(rg[0], rg[1], proxy=None)
                    im.update(a)
                    om.update(b)
                    print("chunk direct", ymd_slash(rg[0]), ymd_slash(rg[1]), "inst", len(a), "oi", len(b))
                    ok = True
                    break
                except Exception as e:
                    last_err = e
                    time.sleep(0.8)
            if ok:
                continue
            for attempt in range(4):
                proxy = random.choice(proxies) if proxies else None
                try:
                    a, b = fetch_chunk(rg[0], rg[1], proxy=proxy)
                    im.update(a)
                    om.update(b)
                    print(
                        "chunk proxy",
                        ymd_slash(rg[0]),
                        ymd_slash(rg[1]),
                        "inst",
                        len(a),
                        "oi",
                        len(b),
                    )
                    ok = True
                    break
                except Exception as e:
                    last_err = e
                    time.sleep(1.2)
            if not ok:
                print("FAIL chunk", ymd_slash(rg[0]), ymd_slash(rg[1]), last_err)
    return im, om


def taifex_post(url, data, referer, proxy=None, timeout=90):
    body = urllib.parse.urlencode(data).encode("utf-8")
    req = urllib.request.Request(url, data=body, headers={**HDR, "Referer": referer})
    if proxy:
        opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({"http": proxy, "https": proxy}),
            urllib.request.HTTPSHandler(context=ssl_context),
        )
        with opener.open(req, timeout=timeout) as r:
            raw = r.read()
    else:
        with urllib.request.urlopen(req, timeout=timeout, context=ssl_context) as r:
            raw = r.read()
    for enc in ("cp950", "big5", "utf-8"):
        try:
            return raw.decode(enc)
        except Exception:
            continue
    return raw.decode("utf-8", "replace")


def ymd_slash(d: datetime) -> str:
    return d.strftime("%Y/%m/%d")


def parse_inst(text: str):
    """date8 -> {il, ish, inet, foreign, trust, dealer}"""
    if not text.startswith("日期"):
        return {}
    out = {}
    for r in csv.reader(io.StringIO(text.strip())):
        if len(r) < 14 or "微型臺指" not in r[1]:
            continue
        if r[2].strip() == "合計":
            continue
        d = r[0].replace("/", "")
        if len(d) != 8:
            continue
        who = r[2].strip()
        il = nint(r[9])
        ish = nint(r[11])
        inet = nint(r[13])
        rec = out.setdefault(d, {"il": 0, "ish": 0, "inet": 0, "foreign": 0, "trust": 0, "dealer": 0})
        rec["il"] += il
        rec["ish"] += ish
        rec["inet"] += inet
        if "外資" in who:
            rec["foreign"] = inet
        elif "投信" in who:
            rec["trust"] = inet
        elif "自營" in who:
            rec["dealer"] = inet
    return out


def parse_market_oi(text: str):
    """date8 -> market_oi (一般盤合計未沖銷)"""
    if not text.startswith("交易日期"):
        return {}
    out = {}
    for r in csv.reader(io.StringIO(text.strip())):
        if len(r) < 12 or r[1].strip() != "TMF":
            continue
        sess = r[17] if len(r) > 17 else ""
        if "盤後" in sess:
            continue
        d = r[0].replace("/", "")
        if len(d) != 8:
            continue
        out[d] = out.get(d, 0) + nint(r[11])
    return out


def fetch_chunk(start: datetime, end: datetime, proxy=None):
    s = ymd_slash(start)
    e = ymd_slash(end)
    inst_txt = taifex_post(
        "https://www.taifex.com.tw/cht/3/futContractsDateDown",
        {"queryStartDate": s, "queryEndDate": e},
        "https://www.taifex.com.tw/cht/3/futContractsDateView",
        proxy=proxy,
    )
    mkt_txt = taifex_post(
        "https://www.taifex.com.tw/cht/3/futDataDown",
        {
            "down_type": "1",
            "commodity_id": "TMF",
            "queryStartDate": s,
            "queryEndDate": e,
        },
        "https://www.taifex.com.tw/cht/3/futDailyMarketView",
        proxy=proxy,
    )
    return parse_inst(inst_txt), parse_market_oi(mkt_txt)


def chunk_ranges(start: datetime, end: datetime, span: int):
    cur = start
    out = []
    while cur <= end:
        nxt = min(cur + timedelta(days=span - 1), end)
        out.append((cur, nxt))
        cur = nxt + timedelta(days=1)
    return out


def build_series(inst_map, oi_map):
    series = []
    for d in sorted(inst_map.keys(), reverse=True):
        a = inst_map[d]
        oi = oi_map.get(d)
        # 無市場OI＝當日行情未齊（或盤中占位），不進副圖，避免假 0 柱／缺比
        if not oi or oi <= 0:
            continue
        retail = a["ish"] - a["il"]  # = -inet
        ratio = round(retail / oi * 100.0, 2)
        series.append(
            {
                "date": d,
                "retail": retail,
                "ratio": ratio,
                "marketOi": oi,
                "instNet": a["inet"],
                "foreign": a["foreign"],
                "trust": a["trust"],
                "dealer": a["dealer"],
            }
        )
    return series


def merge_series(old, new):
    by = {r["date"]: r for r in (old or []) if r.get("date")}
    for r in new or []:
        if r.get("date"):
            by[r["date"]] = r
    return [by[k] for k in sorted(by.keys(), reverse=True)]


def load_existing():
    if not OUT.exists():
        return []
    try:
        j = json.loads(OUT.read_text(encoding="utf-8"))
        return j.get("series") or []
    except Exception:
        return []


def main():
    from when import want_tmf_retail

    if not want_tmf_retail():
        print("SKIP tmf_retail")
        return

    existing = load_existing()
    today = datetime.now(TZ).date()
    end = datetime(today.year, today.month, today.day)
    proxies = load_proxies()
    print("proxies", len(proxies), "existing", len(existing), "GHA", bool(os.environ.get("GITHUB_ACTIONS")))

    # 近段若有日曆洞（例如漏抓兩個交易日），強制拉近 40 日自補
    have_dates = sorted({r["date"] for r in existing if r.get("date")})
    hole = False
    if len(have_dates) >= 2:
        for a, b in zip(have_dates[-40:], have_dates[-39:]):
            try:
                da = datetime.strptime(a, "%Y%m%d")
                db = datetime.strptime(b, "%Y%m%d")
            except Exception:
                continue
            gap = (db - da).days
            # 平日序列通常 1；跨周末約 3。大於 4 視為漏抓
            if gap > 4:
                hole = True
                print("GAP", a, b, "days", gap)
                break
    need_backfill = len(existing) < max(60, HIST_N // 4)
    if need_backfill:
        start = end - timedelta(days=int(HIST_N * 1.6))
        print("BACKFILL", ymd_slash(start), ymd_slash(end))
    elif hole:
        start = end - timedelta(days=40)
        print("HEAL", ymd_slash(start), ymd_slash(end))
    else:
        start = end - timedelta(days=RECENT_DAYS)
        print("REFRESH", ymd_slash(start), ymd_slash(end))

    im, om = fetch_range_maps(start, end, proxies=proxies)
    series = merge_series(existing, build_series(im, om))[:HIST_N]

    # 缺口觀察：近 20 個有 OI 的交易日是否都在 series
    have = {r["date"] for r in series}
    missing = sorted(d for d in om.keys() if d not in have)
    if missing:
        print("WARN missing_after_merge", missing[:12])

    payload = {
        "fetchedAt": datetime.now(TZ).strftime("%Y-%m-%d %H:%M:%S"),
        "date": series[0]["date"] if series else "",
        "source": "taifex:futContractsDateDown+futDataDown",
        "label": "微台散戶多空比",
        "note": "ratio%=(散戶多−散戶空)/市場OI*100；散戶淨口=法人空未平倉合計−法人多未平倉合計",
        "series": series,
    }
    OUT.parent.mkdir(exist_ok=True)
    OUT.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    latest = series[0] if series else {}
    print(
        "OK tmf_retail",
        len(series),
        "date",
        payload["date"],
        "retail",
        latest.get("retail"),
        "ratio",
        latest.get("ratio"),
        "oi",
        latest.get("marketOi"),
    )


if __name__ == "__main__":
    main()

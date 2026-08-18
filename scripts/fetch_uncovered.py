# 法人／大額未平倉 → data/uncovered.json
# 來源：Yahoo（當日多空）+ 聚財（近月淨額序列）+ CMoney DtNo（散戶／PC／Δ）
import json
import re
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "uncovered.json"
TZ = timezone(timedelta(hours=8))
HDR = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0.0.0",
    "Accept": "text/html,application/json,*/*",
}
YAHOO = "https://tw.stock.yahoo.com/future/futures_uncovered.html"
WEARN = "https://stock.wearn.com/taifexphoto.asp"
CMONEY = "https://www.cmoney.tw/MobileService/ashx/GetDtnoData.ashx"
HIST_N = 20


def get_text(url: str, timeout: int = 30) -> str:
    req = urllib.request.Request(url, headers=HDR)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", "replace")


def get_json(url: str, timeout: int = 30):
    req = urllib.request.Request(url, headers={**HDR, "Accept": "application/json,*/*"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


def extract(html: str, marker: str):
    i = html.find(marker)
    if i < 0:
        return []
    i = html.find('"list":', i)
    i = html.find("[", i)
    depth = 0
    for j in range(i, len(html)):
        if html[j] == "[":
            depth += 1
        elif html[j] == "]":
            depth -= 1
            if depth == 0:
                return json.loads(html[i : j + 1])
    return []


def num(v):
    if isinstance(v, dict):
        v = v.get("sort", v.get("raw"))
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def roc_to_ymd(s: str) -> str:
    # 115/08/17 -> 20260817
    m = re.match(r"^(\d{2,3})/(\d{2})/(\d{2})$", s.strip())
    if not m:
        return ""
    y = int(m.group(1)) + 1911
    return f"{y:04d}{m.group(2)}{m.group(3)}"


def ymd_slash(ymd: str) -> str:
    if not ymd or len(ymd) < 8:
        return ymd or ""
    return f"{ymd[:4]}-{ymd[4:6]}-{ymd[6:8]}"



def fetch_yahoo():
    html = get_text(YAHOO, 25)
    inst_raw = [
        x for x in extract(html, "FutureUncoveredTable_insInvestorsOpen") if x.get("marketCode") == "FITX"
    ]
    top_raw = [
        x
        for x in extract(html, "FutureUncoveredTable_top10PositionTrader")
        if x.get("marketCode") == "FITX"
    ]
    inst = [
        {
            "name": x.get("insInventorName") or "",
            "long": num(x.get("longPosition")),
            "short": num(x.get("shortPosition")),
            "net": num(x.get("net")),
        }
        for x in inst_raw
    ]
    top = [
        {
            "type": x.get("type") or "",
            "long": num(x.get("longPosition")),
            "longPct": num(x.get("longRate")),
            "short": num(x.get("shortPosition")),
            "shortPct": num(x.get("shortRate")),
            "open": num(x.get("openPosition")),
        }
        for x in top_raw
    ]
    date = ""
    if inst_raw:
        date = str(inst_raw[0].get("date") or "")[:10].replace("-", "")
    return inst, top, date


def fetch_wearn_hist(n: int = HIST_N):
    html = get_text(WEARN, 30)
    text = re.sub(r"<script[\s\S]*?</script>", "", html, flags=re.I)
    text = re.sub(r"<style[\s\S]*?</style>", "", text, flags=re.I)
    text = re.sub(r"<[^>]+>", "\n", text)
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    # find header then rows: date + 8 numbers
    try:
        i = lines.index("日期")
    except ValueError:
        return []
    # advance to first ROC date
    while i < len(lines) and not re.match(r"^\d{2,3}/\d{2}/\d{2}$", lines[i]):
        i += 1
    out = []
    while i + 8 < len(lines) and len(out) < n:
        if not re.match(r"^\d{2,3}/\d{2}/\d{2}$", lines[i]):
            break
        try:
            row = {
                "date": roc_to_ymd(lines[i]),
                "top5": float(lines[i + 1]),
                "top10": float(lines[i + 2]),
                "top5Spec": float(lines[i + 3]),
                "top10Spec": float(lines[i + 4]),
                "foreign": float(lines[i + 5]),
                "trust": float(lines[i + 6]),
                "dealer": float(lines[i + 7]),
                "close": float(lines[i + 8]),
            }
        except ValueError:
            break
        out.append(row)
        i += 9
    return out


def fetch_cmoney_map(dtno: str):
    url = f"{CMONEY}?Action=GetDtnoData&DtNo={dtno}&FilterNo=0&ParamStr="
    j = get_json(url, 30)
    title = j.get("Title") or []
    data = j.get("Data") or []
    idx = {t: i for i, t in enumerate(title)}
    return idx, data


def cmoney_series():
    """date -> {retail, foreignDelta, retailDelta, foreign, pc}"""
    out = {}
    idx, data = fetch_cmoney_map("883765")
    for row in data[: max(HIST_N * 2, 40)]:
        d = str(row[idx["日期"]])
        out[d] = {
            "foreign": float(row[idx["外資淨未平倉口數"]]),
            "retail": float(row[idx["散戶淨未平倉口數"]]),
            "foreignDelta": float(row[idx["外資期貨未平倉增減"]]),
            "retailDelta": float(row[idx["散戶期貨未平倉增減"]]),
        }
    idx2, data2 = fetch_cmoney_map("345198")
    # oldest-first
    pc_map = {str(r[0]): float(r[1]) for r in data2}
    for d, rec in out.items():
        if d in pc_map:
            rec["pc"] = pc_map[d]
    # also attach pc for wearn-only dates later
    return out, pc_map



def fetch_cmoney_today_extra():
    url = f"{CMONEY}?Action=GetDtnoData&DtNo=43456965&FilterNo=0&ParamStr="
    j = get_json(url, 20)
    data = j.get("Data") or []
    if not data:
        return {}
    row = data[0]
    # Title order fixed in catalog
    return {
        "date": str(row[0]),
        "txNet": float(row[1]),
        "foreign": float(row[2]),
        "trust": float(row[3]),
        "dealer": float(row[4]),
        "top10": float(row[5]),
        "top10Spec": float(row[6]),
        "retail": float(row[7]),
        "pc": float(row[11]),
        "vix": float(row[12]),
    }


def main():
    from when import want_uncovered

    if not want_uncovered():
        print("SKIP uncovered")
        return

    sources = []
    inst, top, yahoo_date = fetch_yahoo()
    sources.append("yahoo")
    wearn = fetch_wearn_hist(HIST_N)
    if wearn:
        sources.append("wearn")
    cm_map, pc_map = cmoney_series()
    sources.append("cmoney")
    extra = {}
    try:
        extra = fetch_cmoney_today_extra()
    except Exception as e:
        print("WARN cmoney today", e)

    # merge history: wearn base + cmoney retail/delta/pc
    history = []
    for w in wearn:
        d = w["date"]
        c = cm_map.get(d) or {}
        rec = {
            "date": d,
            "foreign": w["foreign"],
            "trust": w["trust"],
            "dealer": w["dealer"],
            "retail": c.get("retail"),
            "top5": w["top5"],
            "top10": w["top10"],
            "top5Spec": w["top5Spec"],
            "top10Spec": w["top10Spec"],
            "close": w["close"],
            "foreignDelta": c.get("foreignDelta"),
            "retailDelta": c.get("retailDelta"),
            "pc": c.get("pc", pc_map.get(d)),
        }
        history.append(rec)

    # if wearn empty, fall back cmoney foreign/retail only
    if not history:
        for d, c in list(cm_map.items())[:HIST_N]:
            history.append(
                {
                    "date": d,
                    "foreign": c.get("foreign"),
                    "trust": None,
                    "dealer": None,
                    "retail": c.get("retail"),
                    "top5": None,
                    "top10": None,
                    "top5Spec": None,
                    "top10Spec": None,
                    "close": None,
                    "foreignDelta": c.get("foreignDelta"),
                    "retailDelta": c.get("retailDelta"),
                    "pc": c.get("pc"),
                }
            )

    date = yahoo_date or (history[0]["date"] if history else "") or extra.get("date") or ""
    # Yahoo 偶發只有人名沒多空：用 history 淨額補今日部位
    if history and (not inst or all(r.get("net") is None for r in inst)):
        h0 = history[0]
        inst = [
            {"name": "外資及陸資", "long": None, "short": None, "net": h0.get("foreign")},
            {"name": "投信", "long": None, "short": None, "net": h0.get("trust")},
            {"name": "自營商", "long": None, "short": None, "net": h0.get("dealer")},
            {
                "name": "三大法人合計",
                "long": None,
                "short": None,
                "net": (
                    None
                    if h0.get("foreign") is None and h0.get("trust") is None and h0.get("dealer") is None
                    else float(h0.get("foreign") or 0)
                    + float(h0.get("trust") or 0)
                    + float(h0.get("dealer") or 0)
                ),
            },
        ]
    # today card fields
    top10_net = None
    top10_spec = None
    if top:
        # Yahoo: long-short as net for first two types
        for trow in top:
            net = (trow.get("long") or 0) - (trow.get("short") or 0)
            typ = trow.get("type") or ""
            if "特定" in typ:
                top10_spec = net
            elif top10_net is None:
                top10_net = net
    if history:
        top10_net = history[0].get("top10") if history[0].get("top10") is not None else top10_net
        top10_spec = history[0].get("top10Spec") if history[0].get("top10Spec") is not None else top10_spec

    today = {
        "date": date,
        "inst": inst,
        "top": top,
        "top10Net": extra.get("top10", top10_net),
        "top10SpecNet": extra.get("top10Spec", top10_spec),
        "retailNet": extra.get("retail", history[0].get("retail") if history else None),
        "foreignDelta": history[0].get("foreignDelta") if history else None,
        "retailDelta": history[0].get("retailDelta") if history else None,
        "pc": extra.get("pc", history[0].get("pc") if history else None),
        "vix": extra.get("vix"),
    }

    payload = {
        "fetchedAt": datetime.now(TZ).strftime("%Y-%m-%d %H:%M:%S"),
        "date": ymd_slash(date) if len(date) == 8 else date,
        "source": "+".join(sources),
        "inst": inst,
        "top": top,
        "today": today,
        "history": history[:HIST_N],
    }
    OUT.parent.mkdir(exist_ok=True)
    OUT.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    print(
        "OK inst",
        len(inst),
        "top",
        len(top),
        "hist",
        len(payload["history"]),
        "date",
        payload["date"],
        "src",
        payload["source"],
    )


if __name__ == "__main__":
    main()

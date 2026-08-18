# 法人／大額未平倉 → data/uncovered.json
# 今日部位主路：期交所 OpenAPI；備援 Yahoo；近20日：聚財／wearn／CMoney
# 口數一律大台等值：TX + MTX/4 + TMF/20（點值 200/50/10）
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
OA_INST = (
    "https://openapi.taifex.com.tw/v1/"
    "MarketDataOfMajorInstitutionalTradersDetailsOfFuturesContractsBytheDate"
)
OA_LARGE = "https://openapi.taifex.com.tw/v1/OpenInterestOfLargeTradersFutures"
WEARN = "https://stock.wearn.com/taifexphoto.asp"
CMONEY = "https://www.cmoney.tw/MobileService/ashx/GetDtnoData.ashx"
HIST_N = 20
MTX_DIV = 4.0
TMF_DIV = 20.0
TMF_JSON = ROOT / "data" / "tmf_retail.json"
OA_CODES = (
    ("臺股期貨", 1.0),
    ("小型臺指期貨", MTX_DIV),
    ("微型臺指期貨", TMF_DIV),
)


def get_text(url: str, timeout: int = 30) -> str:
    req = urllib.request.Request(url, headers=HDR)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", "replace")


def get_json(url: str, timeout: int = 30):
    req = urllib.request.Request(url, headers={**HDR, "Accept": "application/json,*/*"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8-sig", "replace"))

def tx_eq(tx, mtx=None, tmf=None):
    if tx is None and mtx is None and tmf is None:
        return None
    return float(tx or 0) + float(mtx or 0) / MTX_DIV + float(tmf or 0) / TMF_DIV


def _sum_div(parts):
    """parts: [(value, div), ...]；全 None 則 None。"""
    if not parts or all(v is None for v, _ in parts):
        return None
    return sum(float(v or 0) / float(div) for v, div in parts)

def mtx_retail_approx(foreign_mtx, trust_mtx, dealer_mtx):
    """小台散戶淨 ≈ −(外資+投信+自營)小台淨（與微台散戶定義同）。"""
    if foreign_mtx is None and trust_mtx is None and dealer_mtx is None:
        return None
    return -(
        float(foreign_mtx or 0) + float(trust_mtx or 0) + float(dealer_mtx or 0)
    )


def apply_txeq_add_to_ls(row, add):
    """把小台/微台淨額增量併入多空，使 long-short == 大台等值淨。"""
    if row is None or add is None:
        return
    add = float(add)
    if abs(add) < 1e-12:
        return
    lg, sh = row.get("long"), row.get("short")
    if lg is None or sh is None:
        if row.get("net") is not None:
            row["net"] = float(row["net"]) + add
        return
    lg, sh = float(lg), float(sh)
    if add >= 0:
        lg += add
    else:
        sh += -add
    row["long"] = lg
    row["short"] = sh
    row["net"] = lg - sh


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

def _ymd8(s) -> str:
    s = str(s or "").strip()
    if len(s) >= 10 and s[4] == "-" and s[7] == "-":
        return s[:10].replace("-", "")
    if len(s) >= 8 and s[:8].isdigit():
        return s[:8]
    return ""


def inst_has_ls(inst) -> bool:
    rows = [r for r in (inst or []) if r.get("name") and "合計" not in (r.get("name") or "")]
    if len(rows) < 3:
        return False
    return all(r.get("long") is not None and r.get("short") is not None for r in rows[:3])


def load_tmf_by_date():
    if not TMF_JSON.exists():
        return {}
    try:
        j = json.loads(TMF_JSON.read_text(encoding="utf-8"))
    except Exception:
        return {}
    out = {}
    for r in j.get("series") or []:
        d = str(r.get("date") or "")
        if d:
            out[d] = r
    return out


def fetch_openapi_tx():
    """最新一交易日：大台等值法人多空（TX+MTX/4+TMF/20）＋近月大額前十／特定。"""
    raw = get_json(OA_INST, 40)
    if isinstance(raw, dict):
        raw = raw.get("data") or raw.get("Data") or []
    by_code = {}
    date = ""
    for code, _div in OA_CODES:
        rows = [r for r in (raw or []) if str(r.get("ContractCode") or "") == code]
        if rows and not date:
            date = _ymd8(rows[0].get("Date"))
        slot = {}
        for r in rows:
            name = str(r.get("Item") or "")
            slot[name] = {
                "long": num(r.get("OpenInterest(Long)")),
                "short": num(r.get("OpenInterest(Short)")),
                "net": num(r.get("OpenInterest(Net)")),
            }
        by_code[code] = slot
    order = ["外資及陸資", "投信", "自營商"]
    inst = []
    for name in order:
        parts_l, parts_s, parts_n = [], [], []
        present = False
        for code, div in OA_CODES:
            cell = (by_code.get(code) or {}).get(name) or {}
            if cell:
                present = True
            parts_l.append((cell.get("long"), div))
            parts_s.append((cell.get("short"), div))
            parts_n.append((cell.get("net"), div))
        if not present:
            continue
        inst.append(
            {
                "name": name,
                "long": _sum_div(parts_l),
                "short": _sum_div(parts_s),
                "net": _sum_div(parts_n),
            }
        )
    if inst:
        ls = [r for r in inst if r.get("long") is not None]
        ss = [r for r in inst if r.get("short") is not None]
        ns = [r for r in inst if r.get("net") is not None]
        inst.append(
            {
                "name": "三大法人合計",
                "long": sum(r["long"] for r in ls) if ls else None,
                "short": sum(r["short"] for r in ss) if ss else None,
                "net": sum(r["net"] for r in ns) if ns else None,
            }
        )
    large = get_json(OA_LARGE, 40)
    if isinstance(large, dict):
        large = large.get("data") or large.get("Data") or []
    txl = [r for r in (large or []) if str(r.get("Contract") or "") == "TX"]
    if not date and txl:
        date = _ymd8(txl[0].get("Date"))
    months = []
    for r in txl:
        m = str(r.get("SettlementMonth") or "")
        if m.isdigit() and m not in ("666666", "999912"):
            months.append(m)
    near = max(months) if months else ""
    top = []
    type_map = {"0": "前十大交易人合計", "1": "特定法人合計"}
    for code, label in type_map.items():
        row = next(
            (
                r
                for r in txl
                if str(r.get("SettlementMonth") or "") == near
                and str(r.get("TypeOfTraders") or "") == code
            ),
            None,
        )
        if not row:
            continue
        lg = num(row.get("Top10Buy"))
        sh = num(row.get("Top10Sell"))
        oi = num(row.get("OIOfMarket"))
        top.append(
            {
                "type": label,
                "long": lg,
                "longPct": (lg / oi * 100.0) if lg is not None and oi else None,
                "short": sh,
                "shortPct": (sh / oi * 100.0) if sh is not None and oi else None,
                "open": oi,
            }
        )
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
    """date -> {retail, foreignDelta, retailDelta, foreign, mtx*, pc}"""
    out = {}
    idx, data = fetch_cmoney_map("883765")
    for row in data[: max(HIST_N * 2, 40)]:
        d = str(row[idx["日期"]])
        def fget(key):
            if key not in idx:
                return None
            try:
                return float(row[idx[key]])
            except (TypeError, ValueError):
                return None

        out[d] = {
            "foreign": fget("外資淨未平倉口數"),
            "retail": fget("散戶淨未平倉口數"),
            "foreignDelta": fget("外資期貨未平倉增減"),
            "retailDelta": fget("散戶期貨未平倉增減"),
            "foreignMtx": fget("外資小台未平倉"),
            "trustMtx": fget("投信小台未平倉"),
            "dealerMtx": fget("自營商小台未平倉"),
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
    oa_inst, oa_top, oa_date = [], [], ""
    try:
        oa_inst, oa_top, oa_date = fetch_openapi_tx()
    except Exception as e:
        print("WARN openapi", e)
    yh_inst, yh_top, yh_date = [], [], ""
    try:
        yh_inst, yh_top, yh_date = fetch_yahoo()
    except Exception as e:
        print("WARN yahoo", e)

    oa_ok = inst_has_ls(oa_inst)
    yh_ok = inst_has_ls(yh_inst)
    # 官方日 >= 奇摩日才用 OpenAPI；官方較舊不蓋較新奇摩（奇摩空值交後面 history 補淨額）
    use_oa = bool(oa_ok and oa_date and (not yh_date or oa_date >= yh_date))
    if not use_oa and oa_ok and not yh_ok and (not yh_date or not yh_inst):
        use_oa = True
    if use_oa:
        inst, top, date_pick = oa_inst, oa_top, oa_date
        sources.append("openapi")
    else:
        inst, top, date_pick = yh_inst, yh_top, yh_date
        if yh_inst or yh_top:
            sources.append("yahoo")
        elif oa_ok:
            inst, top, date_pick = oa_inst, oa_top, oa_date
            sources.append("openapi")
    yahoo_date = date_pick
    wearn = fetch_wearn_hist(HIST_N)
    if wearn:
        sources.append("wearn")
    cm_map, pc_map = cmoney_series()
    tmf_map = load_tmf_by_date()
    if tmf_map:
        sources.append("tmf")
    sources.append("cmoney")
    extra = {}
    try:
        extra = fetch_cmoney_today_extra()
    except Exception as e:
        print("WARN cmoney today", e)

    # merge history：大台等值 = wearn大台 + CMoney小台/4 + tmf_retail微台/20
    history = []
    for w in wearn:
        d = w["date"]
        c = cm_map.get(d) or {}
        t = tmf_map.get(d) or {}
        rec = {
            "date": d,
            "foreign": tx_eq(w["foreign"], c.get("foreignMtx"), t.get("foreign")),
            "trust": tx_eq(w["trust"], c.get("trustMtx"), t.get("trust")),
            "dealer": tx_eq(w["dealer"], c.get("dealerMtx"), t.get("dealer")),
            "retail": tx_eq(
                c.get("retail"),
                mtx_retail_approx(c.get("foreignMtx"), c.get("trustMtx"), c.get("dealerMtx")),
                t.get("retail"),
            ),
            "top5": w["top5"],
            "top10": w["top10"],
            "top5Spec": w["top5Spec"],
            "top10Spec": w["top10Spec"],
            "close": w["close"],
            "pc": c.get("pc", pc_map.get(d)),
        }
        history.append(rec)

    # if wearn empty, fall back cmoney foreign/retail only（仍做小台／微台換算）
    if not history:
        for d, c in list(cm_map.items())[:HIST_N]:
            t = tmf_map.get(d) or {}
            history.append(
                {
                    "date": d,
                    "foreign": tx_eq(c.get("foreign"), c.get("foreignMtx"), t.get("foreign")),
                    "trust": tx_eq(None, c.get("trustMtx"), t.get("trust")),
                    "dealer": tx_eq(None, c.get("dealerMtx"), t.get("dealer")),
                    "retail": tx_eq(
                        c.get("retail"),
                        mtx_retail_approx(c.get("foreignMtx"), c.get("trustMtx"), c.get("dealerMtx")),
                        t.get("retail"),
                    ),
                    "top5": None,
                    "top10": None,
                    "top5Spec": None,
                    "top10Spec": None,
                    "close": None,
                    "pc": c.get("pc"),
                }
            )

    # Δ 用換算後水位重算（口）
    for i, rec in enumerate(history):
        nxt = history[i + 1] if i + 1 < len(history) else None
        if not nxt:
            rec["foreignDelta"] = None
            rec["retailDelta"] = None
            continue
        rec["foreignDelta"] = (
            None
            if rec.get("foreign") is None or nxt.get("foreign") is None
            else float(rec["foreign"]) - float(nxt["foreign"])
        )
        rec["retailDelta"] = (
            None
            if rec.get("retail") is None or nxt.get("retail") is None
            else float(rec["retail"]) - float(nxt["retail"])
        )

    # 奇摩今日多空是大台原值：把小台/微台淨額增量併入多空，使 L-S=等值淨
    if (not use_oa) and inst and date_pick:
        c = cm_map.get(date_pick) or {}
        t = tmf_map.get(date_pick) or {}
        add = {
            "外資及陸資": tx_eq(0, c.get("foreignMtx"), t.get("foreign")),
            "投信": tx_eq(0, c.get("trustMtx"), t.get("trust")),
            "自營商": tx_eq(0, c.get("dealerMtx"), t.get("dealer")),
        }
        for r in inst:
            nm = r.get("name") or ""
            if nm in add:
                apply_txeq_add_to_ls(r, add[nm])
        tot = next((r for r in inst if "合計" in (r.get("name") or "")), None)
        if tot is not None:
            parts = [r for r in inst if r is not tot]
            ls = [r for r in parts if r.get("long") is not None]
            ss = [r for r in parts if r.get("short") is not None]
            ns = [r for r in parts if r.get("net") is not None]
            tot["long"] = sum(float(r["long"]) for r in ls) if ls else None
            tot["short"] = sum(float(r["short"]) for r in ss) if ss else None
            tot["net"] = sum(float(r["net"]) for r in ns) if ns else None

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
        "retailNet": (history[0].get("retail") if history and history[0].get("retail") is not None else extra.get("retail")),
        "foreignDelta": history[0].get("foreignDelta") if history else None,
        "retailDelta": history[0].get("retailDelta") if history else None,
        "pc": extra.get("pc", history[0].get("pc") if history else None),
        "vix": extra.get("vix"),
    }

    payload = {
        "fetchedAt": datetime.now(TZ).strftime("%Y-%m-%d %H:%M:%S"),
        "date": ymd_slash(date) if len(date) == 8 else date,
        "source": "+".join(sources),
        "unit": "tx_eq",
        "unitNote": "大台等值口=大台+小台/4+微台/20；大額前十/特定=官方TX+小台/4（無微台大額）",
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

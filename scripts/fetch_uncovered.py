# 法人／大額未平倉 → data/uncovered.json
# 主路：期交所官網下載 futContractsDateDown（TX+MTX+TMF 大台等值）
# 備援：OpenAPI／Yahoo／玩股大額；PC／VIX 仍可吃 CMoney
# 散戶一律：retail = -(外資+投信+自營)（大台等值）
# 口數一律大台等值：TX + MTX/4 + TMF/20（點值 200/50/10）
import csv
import io
import json
import re
import ssl
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from html.parser import HTMLParser
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "uncovered.json"
TZ = timezone(timedelta(hours=8))
SSL_CTX = ssl.create_default_context()
SSL_CTX.check_hostname = False
SSL_CTX.verify_mode = ssl.CERT_NONE
AH_URL = "https://www.taifex.com.tw/cht/3/futContractsDateAh?queryDate={}"
AH_PRODUCT = {"臺股期貨": "TX", "小型臺指期貨": "MTX", "微型臺指期貨": "TMF"}
AH_INST = {"自營商": "dealer", "投信": "trust", "外資及陸資": "foreign", "外資": "foreign"}
SNAP = ROOT / "data" / "snapshot.json"
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
VIX_CSV = Path(r"E:\CyndiTD\Program\derived\VIX\VIXTWN_daily_master.csv")
OA_CODES = (
    ("臺股期貨", 1.0),
    ("小型臺指期貨", MTX_DIV),
    ("微型臺指期貨", TMF_DIV),
)
TAIFEX_INST_DOWN = "https://www.taifex.com.tw/cht/3/futContractsDateDown"
TAIFEX_INST_VIEW = "https://www.taifex.com.tw/cht/3/futContractsDateView"
TAIFEX_PRODS = {
    "臺股期貨": 1.0,
    "小型臺指期貨": MTX_DIV,
    "微型臺指期貨": TMF_DIV,
}
TAIFEX_WHO = {
    "外資及陸資": "foreign",
    "外資": "foreign",
    "投信": "trust",
    "自營商": "dealer",
}


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


def retail_from_inst(foreign, trust, dealer):
    """散戶 = −(外資+投信+自營)；三者皆缺則 None。"""
    if foreign is None and trust is None and dealer is None:
        return None
    return round(-(float(foreign or 0) + float(trust or 0) + float(dealer or 0)), 2)


def taifex_post_text(url: str, data: dict, referer: str, timeout: int = 90) -> str:
    body = urllib.parse.urlencode(data).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=body,
        headers={
            **HDR,
            "Content-Type": "application/x-www-form-urlencoded",
            "Origin": "https://www.taifex.com.tw",
            "Referer": referer,
        },
    )
    with urllib.request.urlopen(req, timeout=timeout, context=SSL_CTX) as r:
        raw = r.read()
    for enc in ("cp950", "big5", "utf-8"):
        try:
            return raw.decode(enc)
        except Exception:
            continue
    return raw.decode("utf-8", "replace")


def parse_taifex_inst_csv(text: str):
    """官網下載 CSV → date8 -> {foreign,trust,dealer,retail} 大台等值淨未平倉。"""
    if not text or not text.lstrip().startswith("日期"):
        return {}
    bags = {}
    for row in csv.reader(io.StringIO(text.strip())):
        if len(row) < 14:
            continue
        if row[0].strip() == "日期":
            continue
        prod = (row[1] or "").strip()
        who = (row[2] or "").strip()
        if prod not in TAIFEX_PRODS or who == "合計":
            continue
        key = TAIFEX_WHO.get(who)
        if not key:
            continue
        d = str(row[0]).replace("/", "").replace("-", "")
        if len(d) != 8 or not d.isdigit():
            continue
        try:
            net = float(str(row[13]).replace(",", "") or 0)
        except ValueError:
            continue
        div = float(TAIFEX_PRODS[prod])
        slot = bags.setdefault(
            d, {"foreign": 0.0, "trust": 0.0, "dealer": 0.0, "_hit": False}
        )
        slot[key] = float(slot.get(key) or 0) + net / div
        slot["_hit"] = True
    out = {}
    for d, slot in bags.items():
        if not slot.get("_hit"):
            continue
        f = round(float(slot["foreign"]), 2)
        t = round(float(slot["trust"]), 2)
        de = round(float(slot["dealer"]), 2)
        out[d] = {
            "foreign": f,
            "trust": t,
            "dealer": de,
            "retail": retail_from_inst(f, t, de),
        }
    return out


def fetch_taifex_inst_hist(n: int = HIST_N):
    """抓近 n 個日曆窗（約 2n 天）期交所三大法人未平倉，含今日若已上架。"""
    end = datetime.now(TZ).date()
    start = end - timedelta(days=max(n * 2 + 7, 21))
    text = taifex_post_text(
        TAIFEX_INST_DOWN,
        {
            "queryStartDate": start.strftime("%Y/%m/%d"),
            "queryEndDate": end.strftime("%Y/%m/%d"),
        },
        TAIFEX_INST_VIEW,
    )
    m = parse_taifex_inst_csv(text)
    dates = sorted(m.keys(), reverse=True)[:n]
    return [(d, m[d]) for d in dates]


def ymd8(s):
    return re.sub(r"\D", "", str(s or ""))[:8]


def load_vix_map():
    """本機 VIXTWN：既有 uncovered → PG index_daily_prices → CyndiTD CSV（後蓋前）。"""
    m = {}
    if OUT.exists():
        try:
            old = json.loads(OUT.read_text(encoding="utf-8"))
            for r in old.get("history") or []:
                d = ymd8(r.get("date"))
                if d and r.get("vix") is not None:
                    m[d] = float(r["vix"])
            td = ymd8((old.get("today") or {}).get("date") or old.get("date"))
            tv = (old.get("today") or {}).get("vix")
            if td and tv is not None:
                m[td] = float(tv)
        except Exception as e:
            print("WARN vix old", e)
    try:
        import os

        import psycopg2

        pw = os.environ.get("PG_PASSWORD") or os.environ.get("PGPASSWORD")
        if pw:
            conn = psycopg2.connect(
                host=os.environ.get("PGHOST", "127.0.0.1"),
                port=int(os.environ.get("PGPORT", "5432")),
                dbname=os.environ.get("PGDATABASE", "stock_research"),
                user=os.environ.get("PGUSER", "postgres"),
                password=pw,
            )
            cur = conn.cursor()
            cur.execute(
                """
                SELECT trade_date::text, close_price
                FROM index_daily_prices
                WHERE index_id = 'VIXTWN' AND close_price IS NOT NULL
                """
            )
            for d, v in cur.fetchall():
                k = ymd8(d)
                if k:
                    m[k] = float(v)
            conn.close()
    except Exception as e:
        print("WARN vix pg", e)
    if VIX_CSV.exists():
        try:
            import csv

            with VIX_CSV.open(encoding="utf-8", newline="") as f:
                for row in csv.DictReader(f):
                    k = ymd8(row.get("date"))
                    c = row.get("close")
                    if not k or len(k) != 8 or c in (None, ""):
                        continue
                    try:
                        m[k] = float(c)
                    except (TypeError, ValueError):
                        continue
        except Exception as e:
            print("WARN vix csv", e)
    return m


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


class _AhTableParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.tables = []
        self.current_table = None
        self.current_row = None
        self.current_cell = ""
        self.in_cell = False

    def handle_starttag(self, tag, attrs):
        if tag == "table":
            self.current_table = []
        elif tag in ("td", "th") and self.current_table is not None:
            self.in_cell = True
            self.current_cell = ""
        elif tag == "tr" and self.current_table is not None:
            self.current_row = []

    def handle_endtag(self, tag):
        if tag in ("td", "th") and self.in_cell:
            self.in_cell = False
            if self.current_row is not None:
                self.current_row.append(self.current_cell.strip())
        elif tag == "tr" and self.current_row is not None and self.current_table is not None:
            if self.current_row:
                self.current_table.append(self.current_row)
            self.current_row = None
        elif tag == "table" and self.current_table is not None:
            if self.current_table:
                self.tables.append(self.current_table)
            self.current_table = None

    def handle_data(self, data):
        if self.in_cell:
            self.current_cell += data


def _ah_int(s):
    return int(str(s).replace(",", "")) if str(s or "").strip() else 0


def scrape_night_ah(date_slash: str):
    """期交所夜盤三大法人成交。回傳 {foreign,trust,dealer,retail} 大台等值淨口，或 None。"""
    url = AH_URL.format(date_slash)
    req = urllib.request.Request(url, headers=HDR)
    with urllib.request.urlopen(req, timeout=30, context=SSL_CTX) as r:
        html = r.read().decode("utf-8", "replace")
    if "查無資料" in html:
        return None
    p = _AhTableParser()
    p.feed(html)
    big = [t for t in p.tables if len(t) > 10]
    if not big:
        return None
    bags = {
        "foreign": {"l": 0.0, "s": 0.0},
        "trust": {"l": 0.0, "s": 0.0},
        "dealer": {"l": 0.0, "s": 0.0},
    }
    hit = False
    cur = None
    for row in big[0]:
        if not row:
            continue
        if row[0].isdigit() and len(row) >= 9:
            cur = AH_PRODUCT.get(row[1])
            key = AH_INST.get(row[2])
            if cur and key:
                div = 1.0 if cur == "TX" else (MTX_DIV if cur == "MTX" else TMF_DIV)
                bags[key]["l"] += _ah_int(row[3]) / div
                bags[key]["s"] += _ah_int(row[5]) / div
                hit = True
        elif cur and len(row) >= 7 and row[0] in AH_INST:
            key = AH_INST[row[0]]
            div = 1.0 if cur == "TX" else (MTX_DIV if cur == "MTX" else TMF_DIV)
            bags[key]["l"] += _ah_int(row[1]) / div
            bags[key]["s"] += _ah_int(row[3]) / div
            hit = True
    if not hit:
        return None
    foreign = bags["foreign"]["l"] - bags["foreign"]["s"]
    trust = bags["trust"]["l"] - bags["trust"]["s"]
    dealer = bags["dealer"]["l"] - bags["dealer"]["s"]
    return {
        "foreign": round(foreign, 2),
        "trust": round(trust, 2),
        "dealer": round(dealer, 2),
        "retail": round(-(foreign + trust + dealer), 2),
    }


def night_close_from_snap():
    if not SNAP.exists():
        return None
    try:
        snap = json.loads(SNAP.read_text(encoding="utf-8"))
        n = snap.get("night") or {}
        v = n.get("CLastPrice")
        return float(v) if v not in (None, "") else None
    except Exception:
        return None


def fetch_latest_night(day_hist_date: str, base_row: dict | None = None):
    """抓比日盤 history 更新的最新夜盤列；水位＝日盤 OI＋夜盤成交淨口。沒有則 None。"""
    day8 = ymd8(day_hist_date)
    now = datetime.now(TZ)
    old = {}
    if OUT.exists():
        try:
            old = json.loads(OUT.read_text(encoding="utf-8")).get("night") or {}
        except Exception:
            old = {}
    close = night_close_from_snap()
    for delta in range(0, 4):
        d = now - timedelta(days=delta)
        if d.weekday() == 6:
            continue
        d8 = d.strftime("%Y%m%d")
        if day8 and d8 <= day8:
            continue
        slash = d.strftime("%Y/%m/%d")
        try:
            nets = scrape_night_ah(slash)
        except Exception as e:
            print("WARN night ah", slash, e)
            nets = None
        if not nets:
            continue
        if abs(nets["foreign"]) + abs(nets["trust"]) + abs(nets["dealer"]) < 1e-9:
            continue
        trade = dict(nets)
        row = {
            "date": d8,
            "session": "night",
            "foreignDelta": trade["foreign"],
            "trustDelta": trade["trust"],
            "dealerDelta": trade["dealer"],
            "retailDelta": trade["retail"],
            "close": close,
            "unitNote": "夜盤列＝日盤淨未平倉＋當夜三大法人成交淨口（大台等值估水位）",
        }
        if base_row:
            row["foreign"] = round(float(base_row.get("foreign") or 0) + trade["foreign"], 2)
            row["trust"] = round(float(base_row.get("trust") or 0) + trade["trust"], 2)
            row["dealer"] = round(float(base_row.get("dealer") or 0) + trade["dealer"], 2)
            row["retail"] = round(float(base_row.get("retail") or 0) + trade["retail"], 2)
        else:
            row["foreign"] = trade["foreign"]
            row["trust"] = trade["trust"]
            row["dealer"] = trade["dealer"]
            row["retail"] = trade["retail"]
        return row
    od = ymd8(old.get("date"))
    if od and (not day8 or od > day8) and old.get("foreign") is not None:
        row = dict(old)
        if close is not None:
            row["close"] = close
        # 舊 night 只有成交淨口：補成水位＋Delta
        if row.get("foreignDelta") is None and base_row:
            trade_f = float(row.get("foreign") or 0)
            trade_t = float(row.get("trust") or 0)
            trade_d = float(row.get("dealer") or 0)
            trade_r = float(row.get("retail") or 0)
            row["foreignDelta"] = trade_f
            row["trustDelta"] = trade_t
            row["dealerDelta"] = trade_d
            row["retailDelta"] = trade_r
            row["foreign"] = round(float(base_row.get("foreign") or 0) + trade_f, 2)
            row["trust"] = round(float(base_row.get("trust") or 0) + trade_t, 2)
            row["dealer"] = round(float(base_row.get("dealer") or 0) + trade_d, 2)
            row["retail"] = round(float(base_row.get("retail") or 0) + trade_r, 2)
            row["unitNote"] = "夜盤列＝日盤淨未平倉＋當夜三大法人成交淨口（大台等值估水位）"
        return row
    return None


def main():
    from when import want_uncovered

    if not want_uncovered():
        print("SKIP uncovered")
        return

    sources = []
    taifex_rows = []
    try:
        taifex_rows = fetch_taifex_inst_hist(HIST_N)
    except Exception as e:
        print("WARN taifex down", e)
    if taifex_rows:
        sources.append("taifex")

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

    # 大額前十：OpenAPI／奇摩仍可用；今日法人淨額以官網 history 為準
    oa_ok = inst_has_ls(oa_inst)
    yh_ok = inst_has_ls(yh_inst)
    if oa_ok and yh_ok:
        use_oa = bool(oa_date and (not yh_date or oa_date >= yh_date))
    elif oa_ok:
        use_oa = True
    else:
        use_oa = False
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

    wearn = []
    try:
        wearn = fetch_wearn_hist(HIST_N)
    except Exception as e:
        print("WARN wearn", e)
    if wearn:
        sources.append("wearn")
    wearn_by = {w["date"]: w for w in wearn}

    cm_map, pc_map = {}, {}
    try:
        cm_map, pc_map = cmoney_series()
        if cm_map:
            sources.append("cmoney")
    except Exception as e:
        print("WARN cmoney", e)
    extra = {}
    try:
        extra = fetch_cmoney_today_extra()
    except Exception as e:
        print("WARN cmoney today", e)

    # history 主路：期交所官網 TX+MTX/4+TMF/20；散戶嚴格 −(F+T+D)
    history = []
    if taifex_rows:
        for d, slot in taifex_rows:
            w = wearn_by.get(d) or {}
            c = cm_map.get(d) or {}
            history.append(
                {
                    "date": d,
                    "foreign": slot.get("foreign"),
                    "trust": slot.get("trust"),
                    "dealer": slot.get("dealer"),
                    "retail": slot.get("retail"),
                    "top5": w.get("top5"),
                    "top10": w.get("top10"),
                    "top5Spec": w.get("top5Spec"),
                    "top10Spec": w.get("top10Spec"),
                    "close": w.get("close"),
                    "pc": c.get("pc", pc_map.get(d)),
                }
            )
    else:
        # 備援：玩股大台 + CMoney 小台 + tmf 微台（散戶仍強制等號）
        tmf_map = load_tmf_by_date()
        if tmf_map:
            sources.append("tmf")
        for w in wearn:
            d = w["date"]
            c = cm_map.get(d) or {}
            t = tmf_map.get(d) or {}
            foreign = tx_eq(w["foreign"], c.get("foreignMtx"), t.get("foreign"))
            trust = tx_eq(w["trust"], c.get("trustMtx"), t.get("trust"))
            dealer = tx_eq(w["dealer"], c.get("dealerMtx"), t.get("dealer"))
            history.append(
                {
                    "date": d,
                    "foreign": foreign,
                    "trust": trust,
                    "dealer": dealer,
                    "retail": retail_from_inst(foreign, trust, dealer),
                    "top5": w["top5"],
                    "top10": w["top10"],
                    "top5Spec": w["top5Spec"],
                    "top10Spec": w["top10Spec"],
                    "close": w["close"],
                    "pc": c.get("pc", pc_map.get(d)),
                }
            )
        if not history:
            for d, c in list(cm_map.items())[:HIST_N]:
                t = tmf_map.get(d) or {}
                foreign = tx_eq(c.get("foreign"), c.get("foreignMtx"), t.get("foreign"))
                trust = tx_eq(None, c.get("trustMtx"), t.get("trust"))
                dealer = tx_eq(None, c.get("dealerMtx"), t.get("dealer"))
                history.append(
                    {
                        "date": d,
                        "foreign": foreign,
                        "trust": trust,
                        "dealer": dealer,
                        "retail": retail_from_inst(foreign, trust, dealer),
                        "top5": None,
                        "top10": None,
                        "top5Spec": None,
                        "top10Spec": None,
                        "close": None,
                        "pc": c.get("pc"),
                    }
                )

    # 保險：任何來源組完後都再強制一次等號
    for rec in history:
        rec["retail"] = retail_from_inst(rec.get("foreign"), rec.get("trust"), rec.get("dealer"))

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

    # 今日卡片／表頭：優先官網最新日
    if history:
        h0 = history[0]
        h0d = _ymd8(h0.get("date"))
        old_top_date = _ymd8(date_pick)
        date = h0d
        date_pick = h0d
        yahoo_date = h0d
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
        # 大額若日期較舊，先清空改吃 wearn/history
        if top and old_top_date and old_top_date < h0d:
            top = []
    else:
        date = _ymd8(yahoo_date or extra.get("date") or "")

    # Yahoo／OpenAPI 偶發只有人名沒多空：用 history 淨額補
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

    vix_map = load_vix_map()
    if vix_map:
        sources.append("vix")
    for rec in history:
        d = ymd8(rec.get("date"))
        if d and d in vix_map:
            rec["vix"] = vix_map[d]
    today_vix = extra.get("vix")
    if today_vix is None:
        today_vix = vix_map.get(ymd8(date))
    if today_vix is None and history:
        today_vix = history[0].get("vix")
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
        "vix": today_vix,
    }
    if history and today.get("vix") is not None:
        history[0]["vix"] = today["vix"]
    n_vix = sum(1 for r in history[:HIST_N] if r.get("vix") is not None)
    day0 = history[0]["date"] if history else date
    night = None
    try:
        night = fetch_latest_night(day0, history[0] if history else None)
    except Exception as e:
        print("WARN night", e)
    if night:
        sources.append("night_ah")
    payload = {
        "fetchedAt": datetime.now(TZ).strftime("%Y-%m-%d %H:%M:%S"),
        "date": ymd_slash(date) if len(date) == 8 else date,
        "source": "+".join(sources),
        "unit": "tx_eq",
        "unitNote": "大台等值口=TX+MTX/4+TMF/20（期交所官網）；散戶=−(外資+投信+自營)",
        "inst": inst,
        "top": top,
        "today": today,
        "history": history[:HIST_N],
        "night": night,
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
        "vix",
        n_vix,
        "date",
        payload["date"],
        "night",
        (night or {}).get("date"),
        "src",
        payload["source"],
    )


if __name__ == "__main__":
    main()

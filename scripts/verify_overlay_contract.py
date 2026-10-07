# -*- coding: utf-8 -*-
"""報價覆蓋契約測試（Quote Overlay Contract）。

驗證 `fetch_quote.apply_mis` + `slim` 的覆蓋結果符合
`01_Docs/報價覆蓋契約.md`。不打網路（monkeypatch `_mis_quote`）。

Usage:
    python scripts/verify_overlay_contract.py   # exit 0 = CONTRACT OK
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import fetch_quote as fq  # noqa: E402

MIS = {
    "SymbolID": "TXFJ6-M",
    "CLastPrice": "49584.00", "CBidPrice1": "49582.00", "CAskPrice1": "49586.00",
    "COpenPrice": "49946.00", "CHighPrice": "49946.00", "CLowPrice": "49540.00",
    "CRefPrice": "49968.00", "CCeilPrice": "54964.00", "CFloorPrice": "44972.00",
    "CDiff": "-384.00", "CDiffRate": "-0.77", "CTotalVolume": "10385",
    "CDate": "20261007", "CTime": "195733",
}

_fails = []


def check(name, cond, got=None):
    if cond:
        print("PASS", name)
    else:
        print("FAIL", name, "got=", got)
        _fails.append(name)


def yahoo_rows():
    return [{
        "symbol": "WTX&", "symbolName": "台指期近一",
        "price": {"raw": "49000"},          # Yahoo 混盤，應被 MIS 蓋
        "orderbook": [
            {"bid": 49582, "bidVol": 3, "ask": 49586, "askVol": 2},
            {"bid": 49580, "bidVol": 1, "ask": 49588, "askVol": 4},
        ],
    }]


# ── case 1：盤別相符（night），OHLC 應為 MIS 當盤值 ──
fq._mis_quote = lambda sess: dict(MIS, _mt=("0" if sess == "day" else "1"),
                                  _want=("0" if sess == "day" else "1"))
rows = fq.apply_mis(yahoo_rows(), "night")
q = fq.slim(fq.pick(rows, "WTX&"))
check("night CLastPrice=MIS", q["CLastPrice"] == "49584", q["CLastPrice"])
check("night COpenPrice=MIS", q["COpenPrice"] == "49946", q["COpenPrice"])
check("night CHighPrice=MIS", q["CHighPrice"] == "49946", q["CHighPrice"])
check("night CLowPrice=MIS", q["CLowPrice"] == "49540", q["CLowPrice"])
check("night CCeilPrice=MIS", q["CCeilPrice"] == "54964", q["CCeilPrice"])
check("night CFloorPrice=MIS", q["CFloorPrice"] == "44972", q["CFloorPrice"])
check("night CRefPrice=MIS", q["CRefPrice"] == "49968", q["CRefPrice"])
check("night CTotalVolume=MIS", q["CTotalVolume"] == "10385", q["CTotalVolume"])
check("night 五檔=Yahoo(不由MIS蓋)",
      q["CBidPrice1"] == "49582" and q["CAskPrice1"] == "49586",
      (q.get("CBidPrice1"), q.get("CAskPrice1")))

# ── case 2：休市 sess=None → 不得寫 OHLC（防他盤污染，交下游 K 線回填）──
rows2 = fq.apply_mis(yahoo_rows(), None)
q2 = fq.slim(fq.pick(rows2, "WTX&"))
check("closed COpenPrice 留空(交K線)", q2["COpenPrice"] == "", q2["COpenPrice"])
check("closed CHighPrice 留空", q2["CHighPrice"] == "", q2["CHighPrice"])
check("closed 仍有 CLastPrice(MIS)", q2["CLastPrice"] == "49584", q2["CLastPrice"])

# ── case 3：盤別不符（want=day 卻回 night）→ 不得寫 OHLC ──
fq._mis_quote = lambda sess: dict(MIS, _mt="1", _want="0")
rows3 = fq.apply_mis(yahoo_rows(), "day")
q3 = fq.slim(fq.pick(rows3, "WTX&"))
check("盤別不符 COpenPrice 留空", q3["COpenPrice"] == "", q3["COpenPrice"])

print()
if _fails:
    print("CONTRACT FAIL:", len(_fails), _fails)
    sys.exit(1)
print("CONTRACT OK")

"""期交所官網 cht/3 下載端點的唯一入口（2026-11-24 建立）。

為什麼要有這支：2026-11-24 期交所把 futContractsDateDown 從 CSV 改成回 HTML 空頁
（590 bytes），當下 fetch_uncovered.py 與 fetch_tmf_retail.py 各有一份 POST 樣板，
同一件事得修兩處、而且容易只修一邊。之後端點 URL、headers、編碼、proxy、逾時
一律只改這支，其餘腳本呼叫它。

刻意 fail-closed：憑證驗證失敗就讓它失敗，不自動降級成 CERT_NONE
（2026-11-06 事故：降級等於開了中間人攻擊路徑，偽造資料照樣寫進 data/ 並 commit）。

用法：
    from taifex_net import post_text, get_text, ENDPOINTS, looks_like_csv

    csv_text = post_text(ENDPOINTS["inst_down"], {...}, referer=ENDPOINTS["inst_view"])
    if not looks_like_csv(csv_text):
        html = get_text(ENDPOINTS["inst_view"] + "?queryDate=2026/11/24")
"""

import ssl
import urllib.parse
import urllib.request

SSL_CTX = ssl.create_default_context()

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0 Safari/537.36"
)

HDR = {
    "User-Agent": UA,
    "Accept": "*/*",
    "Accept-Language": "zh-TW,zh;q=0.9",
    "Content-Type": "application/x-www-form-urlencoded",
    "Origin": "https://www.taifex.com.tw",
}

_BASE = "https://www.taifex.com.tw/cht/3/"

ENDPOINTS = {
    "inst_down": _BASE + "futContractsDateDown",
    "inst_view": _BASE + "futContractsDate",
    "inst_ah": _BASE + "futContractsDateAh",
    "pc_down": _BASE + "pcRatioDown",
    "pc_view": _BASE + "pcRatio",
    "large_down": _BASE + "largeTraderFutDown",
    "large_view": _BASE + "largeTraderFutView",
    "fut_down": _BASE + "futDataDown",
    "fut_view": _BASE + "futDailyMarketView",
}

# 期交所 CSV 是 Big5/cp950；頁面是 UTF-8。兩種都要能吃。
_ENCODINGS = ("utf-8-sig", "cp950", "big5", "utf-8")


def decode_bytes(raw: bytes) -> str:
    for enc in _ENCODINGS:
        try:
            return raw.decode(enc)
        except Exception:
            continue
    return raw.decode("utf-8", "replace")


def _open(req, proxy=None, timeout=90) -> bytes:
    if proxy:
        opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({"http": proxy, "https": proxy}),
            urllib.request.HTTPSHandler(context=SSL_CTX),
        )
        with opener.open(req, timeout=timeout) as r:
            return r.read()
    with urllib.request.urlopen(req, timeout=timeout, context=SSL_CTX) as r:
        return r.read()


def post_text(url: str, data: dict, referer: str = None, proxy: str = None, timeout: int = 90) -> str:
    body = urllib.parse.urlencode(data).encode("utf-8")
    headers = dict(HDR)
    if referer:
        headers["Referer"] = referer
    req = urllib.request.Request(url, data=body, headers=headers)
    return decode_bytes(_open(req, proxy, timeout))


def get_text(url: str, referer: str = None, proxy: str = None, timeout: int = 90) -> str:
    headers = dict(HDR)
    headers.pop("Content-Type", None)
    if referer:
        headers["Referer"] = referer
    req = urllib.request.Request(url, headers=headers)
    return decode_bytes(_open(req, proxy, timeout))


def looks_like_csv(text: str) -> bool:
    """期交所下載端點回 CSV 時首行是欄名；改回 HTML 空頁時含 <table>／<html>。"""
    head = (text or "")[:400].lower()
    return ("<table" not in head) and ("<html" not in head) and ("," in head)

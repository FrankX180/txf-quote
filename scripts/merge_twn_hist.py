# -*- coding: utf-8 -*-
"""rebase 衝突時，把兩側的 twn-hist.json 按時間戳聯集後寫回。

為什麼需要：workflow 的 commit step 在 push 被拒後會 rebase origin/master。
兩邊都可能帶 data/twn-hist.json（另一輪 run 也寫了），單側 checkout 一定會丟資料：
- 取 ours（rebase 中＝upstream）→ 丟掉本次新抓的 bar
- 取 theirs（rebase 中＝本次 commit）→ 丟掉 upstream 在 fetch 之後才寫進去的 bar

用法（在 repo 根目錄、rebase 衝突中執行）：
    python scripts/merge_twn_hist.py
或本機測試：
    python scripts/merge_twn_hist.py --files a.json b.json
"""
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

# repo 根目錄：優先用 Actions 的 GITHUB_WORKSPACE，否則用 cwd
# （不能靠 __file__.parents，因為腳本可能是從 /tmp 的急難副本執行）
ROOT = Path(os.environ.get("GITHUB_WORKSPACE") or Path.cwd()).resolve()
BARS = "data/twn-hist.json"
META = "data/twn-hist-meta.json"
TZ = timezone(timedelta(hours=8))
KEEP_SEC = 14 * 86400


def _parse(raw):
    try:
        data = json.loads(raw.decode("utf-8", "replace") if isinstance(raw, bytes) else raw)
    except Exception:
        return {}
    out = {}
    for row in (data.get("bars") or []):
        try:
            t, c = int(row[0]), float(row[1])
        except Exception:
            continue
        if c > 0:
            out[t] = c
    return out


def load_stage(stage):
    """stage: '2'（ours/upstream）或 '3'（theirs/本次 commit）。沒有該 stage 代表該側沒改過。"""
    r = subprocess.run(["git", "show", "%s:%s" % (stage, BARS)],
                       cwd=str(ROOT), capture_output=True)
    if r.returncode != 0 or not r.stdout:
        return {}
    return _parse(r.stdout)


def load_file(path):
    try:
        return _parse(Path(path).read_bytes())
    except Exception:
        return {}


def merge(ours, theirs):
    merged = dict(ours)
    merged.update(theirs)
    if not merged:
        return []
    bars = sorted(merged.items())
    cut = min(bars[-1][0], int(time.time())) - KEEP_SEC
    return [(t, c) for t, c in bars if t >= cut]


def write_out(bars):
    now = datetime.now(TZ).strftime("%Y-%m-%d %H:%M:%S")
    payload = {
        "updatedAt": now,
        "symbol": "SGX:TWN1!",
        "note": "TradingView 1 分 K 收盤（延遲約 10 分）；epoch 秒；前端與 HiStock 即時尾巴拼接",
        "bars": [[t, c] for t, c in bars],
    }
    for rel, text in (
        (BARS, json.dumps(payload, ensure_ascii=False, separators=(",", ":"))),
        (META, json.dumps({"lastTs": bars[-1][0], "count": len(bars), "updatedAt": now},
                          ensure_ascii=False, separators=(",", ":"))),
    ):
        p = ROOT / rel
        tmp = p.with_name(p.name + ".tmp")
        tmp.write_text(text, encoding="utf-8")
        tmp.replace(p)


def main():
    argv = sys.argv[1:]
    if "--files" in argv:
        i = argv.index("--files")
        a, b = argv[i + 1], argv[i + 2]
        ours, theirs = load_file(a), load_file(b)
        src = "files"
    elif "--stages" in argv:
        i = argv.index("--stages")
        s1, s2 = argv[i + 1], argv[i + 2]
        ours, theirs = load_stage(s1), load_stage(s2)
        src = "stages"
    else:
        ours, theirs = load_stage("2"), load_stage("3")
        src = "stages"
    bars = merge(ours, theirs)
    if not bars:
        print("MERGE-EMPTY (%s): both sides unreadable, refusing to write" % src, flush=True)
        return 1
    if "--dry-run" in argv:
        print("MERGE-DRY (%s) ours=%d theirs=%d merged=%d last=%s"
              % (src, len(ours), len(theirs), len(bars),
                 datetime.fromtimestamp(bars[-1][0], TZ).strftime("%m-%d %H:%M")), flush=True)
        return 0
    write_out(bars)
    print("MERGE-OK (%s) ours=%d theirs=%d merged=%d last=%s"
          % (src, len(ours), len(theirs), len(bars),
             datetime.fromtimestamp(bars[-1][0], TZ).strftime("%m-%d %H:%M")), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())

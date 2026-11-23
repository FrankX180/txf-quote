"""Build ./public for Cloudflare Workers Static Assets.

Whitelist only: index.html + data/*.json (top level). Never copies subdirectories
such as data/Backup, which exist locally and must not be published.

Atomic: builds into public.tmp, then swaps into public/.
"""
from pathlib import Path
import shutil
import sys

ROOT = Path(__file__).resolve().parents[1]
PUB = ROOT / "public"
TMP = ROOT / "public.tmp"

HEADERS = """\
/
  Cache-Control: public, max-age=300

/data/*
  Cache-Control: public, max-age=300
"""


def main() -> int:
    idx = ROOT / "index.html"
    if not idx.exists():
        print("FAIL index.html missing")
        return 1
    src = ROOT / "data"
    if not src.exists():
        print("FAIL data/ missing")
        return 1

    jsons = sorted(p for p in src.glob("*.json") if p.is_file())
    if not any(p.name == "snapshot.json" for p in jsons):
        print("FAIL data/snapshot.json missing")
        return 1

    if TMP.exists():
        shutil.rmtree(TMP)
    (TMP / "data").mkdir(parents=True)

    shutil.copy2(idx, TMP / "index.html")
    for p in jsons:
        shutil.copy2(p, TMP / "data" / p.name)
    (TMP / "_headers").write_text(HEADERS, encoding="utf-8")

    if PUB.exists():
        shutil.rmtree(PUB)
    TMP.rename(PUB)

    size = sum(p.stat().st_size for p in PUB.rglob("*") if p.is_file())
    print("OK public/ data=%d MB=%.2f" % (len(jsons), size / 1048576))
    return 0


if __name__ == "__main__":
    sys.exit(main())

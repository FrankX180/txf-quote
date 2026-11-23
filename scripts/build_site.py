"""Build ./public for Cloudflare Workers Static Assets.

Copies index.html and data/ into public/, plus _headers for cache policy.
Idempotent: wipes public/ first. Never touches git-tracked files.
"""
from pathlib import Path
import shutil
import sys

ROOT = Path(__file__).resolve().parents[1]
PUB = ROOT / "public"

HEADERS = """\
/
  Cache-Control: public, max-age=300

/data/*
  Cache-Control: public, max-age=300
"""


def main() -> int:
    if PUB.exists():
        shutil.rmtree(PUB)
    PUB.mkdir(parents=True)

    idx = ROOT / "index.html"
    if not idx.exists():
        print("FAIL index.html missing")
        return 1
    shutil.copy2(idx, PUB / "index.html")

    src = ROOT / "data"
    if not src.exists():
        print("FAIL data/ missing")
        return 1
    shutil.copytree(src, PUB / "data")

    (PUB / "_headers").write_text(HEADERS, encoding="utf-8")

    n = sum(1 for _ in (PUB / "data").rglob("*") if _.is_file())
    size = sum(p.stat().st_size for p in PUB.rglob("*") if p.is_file())
    print("OK public/ files=%d data=%d MB=%.2f" % (len(list(PUB.rglob("*"))), n, size / 1048576))
    return 0


if __name__ == "__main__":
    sys.exit(main())

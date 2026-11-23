"""Deploy ./public to Cloudflare Workers Static Assets (worker: txf-site).

Credential priority (same as scripts/deploy_worker.py):
  1) env CLOUDFLARE_API_TOKEN  -> Bearer token
  2) env CLOUDFLARE_API_KEY (+ CLOUDFLARE_EMAIL) -> Global API Key
  3) local secrets file E:\\_PluginTools\\Memory\\secrets\\LLM_API_KEY.MD

Token is only injected into the child process env; never printed.
"""
from pathlib import Path
import os
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import deploy_worker as dw  # noqa: E402

CFG = ROOT / "wrangler.site.toml"


def main() -> int:
    if not (ROOT / "public" / "index.html").exists():
        print("FAIL public/index.html missing; run scripts/build_site.py first")
        return 1

    env = dict(os.environ)
    env["CLOUDFLARE_ACCOUNT_ID"] = dw.AID
    token = (env.get("CLOUDFLARE_API_TOKEN") or "").strip()
    gkey = (env.get("CLOUDFLARE_API_KEY") or "").strip()
    if token:
        env["CLOUDFLARE_API_TOKEN"] = token
    elif gkey:
        env["CLOUDFLARE_API_KEY"] = gkey
        env.setdefault("CLOUDFLARE_EMAIL", dw.EMAIL)
    else:
        key = dw.cf_key()
        if dw.CF_MODE == "token":
            env["CLOUDFLARE_API_TOKEN"] = key
        else:
            env["CLOUDFLARE_API_KEY"] = key
            env["CLOUDFLARE_EMAIL"] = dw.EMAIL

    npx = shutil.which("npx") or shutil.which("npx.cmd") or "npx"
    # 固定版本：CI 每次都是乾淨環境，跑 @latest 會有未來 breaking 的風險
    cmd = [
        npx, "--yes", "wrangler@4.166.0", "deploy",
        "--config", str(CFG),
    ]
    print("deploy txf-site ...")
    try:
        r = subprocess.run(cmd, cwd=str(ROOT), env=env, timeout=600)
    except subprocess.TimeoutExpired:
        print("FAIL wrangler timeout after 600s (network/npx hang)")
        return 3
    if r.returncode != 0:
        print("FAIL wrangler exit", r.returncode)
        return r.returncode
    print("OK deployed")
    return 0


if __name__ == "__main__":
    sys.exit(main())

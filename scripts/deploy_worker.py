# Deploy worker/yahoo-quote.js + bind D1 database txf-imb
from pathlib import Path
import json
import uuid
import urllib.request
import urllib.error

SECRETS = Path(r"E:\_PluginTools\Memory\secrets\LLM_API_KEY.MD")
WORKER = Path(r"E:\_Project\FuturesHTML\worker\yahoo-quote.js")
EMAIL = "fx0926@gmail.com"
AID = "a623d11cc8b419579d99db54c35b8d79"
ZID = "39099a55a79cb78d956a71bba62dcf1c"
NAME = "txf-yahoo"
DB_NAME = "txf-imb"


def cf_key():
    lines = SECRETS.read_text(encoding="utf-8").splitlines()
    for i, line in enumerate(lines):
        if line.strip().upper() == "CLOUDFLARE API KEY" and i + 1 < len(lines):
            return lines[i + 1].strip()
    raise SystemExit("no CF key")


def call(method, url, data=None, headers=None, raw=None):
    key = cf_key()
    h = {"X-Auth-Email": EMAIL, "X-Auth-Key": key}
    if headers:
        h.update(headers)
    body = raw
    if data is not None:
        h["Content-Type"] = "application/json"
        body = json.dumps(data).encode()
    req = urllib.request.Request(url, data=body, headers=h, method=method)
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        txt = e.read().decode("utf-8", "replace")
        print("HTTP", e.code, method, url.split("/v4/")[-1][:90], txt[:500])
        try:
            return json.loads(txt)
        except Exception:
            return None


def ensure_d1():
    listed = call(
        "GET", f"https://api.cloudflare.com/client/v4/accounts/{AID}/d1/database"
    )
    if listed and listed.get("success"):
        for db in listed.get("result") or []:
            if db.get("name") == DB_NAME:
                print("d1 exists", db.get("uuid"), db.get("name"))
                return db.get("uuid")
    created = call(
        "POST",
        f"https://api.cloudflare.com/client/v4/accounts/{AID}/d1/database",
        {"name": DB_NAME},
    )
    print("create d1", created and created.get("success"), (created or {}).get("errors"))
    if created and created.get("success"):
        return (created.get("result") or {}).get("uuid")
    return None


def multipart_put(url, metadata, filename, script):
    boundary = "----" + uuid.uuid4().hex
    parts = []

    def add(name, content, ctype, fname=None):
        disp = 'form-data; name="%s"' % name
        if fname:
            disp += '; filename="%s"' % fname
        chunk = (
            "--%s\r\nContent-Disposition: %s\r\nContent-Type: %s\r\n\r\n"
            % (boundary, disp, ctype)
        ).encode() + content + b"\r\n"
        parts.append(chunk)

    add("metadata", json.dumps(metadata).encode(), "application/json")
    add(filename, script, "application/javascript+module", filename)
    body = b"".join(parts) + ("--%s--\r\n" % boundary).encode()
    return call(
        "PUT",
        url,
        raw=body,
        headers={"Content-Type": "multipart/form-data; boundary=" + boundary},
    )


def main():
    db_id = ensure_d1()
    if not db_id:
        raise SystemExit("no d1 uuid")
    script = WORKER.read_bytes()
    meta = {
        "main_module": "yahoo-quote.js",
        "compatibility_date": "2026-08-13",
        "usage_model": "standard",
        "cache_options": {"enabled": True, "cross_version_cache": True},
        "bindings": [
            {
                "type": "d1",
                "name": "IMB_DB",
                "id": db_id,
            }
        ],
    }
    up = multipart_put(
        f"https://api.cloudflare.com/client/v4/accounts/{AID}/workers/scripts/{NAME}",
        meta,
        "yahoo-quote.js",
        script,
    )
    print("upload", up and up.get("success"), (up or {}).get("errors"))
    if not (up and up.get("success")):
        raise SystemExit(1)
    sub = call(
        "POST",
        f"https://api.cloudflare.com/client/v4/accounts/{AID}/workers/scripts/{NAME}/subdomain",
        {"enabled": True},
    )
    print("workers_dev", sub and sub.get("success"))
    dom = call(
        "PUT",
        f"https://api.cloudflare.com/client/v4/accounts/{AID}/workers/domains",
        {
            "hostname": "wtx.19850926.xyz",
            "service": NAME,
            "zone_id": ZID,
        },
    )
    print(
        "domain",
        dom and dom.get("success"),
        (dom or {}).get("errors") or (dom or {}).get("result"),
    )
    print("OK d1", db_id)
    # Cron：每分鐘自打（CF 最短 1 分；真 5 秒另用本機守護）
    sched = call(
        "PUT",
        f"https://api.cloudflare.com/client/v4/accounts/{AID}/workers/scripts/{NAME}/schedules",
        [{"cron": "* * * * *"}],
    )
    print("cron", sched and sched.get("success"), (sched or {}).get("errors") or (sched or {}).get("result"))


if __name__ == "__main__":
    main()

#!/usr/bin/env python3

import json
import os
import signal
import subprocess
import time

BASE = "/opt/nikatel-probe-manager"
DB = f"{BASE}/configs.json"
CONFIG_DIR = f"{BASE}/configs"
LOG_DIR = f"{BASE}/logs"
XRAY = "/opt/nikatel-probe/xray/xray"

XRAY_NEW = "/opt/nikatel-probe/xray-new/xray"


def xray_for(path):
    """VLESS Encryption (mlkem...) needs the newer Xray; every other config keeps the old one."""
    import json as _j, os as _o
    try:
        d = _j.load(open(path))
        for ob in d.get("outbounds", []):
            for v in (ob.get("settings") or {}).get("vnext", []):
                for u in v.get("users", []):
                    if str(u.get("encryption", "none")).lower() not in ("none", ""):
                        return XRAY_NEW if _o.path.exists(XRAY_NEW) else XRAY
    except Exception:
        pass
    return XRAY

processes = {}
stopping = False


def load_db():
    with open(DB, "r") as f:
        return json.load(f)


def file_mtime(path):
    try:
        return os.path.getmtime(path)
    except OSError:
        return 0


def start_probe(item):
    slug = item["slug"]
    config = f"{CONFIG_DIR}/{slug}.json"
    logfile = f"{LOG_DIR}/{slug}.log"

    if not os.path.exists(config):
        print(
            f"ERROR {item['name']} config file missing",
            flush=True
        )
        return None

    log = open(logfile, "a")

    proc = subprocess.Popen(
        [xray_for(config), "run", "-config", config],
        stdout=log,
        stderr=subprocess.STDOUT
    )

    processes[slug] = {
        "proc": proc,
        "item": item,
        "log": log,
        "restarts": 0,
        "mtime": file_mtime(config)
    }

    print(
        f"START {item['name']} "
        f"port={item['port']} "
        f"pid={proc.pid}",
        flush=True
    )

    return proc


def restart_probe(slug):
    info = processes.get(slug)

    if not info:
        return

    item = info["item"]

    try:
        info["log"].close()
    except Exception:
        pass

    time.sleep(2)

    config = f"{CONFIG_DIR}/{slug}.json"
    logfile = f"{LOG_DIR}/{slug}.log"

    log = open(logfile, "a")

    proc = subprocess.Popen(
        [xray_for(config), "run", "-config", config],
        stdout=log,
        stderr=subprocess.STDOUT
    )

    restarts = info.get("restarts", 0) + 1

    processes[slug] = {
        "proc": proc,
        "item": item,
        "log": log,
        "restarts": restarts,
        "mtime": file_mtime(config)
    }

    print(
        f"RECOVER {item['name']} "
        f"pid={proc.pid} "
        f"restart={restarts}",
        flush=True
    )


def stop_all(*args):
    global stopping
    stopping = True

    print("Stopping probes...", flush=True)

    for info in processes.values():
        proc = info["proc"]

        if proc.poll() is None:
            try:
                proc.terminate()
            except Exception:
                pass

    deadline = time.time() + 5

    while time.time() < deadline:
        alive = [
            x["proc"]
            for x in processes.values()
            if x["proc"].poll() is None
        ]

        if not alive:
            break

        time.sleep(0.2)

    for info in processes.values():
        proc = info["proc"]

        if proc.poll() is None:
            try:
                proc.kill()
            except Exception:
                pass

        try:
            info["log"].close()
        except Exception:
            pass

    raise SystemExit(0)


def stop_probe(slug):
    info = processes.pop(slug, None)

    if not info:
        return

    proc = info["proc"]

    if proc.poll() is None:
        try:
            proc.terminate()
        except Exception:
            pass

        try:
            proc.wait(timeout=3)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass

    try:
        info["log"].close()
    except Exception:
        pass


db_mtime = [0.0]


def reconcile():
    """
    Apply changes of configs.json without touching unchanged probes:
    start new ones, stop removed / disabled ones, reload ones whose
    Xray config file changed.
    """
    try:
        mtime = os.path.getmtime(DB)
    except OSError:
        return

    if mtime == db_mtime[0]:
        return

    try:
        data = load_db()
    except Exception as e:
        print(f"RECONCILE cannot read db: {e}", flush=True)
        return

    db_mtime[0] = mtime

    wanted = {
        x["slug"]: x
        for x in data["configs"]
        if x.get("enabled", True)
    }

    for slug in list(processes):
        if slug not in wanted:
            stop_probe(slug)
            print(f"STOP {slug} (removed or disabled)", flush=True)

    for slug, item in wanted.items():
        config = f"{CONFIG_DIR}/{slug}.json"
        info = processes.get(slug)

        if info is None:
            start_probe(item)
            time.sleep(0.05)

        elif info.get("mtime") != file_mtime(config):
            stop_probe(slug)
            start_probe(item)
            print(f"RELOAD {slug} (config changed)", flush=True)

        else:
            # name / push_url may have changed
            info["item"] = item


signal.signal(signal.SIGTERM, stop_all)
signal.signal(signal.SIGINT, stop_all)


db = load_db()

enabled = [
    item
    for item in db["configs"]
    if item.get("enabled", True)
]

for item in enabled:
    start_probe(item)
    time.sleep(0.15)


print(
    f"{len(processes)} probes started",
    flush=True
)

try:
    db_mtime[0] = os.path.getmtime(DB)
except OSError:
    pass


while True:

    if stopping:
        break

    reconcile()

    for slug in list(processes.keys()):

        info = processes[slug]
        proc = info["proc"]

        code = proc.poll()

        if code is not None:

            print(
                f"CRASH {info['item']['name']} "
                f"exit={code}",
                flush=True
            )

            restart_probe(slug)

    time.sleep(1)

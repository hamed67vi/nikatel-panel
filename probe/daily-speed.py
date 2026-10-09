#!/usr/bin/env python3

import json
import subprocess
import time
import re
import os
from datetime import datetime, timezone

BASE = "/opt/nikatel-probe-manager"
DB = f"{BASE}/configs.json"
STATE_DIR = f"{BASE}/speed-state"

GAP = 600

os.makedirs(STATE_DIR, exist_ok=True)

with open(DB) as f:
    db = json.load(f)

configs = [
    x for x in db["configs"]
    if x.get("enabled", True)
]

print(
    f"=== DAILY SPEED TEST {datetime.now():%Y-%m-%d %H:%M:%S} "
    f"configs={len(configs)} ===",
    flush=True
)

for index, item in enumerate(configs):

    name = item["name"]
    slug = item["slug"]

    print(
        f"\n[{index + 1}/{len(configs)}] SPEED {name}",
        flush=True
    )

    result = subprocess.run(
        ["/usr/local/bin/nikatel-probe", "speed", name],
        capture_output=True,
        text=True,
        timeout=180
    )

    output = result.stdout

    print(output, flush=True)

    ping_match = re.search(
        r"Ping:\s+([\d.]+)\s*ms",
        output
    )

    dl_match = re.search(
        r"Download:\s+([\d.]+)\s*Mbps",
        output
    )

    ul_match = re.search(
        r"Upload:\s+([\d.]+)\s*Mbps",
        output
    )

    if (
        result.returncode == 0
        and ping_match
        and dl_match
        and ul_match
    ):

        state = {
            "name": name,
            "ping": float(ping_match.group(1)),
            "download": float(dl_match.group(1)),
            "upload": float(ul_match.group(1)),
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "ok": True
        }

        tmp = f"{STATE_DIR}/{slug}.json.tmp"
        final = f"{STATE_DIR}/{slug}.json"

        with open(tmp, "w") as f:
            json.dump(state, f, indent=2)

        os.replace(tmp, final)

        print(
            f"SAVED {name}: "
            f"DL={state['download']:.2f} Mbps "
            f"UL={state['upload']:.2f} Mbps",
            flush=True
        )

    else:
        print(
            f"FAILED {name}: speed result not saved",
            flush=True
        )

    if index < len(configs) - 1:
        print(
            f"Waiting {GAP} seconds before next test...",
            flush=True
        )
        time.sleep(GAP)

print(
    f"\n=== DAILY SPEED TEST FINISHED "
    f"{datetime.now():%Y-%m-%d %H:%M:%S} ===",
    flush=True
)

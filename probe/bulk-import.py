#!/usr/bin/env python3

import json
import os
import re
import subprocess
import sys
import urllib.parse

BASE = "/opt/nikatel-probe-manager"
DB = f"{BASE}/configs.json"
IMPORT = f"{BASE}/import.txt"
CONFIG_DIR = f"{BASE}/configs"
XRAY = "/opt/nikatel-probe/xray/xray"

sys.path.insert(0, BASE)
from parser import make_xray_config


def slugify(name):
    s = re.sub(r'[^a-zA-Z0-9]+', '-', name.lower()).strip('-')
    return s or "config"


def get_name(uri):
    fragment = urllib.parse.urlparse(uri).fragment
    name = urllib.parse.unquote(fragment).strip()

    # حذف flag/emoji از ابتدای نام
    name = re.sub(r'^[^\w]+', '', name, flags=re.UNICODE).strip()

    return name or "Unknown"


with open(DB) as f:
    db = json.load(f)

with open(IMPORT) as f:
    uris = [x.strip() for x in f if x.strip()]

used_ports = {int(x["port"]) for x in db["configs"]}
existing = {x["slug"] for x in db["configs"]}

port = int(db["settings"]["start_port"])

print(f"Found {len(uris)} configs")
print()

added = 0
failed = 0
skipped = 0

for uri in uris:

    name = get_name(uri)
    slug = slugify(name)

    if slug in existing:
        print(f"SKIP   {name} - already exists")
        skipped += 1
        continue

    while port in used_ports:
        port += 1

    try:
        config = make_xray_config(uri, port)

        path = f"{CONFIG_DIR}/{slug}.json"

        with open(path, "w") as f:
            json.dump(config, f, indent=2)

        test = subprocess.run(
            [XRAY, "run", "-test", "-config", path],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True
        )

        if test.returncode != 0:
            print(f"FAIL   {name} - Xray rejected config")
            print(test.stdout)
            os.remove(path)
            failed += 1
            continue

        db["configs"].append({
            "name": name,
            "slug": slug,
            "uri": uri,
            "push_url": "",
            "port": port,
            "enabled": True
        })

        existing.add(slug)
        used_ports.add(port)

        print(f"OK     {name:<20} -> SOCKS {port}")

        added += 1
        port += 1

    except Exception as e:
        print(f"FAIL   {name}: {e}")
        failed += 1


tmp = DB + ".tmp"

with open(tmp, "w") as f:
    json.dump(db, f, indent=2)

os.replace(tmp, DB)

print()
print("==============================")
print(f"Added:   {added}")
print(f"Skipped: {skipped}")
print(f"Failed:  {failed}")
print("==============================")

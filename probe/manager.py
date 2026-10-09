#!/usr/bin/env python3

import json
import os
import re
import sys
import subprocess
import time
import urllib.parse

BASE = "/opt/nikatel-probe-manager"
DB = f"{BASE}/configs.json"
CONFIG_DIR = f"{BASE}/configs"
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

MANAGER_SERVICE = "nikatel-probe-manager"
MONITOR_SERVICE = "nikatel-probe-monitor"

sys.path.insert(0, BASE)
from parser import make_xray_config


def load_db():
    with open(DB, "r") as f:
        return json.load(f)


def save_db(data):
    tmp = DB + ".tmp"

    with open(tmp, "w") as f:
        json.dump(data, f, indent=2)

    os.replace(tmp, DB)


def slugify(name):
    safe = "".join(
        c.lower() if c.isalnum() else "-"
        for c in name
    )

    while "--" in safe:
        safe = safe.replace("--", "-")

    return safe.strip("-")


def name_from_uri(uri):
    try:
        fragment = urllib.parse.urlparse(uri).fragment
        name = urllib.parse.unquote(fragment).strip()

        # حذف flag/emoji از ابتدای اسم
        name = re.sub(r"^[^\w]+", "", name, flags=re.UNICODE)

        if name:
            return name

    except Exception:
        pass

    return ""


def next_port(data):
    port = int(data["settings"]["start_port"])
    used = {int(x["port"]) for x in data["configs"]}

    while port in used:
        port += 1

    return port


def find_item(data, name):
    wanted = name.strip().lower()

    for item in data["configs"]:
        if item["name"].lower() == wanted:
            return item

        if item["slug"].lower() == wanted:
            return item

    return None


def write_xray_config(item):
    os.makedirs(CONFIG_DIR, exist_ok=True)

    path = f'{CONFIG_DIR}/{item["slug"]}.json'
    tmp = f'{CONFIG_DIR}/{item["slug"]}.tmp.json'

    config = make_xray_config(
        item["uri"],
        item["port"]
    )

    with open(tmp, "w") as f:
        json.dump(config, f, indent=2)

    ok, output = xray_test(tmp)

    if not ok:
        try:
            os.remove(tmp)
        except Exception:
            pass

        raise RuntimeError(output)

    os.replace(tmp, path)

    return path


def xray_test(path):
    result = subprocess.run(
        [xray_for(path), "run", "-test", "-config", path],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True
    )

    return result.returncode == 0, result.stdout


def restart_services():
    subprocess.run(
        ["systemctl", "restart", MANAGER_SERVICE],
        check=True
    )

    # کمی زمان برای بالا آمدن SOCKSها
    time.sleep(2)

    subprocess.run(
        ["systemctl", "restart", MONITOR_SERVICE],
        check=True
    )


def test_proxy(item):
    data = load_db()

    timeout = str(
        data["settings"].get("timeout", 10)
    )

    test_url = data["settings"].get(
        "test_url",
        "https://www.gstatic.com/generate_204"
    )

    result = subprocess.run(
        [
            "/usr/bin/curl",
            "--socks5-hostname",
            f'127.0.0.1:{item["port"]}',
            "--max-time", timeout,
            "-o", "/dev/null",
            "-sS",
            "-w", "%{http_code} %{time_total}",
            test_url
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True
    )

    output = result.stdout.strip().split()

    if len(output) != 2:
        return False, 0, result.stderr.strip()

    code, total = output

    if code != "204":
        return False, 0, f"HTTP {code}"

    try:
        ping = round(float(total) * 1000)
    except Exception:
        ping = 0

    return True, ping, ""


def list_configs():
    data = load_db()

    if not data["configs"]:
        print("No configs added")
        return

    print()
    print(
        f'{"NAME":20} '
        f'{"PORT":8} '
        f'{"ENABLED":9} '
        f'{"PUSH":6}'
    )

    print("-" * 50)

    for item in data["configs"]:
        print(
            f'{item["name"][:20]:20} '
            f'{item["port"]:<8} '
            f'{str(item.get("enabled", True)):<9} '
            f'{"YES" if item.get("push_url") else "NO":6}'
        )


def add_config():
    data = load_db()

    # پشتیبانی از:
    # nikatel-probe add URI PUSH_URL
    if len(sys.argv) >= 3:
        uri = sys.argv[2].strip()
    else:
        uri = input(
            "Paste a vless:// vmess:// trojan:// or ss:// link: "
        ).strip()

    if not uri:
        print("ERROR: config URL cannot be empty")
        return 1

    detected_name = name_from_uri(uri)

    if detected_name:
        name = detected_name
        print(f"Detected name: {name}")
    else:
        name = input("Config name: ").strip()

    if not name:
        print("ERROR: name cannot be empty")
        return 1

    slug = slugify(name)

    if not slug:
        print("ERROR: invalid name")
        return 1

    if any(
        x["slug"] == slug
        for x in data["configs"]
    ):
        print(
            f"ERROR: config '{name}' already exists"
        )
        return 1

    if len(sys.argv) >= 4:
        push = sys.argv[3].strip()
    else:
        push = input(
            "Paste Kuma Push URL: "
        ).strip()

    # فقط base push URL ذخیره شود
    if push:
        push = push.split("?")[0]

    port = next_port(data)

    item = {
        "name": name,
        "slug": slug,
        "uri": uri,
        "push_url": push,
        "port": port,
        "enabled": True
    }

    try:
        path = write_xray_config(item)

    except Exception as e:
        print("ERROR: Xray rejected configuration")
        print(e)
        return 1

    print("Xray configuration OK")
    print(f"SOCKS port: {port}")

    # اول DB ذخیره می‌شود تا manager آن را اجرا کند
    data["configs"].append(item)
    save_db(data)

    try:
        restart_services()

    except Exception as e:
        print(f"ERROR restarting services: {e}")
        return 1

    print("Services restarted")
    print("Testing config from Iran...")

    up, ping, error = test_proxy(item)

    if up:
        print(f"SUCCESS: {ping} ms")
    else:
        print(f"FAILED: {error or 'connectivity test failed'}")

    print("Config saved")

    return 0



def update_config_force(name, uri="", new_name=""):
    """
    Update an existing config without changing its slug,
    SOCKS port, push URL or enabled state.

    uri may be empty for rename-only operations.
    new_name may be empty for URI-only operations.
    """

    data = load_db()
    item = find_item(data, name)

    if not item:
        print(f"ERROR: config '{name}' not found")
        return 1

    uri = str(uri or "").strip()
    new_name = str(new_name or "").strip()

    if not uri and not new_name:
        print("ERROR: uri or new_name is required")
        return 1

    if uri and not (
        uri.startswith("vless://")
        or uri.startswith(("vmess://", "trojan://", "ss://"))
    ):
        print("ERROR: only VLESS / VMess / Trojan / Shadowsocks configs are supported")
        return 1

    old_item = dict(item)

    target_name = new_name or old_item["name"]

    if not target_name:
        print("ERROR: config name cannot be empty")
        return 1

    if len(target_name) > 120:
        print("ERROR: config name is too long")
        return 1

    # Do not allow a renamed config to become ambiguous
    # with another config name or slug.
    wanted = target_name.lower()

    for other in data.get("configs", []):
        if other is item:
            continue

        other_name = str(
            other.get("name", "")
        ).lower()

        other_slug = str(
            other.get("slug", "")
        ).lower()

        if wanted in (other_name, other_slug):
            print(
                f"ERROR: config name '{target_name}' "
                "conflicts with another config"
            )
            return 1

    candidate = dict(old_item)
    candidate["name"] = target_name

    if uri:
        candidate["uri"] = uri

    uri_changed = (
        candidate.get("uri")
        != old_item.get("uri")
    )

    name_changed = (
        candidate.get("name")
        != old_item.get("name")
    )

    # Validate and atomically replace Xray configuration
    # only when the actual proxy URI has changed.
    if uri_changed:
        try:
            write_xray_config(candidate)

        except Exception as e:
            print("ERROR: Xray rejected updated configuration")
            print(e)
            return 1

    item["name"] = candidate["name"]

    if uri_changed:
        item["uri"] = candidate["uri"]

    try:
        save_db(data)

    except Exception as e:
        # Restore old Xray file if DB write fails.
        if uri_changed:
            try:
                write_xray_config(old_item)
            except Exception:
                pass

        print(f"ERROR saving database: {e}")
        return 1

    # URI changes require the proxy processes to reload.
    # A display/name-only change does not need to interrupt
    # the running SOCKS proxies.
    if uri_changed:
        try:
            restart_services()

        except Exception as e:
            print(
                "ERROR restarting services; "
                "rolling back config"
            )

            # Restore DB
            item.clear()
            item.update(old_item)

            try:
                save_db(data)
            except Exception:
                pass

            # Restore previous Xray config
            try:
                write_xray_config(old_item)
            except Exception:
                pass

            # Best-effort recovery restart
            try:
                restart_services()
            except Exception:
                pass

            print(e)
            return 1

    print(
        "Config updated successfully | "
        f"name={item['name']} | "
        f"slug={item['slug']} | "
        f"port={item['port']} | "
        f"name_changed={'yes' if name_changed else 'no'} | "
        f"uri_changed={'yes' if uri_changed else 'no'}"
    )

    return 0


def test_config(name):
    data = load_db()
    item = find_item(data, name)

    if not item:
        print(f"ERROR: config '{name}' not found")
        return 1

    if not item.get("enabled", True):
        print(f"ERROR: {item['name']} is disabled")
        return 1

    print(
        f"Testing {item['name']} "
        f"via SOCKS 127.0.0.1:{item['port']}..."
    )

    up, ping, error = test_proxy(item)

    if up:
        print(f"UP - {ping} ms")
        return 0

    print(f"DOWN - {error or 'connection failed'}")
    return 1


def set_enabled(name, enabled):
    data = load_db()
    item = find_item(data, name)

    if not item:
        print(f"ERROR: config '{name}' not found")
        return 1

    item["enabled"] = enabled
    save_db(data)

    restart_services()

    state = "enabled" if enabled else "disabled"

    print(
        f"{item['name']} {state} successfully"
    )

    return 0


def remove_config_force(name):
    """
    Non-interactive config removal for API use.
    """
    data = load_db()
    item = find_item(data, name)

    if not item:
        print(f"ERROR: config '{name}' not found")
        return 1

    data["configs"] = [
        x for x in data["configs"]
        if x["slug"] != item["slug"]
    ]

    save_db(data)

    path = f'{CONFIG_DIR}/{item["slug"]}.json'

    if os.path.exists(path):
        os.remove(path)

    # Remove cached state/log files if present.
    for extra_path in (
        f'/opt/nikatel-probe-manager/health-state/{item["slug"]}.json',
        f'/opt/nikatel-probe-manager/speed-state/{item["slug"]}.json',
        f'/opt/nikatel-probe-manager/logs/{item["slug"]}.log',
    ):
        try:
            if os.path.exists(extra_path):
                os.remove(extra_path)
        except Exception:
            pass

    restart_services()

    print(
        f'{item["name"]} removed successfully'
    )

    return 0



def remove_config(name):
    data = load_db()
    item = find_item(data, name)

    if not item:
        print(f"ERROR: config '{name}' not found")
        return 1

    answer = input(
        f"Remove {item['name']}? [y/N]: "
    ).strip().lower()

    if answer not in ("y", "yes"):
        print("Cancelled")
        return 0

    data["configs"] = [
        x for x in data["configs"]
        if x["slug"] != item["slug"]
    ]

    save_db(data)

    path = f'{CONFIG_DIR}/{item["slug"]}.json'

    if os.path.exists(path):
        os.remove(path)

    restart_services()

    print(
        f"{item['name']} removed successfully"
    )

    return 0


def status():
    data = load_db()

    print()
    print("SERVICE STATUS")
    print("-" * 50)

    for service in (
        MANAGER_SERVICE,
        MONITOR_SERVICE
    ):
        result = subprocess.run(
            ["systemctl", "is-active", service],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True
        )

        state = result.stdout.strip()

        print(f"{service:30} {state}")

    print()
    print(
        f'{"NAME":20} '
        f'{"PORT":8} '
        f'{"STATE":10} '
        f'{"PING":10}'
    )

    print("-" * 55)

    for item in data["configs"]:

        if not item.get("enabled", True):
            print(
                f'{item["name"][:20]:20} '
                f'{item["port"]:<8} '
                f'{"DISABLED":10} '
                f'{"-":10}'
            )
            continue

        up, ping, error = test_proxy(item)

        print(
            f'{item["name"][:20]:20} '
            f'{item["port"]:<8} '
            f'{"UP" if up else "DOWN":10} '
            f'{(str(ping) + "ms") if up else "-":10}'
        )



def speed_config(name):
    data = load_db()
    item = find_item(data, name)

    if not item:
        print(f"ERROR: config not found: {name}")
        return

    port = item["port"]

    print()
    print(f"Speed Test: {item['name']}")
    print(f"SOCKS: 127.0.0.1:{port}")
    print("Testing route through config...")
    print()

    # Latency
    start = time.time()

    result = subprocess.run(
        [
            "/usr/bin/curl",
            "--socks5-hostname", f"127.0.0.1:{port}",
            "--max-time", "15",
            "-o", "/dev/null",
            "-sS",
            "-w", "%{http_code}",
            "https://www.gstatic.com/generate_204"
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True
    )

    latency = round((time.time() - start) * 1000)

    if result.returncode != 0 or result.stdout.strip() != "204":
        print("FAILED: config is not reachable")
        if result.stderr.strip():
            print(result.stderr.strip())
        return

    print(f"Ping:      {latency} ms")

    # Download test - Cloudflare 25 MB
    dl = subprocess.run(
        [
            "/usr/bin/curl",
            "--socks5-hostname", f"127.0.0.1:{port}",
            "--max-time", "60",
            "-L",
            "-o", "/dev/null",
            "-sS",
            "-w", "%{speed_download}",
            "https://speed.cloudflare.com/__down?bytes=25000000"
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True
    )

    try:
        dl_bps = float(dl.stdout.strip())
        dl_mbps = dl_bps * 8 / 1000000
    except Exception:
        dl_mbps = 0

    print(f"Download:  {dl_mbps:.2f} Mbps")

    # Upload test - generate 10 MB locally and send through SOCKS
    upload_file = "/tmp/nikatel-speed-upload.bin"

    try:
        with open(upload_file, "wb") as f:
            f.truncate(10_000_000)

        ul = subprocess.run(
            [
                "/usr/bin/curl",
                "--socks5-hostname", f"127.0.0.1:{port}",
                "--max-time", "60",
                "-o", "/dev/null",
                "-sS",
                "-w", "%{speed_upload}",
                "-X", "POST",
                "--data-binary", f"@{upload_file}",
                "https://speed.cloudflare.com/__up"
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True
        )

        try:
            ul_bps = float(ul.stdout.strip())
            ul_mbps = ul_bps * 8 / 1000000
        except Exception:
            ul_mbps = 0

    finally:
        try:
            os.remove(upload_file)
        except Exception:
            pass

    print(f"Upload:    {ul_mbps:.2f} Mbps")
    print()

    # Save latest speed-test result
    from datetime import datetime, timezone

    state_dir = f"{BASE}/speed-state"
    os.makedirs(state_dir, exist_ok=True)

    state = {
        "name": item["name"],
        "ping": float(latency),
        "download": round(dl_mbps, 2),
        "upload": round(ul_mbps, 2),
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "ok": True
    }

    state_path = f'{state_dir}/{item["slug"]}.json'
    tmp_path = state_path + ".tmp"

    with open(tmp_path, "w") as f:
        json.dump(state, f, indent=2)

    os.replace(tmp_path, state_path)


def wait_for_probes(ports, timeout=25):
    """
    run-all.py starts / reloads probes by itself when configs.json
    changes. Wait until the SOCKS ports of the given probes accept
    connections. Returns the ports that never came up.
    """
    import socket

    pending = set(ports)
    deadline = time.time() + timeout

    while pending and time.time() < deadline:
        for port in list(pending):
            try:
                socket.create_connection(
                    ("127.0.0.1", int(port)),
                    timeout=0.3
                ).close()
                pending.discard(port)
            except OSError:
                pass

        if pending:
            time.sleep(0.2)

    return sorted(pending)


def bulk_apply(spec):
    """
    Several config changes with ONE restart of the probe services
    (instead of one full restart per config).

    spec = {
      "add":    [{"uri": "...", "name": "optional", "push_url": "optional"}],
      "update": [{"name": "...", "uri": "..."}],     # URI change only
      "delete": ["name", ...]
    }

    Per-item problems are reported in the result and never abort
    the rest of the batch. Does not run the per-config curl test
    (the monitor service health-checks every config anyway).
    """
    data = load_db()

    out = {"add": [], "update": [], "delete": []}

    changed = False

    # ---- delete ---------------------------------------------------
    for name in spec.get("delete", []) or []:
        name = str(name or "").strip()

        item = find_item(data, name) if name else None

        if not item:
            out["delete"].append({
                "name": name,
                "ok": False,
                "error": "not found"
            })
            continue

        data["configs"] = [
            x for x in data["configs"]
            if x["slug"] != item["slug"]
        ]

        for path in (
            f'{CONFIG_DIR}/{item["slug"]}.json',
            f'{BASE}/health-state/{item["slug"]}.json',
            f'{BASE}/speed-state/{item["slug"]}.json',
            f'{BASE}/logs/{item["slug"]}.log',
        ):
            try:
                if os.path.exists(path):
                    os.remove(path)
            except Exception:
                pass

        out["delete"].append({
            "name": item["name"],
            "ok": True
        })

        changed = True

    # ---- update (URI only) ---------------------------------------
    for req in spec.get("update", []) or []:
        name = str(req.get("name", "")).strip()
        uri = str(req.get("uri", "")).strip()

        item = find_item(data, name) if name else None

        if not item:
            out["update"].append({
                "name": name,
                "ok": False,
                "error": "not found"
            })
            continue

        if not (
            uri.startswith("vless://")
            or uri.startswith(("vmess://", "trojan://", "ss://"))
        ):
            out["update"].append({
                "name": item["name"],
                "ok": False,
                "error": "only VLESS / VMess / Trojan / Shadowsocks configs are supported"
            })
            continue

        if uri == item.get("uri"):
            out["update"].append({
                "name": item["name"],
                "ok": True,
                "changed": False
            })
            continue

        candidate = dict(item)
        candidate["uri"] = uri

        try:
            write_xray_config(candidate)

        except Exception as e:
            out["update"].append({
                "name": item["name"],
                "ok": False,
                "error": "Xray rejected configuration: "
                         + str(e)[-300:]
            })
            continue

        item["uri"] = uri

        out["update"].append({
            "name": item["name"],
            "ok": True,
            "changed": True,
            "port": item["port"]
        })

        changed = True

    # ---- add -------------------------------------------------------
    for req in spec.get("add", []) or []:
        uri = str(req.get("uri", "")).strip()

        name = (
            str(req.get("name", "")).strip()
            or name_from_uri(uri)
        )

        def fail(error):
            out["add"].append({
                "name": name,
                "ok": False,
                "error": error
            })

        if not (
            uri.startswith("vless://")
            or uri.startswith(("vmess://", "trojan://", "ss://"))
        ):
            fail("only VLESS / VMess / Trojan / Shadowsocks configs are supported")
            continue

        if not name:
            fail("name cannot be empty")
            continue

        if len(name) > 120:
            fail("config name is too long")
            continue

        slug = slugify(name)

        if not slug:
            fail("invalid name")
            continue

        if any(
            x["slug"] == slug
            or str(x["name"]).lower() == name.lower()
            for x in data["configs"]
        ):
            fail(f"config '{name}' already exists")
            continue

        item = {
            "name": name,
            "slug": slug,
            "uri": uri,
            "push_url": str(
                req.get("push_url", "")
            ).split("?")[0].strip(),
            "port": next_port(data),
            "enabled": True
        }

        try:
            write_xray_config(item)

        except Exception as e:
            fail(
                "Xray rejected configuration: "
                + str(e)[-300:]
            )
            continue

        data["configs"].append(item)

        out["add"].append({
            "name": name,
            "slug": slug,
            "port": item["port"],
            "ok": True
        })

        changed = True

    restart_error = ""
    not_ready = []

    if changed:
        # run-all.py notices the new configs.json and starts only
        # the probes that are new / changed. No service restart.
        save_db(data)

        ports = [
            x["port"] for x in out["add"] if x.get("ok")
        ]

        not_ready = wait_for_probes(ports)

    return {
        "results": out,
        "restarted": False,
        "restart_error": restart_error,
        "not_ready_ports": not_ready
    }


def usage():
    print("""
Nikatel Probe Manager

Commands:

  nikatel-probe list
  nikatel-probe status

  nikatel-probe add
  nikatel-probe add 'VLESS_URL' 'PUSH_URL'

  nikatel-probe test Germany\n\n  nikatel-probe speed Germany

  nikatel-probe enable Germany
  nikatel-probe disable Germany

  nikatel-probe remove Germany

  nikatel-probe restart
""")


def main():
    if len(sys.argv) < 2:
        usage()
        return 0

    command = sys.argv[1].lower()

    try:
        if command == "add":
            return add_config() or 0

        elif command == "list":
            list_configs()
            return 0

        elif command == "status":
            status()
            return 0

        elif command == "test":
            if len(sys.argv) < 3:
                print(
                    "Usage: nikatel-probe test NAME"
                )
                return 1

            return test_config(
                " ".join(sys.argv[2:])
            )

        elif command == "enable":
            if len(sys.argv) < 3:
                print(
                    "Usage: nikatel-probe enable NAME"
                )
                return 1

            return set_enabled(
                " ".join(sys.argv[2:]),
                True
            )

        elif command == "disable":
            if len(sys.argv) < 3:
                print(
                    "Usage: nikatel-probe disable NAME"
                )
                return 1

            return set_enabled(
                " ".join(sys.argv[2:]),
                False
            )

        elif command == "remove":
            if len(sys.argv) < 3:
                print(
                    "Usage: nikatel-probe remove NAME"
                )
                return 1

            return remove_config(
                " ".join(sys.argv[2:])
            )

        elif command == "speed":
            if len(sys.argv) < 3:
                print("Usage: nikatel-probe speed NAME")
                return

            speed_config(" ".join(sys.argv[2:]))

        elif command == "restart":
            restart_services()
            print("Manager and monitor restarted")
            return 0

        else:
            usage()
            return 1

    except KeyboardInterrupt:
        print("\nCancelled")
        return 130

    except Exception as e:
        print(f"ERROR: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())

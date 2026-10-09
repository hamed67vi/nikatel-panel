#!/usr/bin/env python3

import gzip
import json
import os
import subprocess
import threading
import time
import socket
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

BASE = "/opt/nikatel-probe-manager"
DB = f"{BASE}/configs.json"
STATE_DIR = f"{BASE}/speed-state"
TOKEN_FILE = f"{BASE}/speed-api.token"

lock = threading.Lock()


def load_token():
    with open(TOKEN_FILE) as f:
        return f.read().strip()


def load_db():
    with open(DB) as f:
        return json.load(f)


def find_config(name):
    data = load_db()

    for item in data.get("configs", []):
        if (
            item.get("name", "").lower() == name.lower()
            or item.get("slug", "").lower() == name.lower()
        ):
            return item

    return None


def read_state(slug):
    path = f"{STATE_DIR}/{slug}.json"

    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return None


def live_health(item):
    """Quick real connectivity test through the config SOCKS proxy."""
    port = item["port"]

    started = time.monotonic()

    try:
        result = subprocess.run(
            [
                "/usr/bin/curl",
                "--socks5-hostname", f"127.0.0.1:{port}",
                "--connect-timeout", "3",
                "--max-time", "5",
                "-o", "/dev/null",
                "-sS",
                "-w", "%{http_code}",
                "https://www.gstatic.com/generate_204"
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=7
        )

        ping = round((time.monotonic() - started) * 1000)

        if result.returncode == 0 and result.stdout.strip() == "204":
            return {
                "status": "up",
                "ping": ping,
                "checked_at": int(time.time())
            }

        return {
            "status": "down",
            "ping": None,
            "checked_at": int(time.time()),
            "error": (result.stderr.strip() or "connectivity test failed")[:160]
        }

    except Exception as e:
        return {
            "status": "down",
            "ping": None,
            "checked_at": int(time.time()),
            "error": str(e)[:160]
        }


def configs_with_health():
    data = load_db()

    items = [
        item for item in data.get("configs", [])
        if item.get("enabled", True)
    ]

    health = {}

    # All configs tested concurrently
    with ThreadPoolExecutor(max_workers=min(16, len(items) or 1)) as pool:
        jobs = {
            pool.submit(live_health, item): item["slug"]
            for item in items
        }

        for future, slug in [(f, s) for f, s in jobs.items()]:
            try:
                health[slug] = future.result()
            except Exception as e:
                health[slug] = {
                    "status": "down",
                    "ping": None,
                    "checked_at": int(time.time()),
                    "error": str(e)[:160]
                }

    result = []

    for item in items:
        h = health.get(item["slug"], {})

        result.append({
            "name": item["name"],
            "slug": item["slug"],
            "port": item["port"],
            "push_url": item.get("push_url", ""),
            "status": h.get("status", "unknown"),
            "ping": h.get("ping"),
            "checked_at": h.get("checked_at"),
            "error": h.get("error"),
            "speed": read_state(item["slug"])
        })

    return result


class Handler(BaseHTTPRequestHandler):

    def send_json(self, code, data):
        body = json.dumps(data).encode()

        # Big answers are compressed when the client accepts gzip:
        # the network path to the panel loses large plain responses.
        gz = (
            len(body) > 600
            and "gzip" in self.headers.get("Accept-Encoding", "")
        )

        if gz:
            body = gzip.compress(body, 6)

        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        if gz:
            self.send_header("Content-Encoding", "gzip")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()

        self.wfile.write(body)

    def _body(self, length):
        """Request body; gzip is accepted (small requests survive a path that drops big ones)."""
        raw = self.rfile.read(length)

        if self.headers.get("Content-Encoding", "") == "gzip":
            raw = gzip.decompress(raw)

        return raw

    def authorized(self):
        auth = self.headers.get("Authorization", "")
        return auth == f"Bearer {load_token()}"

    def do_GET(self):
        if not self.authorized():
            return self.send_json(401, {
                "ok": False,
                "error": "unauthorized"
            })

        parsed = urlparse(self.path)

        # Plain list of the configs: no live health test, answers at once.
        if parsed.path == "/api/config/list":
            data = load_db()

            return self.send_json(200, {
                "ok": True,
                "gzip_in": True,
                "configs": [
                    {
                        "name": x["name"],
                        "slug": x["slug"],
                        "port": x["port"],
                        "push_url": x.get("push_url", ""),
                        "enabled": x.get("enabled", True)
                    }
                    for x in data.get("configs", [])
                ]
            })

        # The stored link of one config (the panel opens it in a v2rayN-style editor).
        if parsed.path == "/api/config/uri":
            _q = parse_qs(parsed.query)
            _n = (_q.get("name") or [""])[0].strip().lower()

            for _x in load_db().get("configs", []):
                if _n and _n in (str(_x.get("name", "")).lower(), str(_x.get("slug", "")).lower()):
                    return self.send_json(200, {
                        "ok": True,
                        "name": _x.get("name"),
                        "slug": _x.get("slug"),
                        "uri": _x.get("uri", ""),
                        "port": _x.get("port"),
                        "enabled": _x.get("enabled", True)
                    })

            return self.send_json(404, {"ok": False, "error": "config not found"})

        if parsed.path == "/api/configs":
            result = configs_with_health()

            up = sum(1 for x in result if x["status"] == "up")
            down = sum(1 for x in result if x["status"] == "down")

            pings = [
                x["ping"] for x in result
                if x["status"] == "up" and x["ping"] is not None
            ]

            return self.send_json(200, {
                "ok": True,
                "summary": {
                    "total": len(result),
                    "up": up,
                    "down": down,
                    "avg_ping": round(sum(pings) / len(pings)) if pings else None
                },
                "configs": result
            })

        if parsed.path == "/api/speed":
            qs = parse_qs(parsed.query)
            name = qs.get("name", [""])[0]

            item = find_config(name)

            if not item:
                return self.send_json(404, {
                    "ok": False,
                    "error": "config not found"
                })

            state = read_state(item["slug"])

            return self.send_json(200, {
                "ok": True,
                "name": item["name"],
                "speed": state
            })

        return self.send_json(404, {
            "ok": False,
            "error": "not found"
        })

    def do_POST(self):
        if not self.authorized():
            return self.send_json(401, {
                "ok": False,
                "error": "unauthorized"
            })

        parsed = urlparse(self.path)

        # Add a new VLESS/VMess config
        if parsed.path == "/api/config/add":
            try:
                length = int(self.headers.get("Content-Length", "0"))
                raw = self._body(length)

                body = json.loads(raw.decode() or "{}")

                uri = str(body.get("uri", "")).strip()
                push_url = str(body.get("push_url", "")).strip()

                if not uri:
                    return self.send_json(400, {
                        "ok": False,
                        "error": "config URL is required"
                    })

                if not (
                    uri.startswith("vless://")
                    or uri.startswith(("vmess://", "trojan://", "ss://"))
                ):
                    return self.send_json(400, {
                        "ok": False,
                        "error": "only VLESS / VMess / Trojan / Shadowsocks configs are supported"
                    })

                result = subprocess.run(
                    [
                        "/usr/local/bin/nikatel-probe",
                        "add",
                        uri,
                        push_url
                    ],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    timeout=60
                )

                output = result.stdout.strip()

                if result.returncode != 0:
                    return self.send_json(400, {
                        "ok": False,
                        "error": "failed to add config",
                        "output": output[-3000:]
                    })

                return self.send_json(200, {
                    "ok": True,
                    "message": "config added successfully",
                    "output": output
                })

            except subprocess.TimeoutExpired:
                return self.send_json(504, {
                    "ok": False,
                    "error": "add config timeout"
                })

            except Exception as e:
                return self.send_json(500, {
                    "ok": False,
                    "error": str(e)
                })

        # Several changes (add / update URI / delete) with ONE restart
        # of the probe services.
        if parsed.path == "/api/config/bulk":
            try:
                length = int(
                    self.headers.get("Content-Length", "0")
                )

                raw = self._body(length)

                spec = json.loads(raw.decode() or "{}")

                clean = {}

                for key in ("add", "update", "delete"):
                    value = spec.get(key, [])

                    if not isinstance(value, list):
                        return self.send_json(400, {
                            "ok": False,
                            "error": f"'{key}' must be a list"
                        })

                    clean[key] = value

                # Same lock as /api/config/push: configs.json is
                # never written by two requests at once.
                with lock:
                    result = subprocess.run(
                        [
                            "/usr/bin/python3",
                            "-c",
                            (
                                "import sys,json;"
                                "sys.path.insert(0,"
                                "'/opt/nikatel-probe-manager');"
                                "import manager;"
                                "print(json.dumps(manager.bulk_apply("
                                "json.loads(sys.stdin.read()))))"
                            )
                        ],
                        input=json.dumps(clean),
                        stdout=subprocess.PIPE,
                        stderr=subprocess.STDOUT,
                        text=True,
                        timeout=300
                    )

                output = result.stdout.strip()

                if result.returncode != 0 or not output:
                    return self.send_json(500, {
                        "ok": False,
                        "error": "bulk operation failed",
                        "output": output[-3000:]
                    })

                data = json.loads(output.splitlines()[-1])

                data["ok"] = True

                return self.send_json(200, data)

            except subprocess.TimeoutExpired:
                return self.send_json(504, {
                    "ok": False,
                    "error": "bulk operation timeout"
                })

            except Exception as e:
                return self.send_json(500, {
                    "ok": False,
                    "error": str(e)
                })

        # Update an existing config without changing
        # its slug, SOCKS port or Kuma Push URL.
        if parsed.path == "/api/config/update":
            try:
                length = int(
                    self.headers.get(
                        "Content-Length",
                        "0"
                    )
                )

                raw = self._body(length)
                body = json.loads(
                    raw.decode() or "{}"
                )

                name = str(
                    body.get("name", "")
                    or body.get("slug", "")
                ).strip()

                uri = str(
                    body.get("uri", "")
                ).strip()

                new_name = str(
                    body.get("new_name", "")
                ).strip()

                if not name:
                    return self.send_json(400, {
                        "ok": False,
                        "error":
                            "config name or slug is required"
                    })

                if not uri and not new_name:
                    return self.send_json(400, {
                        "ok": False,
                        "error":
                            "uri or new_name is required"
                    })

                if uri and not (
                    uri.startswith("vless://")
                    or uri.startswith(("vmess://", "trojan://", "ss://"))
                ):
                    return self.send_json(400, {
                        "ok": False,
                        "error":
                            "only VLESS / VMess / Trojan / Shadowsocks configs are supported"
                    })

                with lock:
                    result = subprocess.run(
                        [
                            "/usr/bin/python3",
                            "-c",
                            (
                                "import sys;"
                                "sys.path.insert(0,"
                                "'/opt/nikatel-probe-manager');"
                                "import manager;"
                                "sys.exit("
                                "manager.update_config_force("
                                "sys.argv[1],"
                                "sys.argv[2],"
                                "sys.argv[3]"
                                "))"
                            ),
                            name,
                            uri,
                            new_name
                        ],
                        stdout=subprocess.PIPE,
                        stderr=subprocess.STDOUT,
                        text=True,
                        timeout=90
                    )

                output = result.stdout.strip()

                if result.returncode != 0:
                    return self.send_json(400, {
                        "ok": False,
                        "error":
                            "failed to update config",
                        "output": output[-3000:]
                    })

                lookup = new_name or name

                updated = find_config(lookup)

                if not updated:
                    updated = find_config(name)

                response = {
                    "ok": True,
                    "message":
                        "config updated successfully",
                    "output": output
                }

                if updated:
                    response.update({
                        "name": updated.get("name"),
                        "slug": updated.get("slug"),
                        "port": updated.get("port"),
                        "enabled":
                            updated.get(
                                "enabled",
                                True
                            )
                    })

                return self.send_json(
                    200,
                    response
                )

            except subprocess.TimeoutExpired:
                return self.send_json(504, {
                    "ok": False,
                    "error":
                        "update config timeout"
                })

            except Exception as e:
                return self.send_json(500, {
                    "ok": False,
                    "error": str(e)
                })


        # Delete an existing config
        if parsed.path == "/api/config/delete":
            try:
                length = int(
                    self.headers.get("Content-Length", "0")
                )

                raw = self._body(length)
                body = json.loads(raw.decode() or "{}")

                name = str(
                    body.get("name", "")
                    or body.get("slug", "")
                ).strip()

                if not name:
                    return self.send_json(400, {
                        "ok": False,
                        "error": "config name or slug is required"
                    })

                result = subprocess.run(
                    [
                        "/usr/bin/python3",
                        "-c",
                        (
                            "import sys;"
                            "sys.path.insert(0,"
                            "'/opt/nikatel-probe-manager');"
                            "import manager;"
                            "sys.exit(manager.remove_config_force(sys.argv[1]))"
                        ),
                        name
                    ],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    timeout=60
                )

                output = result.stdout.strip()

                if result.returncode != 0:
                    return self.send_json(400, {
                        "ok": False,
                        "error": "failed to delete config",
                        "output": output[-3000:]
                    })

                return self.send_json(200, {
                    "ok": True,
                    "name": name,
                    "message": "config deleted successfully",
                    "output": output
                })

            except subprocess.TimeoutExpired:
                return self.send_json(504, {
                    "ok": False,
                    "error": "delete config timeout"
                })

            except Exception as e:
                return self.send_json(500, {
                    "ok": False,
                    "error": str(e)
                })


        # Update Kuma Push URL for an existing config
        if parsed.path == "/api/config/push":
            try:
                length = int(
                    self.headers.get("Content-Length", "0")
                )

                raw = self._body(length)
                body = json.loads(raw.decode() or "{}")

                name = str(
                    body.get("name", "")
                    or body.get("slug", "")
                ).strip()

                push_url = str(
                    body.get("push_url", "")
                ).strip()

                if not name:
                    return self.send_json(400, {
                        "ok": False,
                        "error": "config name or slug is required"
                    })

                if not push_url:
                    return self.send_json(400, {
                        "ok": False,
                        "error": "push_url is required"
                    })

                # Store only the base Kuma push URL
                push_url = push_url.split("?")[0]

                with lock:
                    data = load_db()

                    item = None

                    for x in data.get("configs", []):
                        if (
                            x.get("name", "").lower() == name.lower()
                            or x.get("slug", "").lower() == name.lower()
                        ):
                            item = x
                            break

                    if not item:
                        return self.send_json(404, {
                            "ok": False,
                            "error": "config not found"
                        })

                    item["push_url"] = push_url

                    tmp = DB + ".tmp"

                    with open(tmp, "w") as f:
                        json.dump(
                            data,
                            f,
                            indent=2,
                            ensure_ascii=False
                        )

                    os.replace(tmp, DB)

                return self.send_json(200, {
                    "ok": True,
                    "name": item["name"],
                    "slug": item["slug"],
                    "push_url": push_url,
                    "message": "push URL updated successfully"
                })

            except Exception as e:
                return self.send_json(500, {
                    "ok": False,
                    "error": str(e)
                })

        # Check direct TCP reachability from Iran probe
        if parsed.path == "/api/filter/check":
            try:
                length = int(
                    self.headers.get("Content-Length", "0")
                )

                raw = self._body(length)
                body = json.loads(raw.decode() or "{}")

                host = str(
                    body.get("host", "")
                ).strip()

                try:
                    port = int(body.get("port", 0))
                except Exception:
                    port = 0

                if not host:
                    return self.send_json(400, {
                        "ok": False,
                        "error": "host is required"
                    })

                if port < 1 or port > 65535:
                    return self.send_json(400, {
                        "ok": False,
                        "error": "valid port is required"
                    })

                timeout = 5.0
                started = time.monotonic()

                try:
                    sock = socket.create_connection(
                        (host, port),
                        timeout=timeout
                    )

                    latency_ms = round(
                        (time.monotonic() - started) * 1000
                    )

                    try:
                        peer = sock.getpeername()[0]
                    except Exception:
                        peer = None

                    sock.close()

                    return self.send_json(200, {
                        "ok": True,
                        "reachable": True,
                        "host": host,
                        "port": port,
                        "latency_ms": latency_ms,
                        "resolved_ip": peer,
                        "checked_at": int(time.time())
                    })

                except socket.timeout:
                    return self.send_json(200, {
                        "ok": True,
                        "reachable": False,
                        "host": host,
                        "port": port,
                        "latency_ms": None,
                        "reason": "timeout",
                        "checked_at": int(time.time())
                    })

                except ConnectionRefusedError:
                    return self.send_json(200, {
                        "ok": True,
                        "reachable": False,
                        "host": host,
                        "port": port,
                        "latency_ms": None,
                        "reason": "connection refused",
                        "checked_at": int(time.time())
                    })

                except OSError as e:
                    return self.send_json(200, {
                        "ok": True,
                        "reachable": False,
                        "host": host,
                        "port": port,
                        "latency_ms": None,
                        "reason": str(e)[:200],
                        "checked_at": int(time.time())
                    })

            except Exception as e:
                return self.send_json(500, {
                    "ok": False,
                    "error": str(e)
                })



        # Tunnel path quality test from Iran probe
        if parsed.path == "/api/tunnel/test":
            try:
                length = int(
                    self.headers.get("Content-Length", "0")
                )

                raw = self._body(length)
                body = json.loads(raw.decode() or "{}")

                host = str(
                    body.get("host", "")
                ).strip()

                try:
                    count = int(body.get("count", 12))
                except Exception:
                    count = 12

                count = max(3, min(count, 50))

                if not host:
                    return self.send_json(400, {
                        "ok": False,
                        "error": "host is required"
                    })

                # Prevent ping option injection
                if host.startswith("-"):
                    return self.send_json(400, {
                        "ok": False,
                        "error": "invalid host"
                    })

                started = time.monotonic()

                result = subprocess.run(
                    [
                        "/usr/bin/ping",
                        "-n",
                        "-c", str(count),
                        "-i", "0.20",
                        "-W", "2",
                        host
                    ],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    timeout=max(15, count * 3)
                )

                elapsed_ms = round(
                    (time.monotonic() - started) * 1000
                )

                output = result.stdout or ""
                error_output = result.stderr or ""

                import re

                sent = None
                received = None
                loss_pct = None

                packet_match = re.search(
                    r'(\d+)\s+packets transmitted,\s+'
                    r'(\d+)\s+(?:packets )?received,.*?'
                    r'([\d.]+)%\s+packet loss',
                    output
                )

                if packet_match:
                    sent = int(packet_match.group(1))
                    received = int(packet_match.group(2))
                    loss_pct = float(packet_match.group(3))

                min_ms = None
                avg_ms = None
                max_ms = None
                jitter_ms = None

                rtt_match = re.search(
                    r'(?:rtt|round-trip) min/avg/max/(?:mdev|stddev)'
                    r'\s*=\s*'
                    r'([\d.]+)/([\d.]+)/([\d.]+)/([\d.]+)\s*ms',
                    output
                )

                if rtt_match:
                    min_ms = round(float(rtt_match.group(1)), 2)
                    avg_ms = round(float(rtt_match.group(2)), 2)
                    max_ms = round(float(rtt_match.group(3)), 2)
                    jitter_ms = round(float(rtt_match.group(4)), 2)

                usable = (
                    received is not None
                    and received > 0
                    and avg_ms is not None
                )

                return self.send_json(200, {
                    "ok": True,
                    "host": host,
                    "usable": usable,
                    "sent": sent,
                    "received": received,
                    "loss_pct": loss_pct,
                    "min_ms": min_ms,
                    "avg_ms": avg_ms,
                    "max_ms": max_ms,
                    "jitter_ms": jitter_ms,
                    "elapsed_ms": elapsed_ms,
                    "checked_at": int(time.time()),
                    "error": (
                        error_output.strip()[:300]
                        if error_output.strip()
                        else None
                    )
                })

            except subprocess.TimeoutExpired:
                return self.send_json(504, {
                    "ok": False,
                    "error": "tunnel test timeout"
                })

            except Exception as e:
                return self.send_json(500, {
                    "ok": False,
                    "error": str(e)
                })


        if parsed.path != "/api/speed":
            return self.send_json(404, {
                "ok": False,
                "error": "not found"
            })

        qs = parse_qs(parsed.query)
        name = qs.get("name", [""])[0]

        item = find_config(name)

        if not item:
            return self.send_json(404, {
                "ok": False,
                "error": "config not found"
            })

        if not lock.acquire(blocking=False):
            return self.send_json(409, {
                "ok": False,
                "error": "another speed test is running"
            })

        try:
            result = subprocess.run(
                [
                    "/usr/local/bin/nikatel-probe",
                    "speed",
                    item["name"]
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                timeout=180
            )

            state = read_state(item["slug"])

            if result.returncode != 0:
                return self.send_json(500, {
                    "ok": False,
                    "error": "speed test failed",
                    "output": result.stdout[-2000:]
                })

            return self.send_json(200, {
                "ok": True,
                "name": item["name"],
                "speed": state,
                "output": result.stdout
            })

        except subprocess.TimeoutExpired:
            return self.send_json(504, {
                "ok": False,
                "error": "speed test timeout"
            })

        finally:
            lock.release()

    def log_message(self, fmt, *args):
        print(
            f"{self.client_address[0]} "
            f"{fmt % args}",
            flush=True
        )


server = ThreadingHTTPServer(
    ("0.0.0.0", 18080),
    Handler
)

print("Nikatel Speed API listening on :18080", flush=True)
server.serve_forever()

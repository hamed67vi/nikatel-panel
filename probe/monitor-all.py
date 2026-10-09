#!/usr/bin/env python3

import json
import os
import subprocess
import time
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed

BASE = "/opt/nikatel-probe-manager"
DB = f"{BASE}/configs.json"
SPEED_STATE_DIR = f"{BASE}/speed-state"
HEALTH_STATE_DIR = f"{BASE}/health-state"

FAIL_THRESHOLD = 3
RETRY_DELAY = 5

os.makedirs(HEALTH_STATE_DIR, exist_ok=True)


def load_db():
    with open(DB) as f:
        return json.load(f)


def get_speed_message(item):
    slug = item.get("slug")

    if not slug:
        return "OK"

    path = f"{SPEED_STATE_DIR}/{slug}.json"

    try:
        with open(path) as f:
            state = json.load(f)

        if not state.get("ok"):
            return "OK | Speed: FAILED"

        download = float(state.get("download", 0))
        upload = float(state.get("upload", 0))

        return (
            f"OK | DL {download:.2f} Mbps "
            f"| UL {upload:.2f} Mbps"
        )

    except FileNotFoundError:
        return "OK | Speed: not tested"

    except Exception:
        return "OK | Speed: unavailable"


def state_path(item):
    return f'{HEALTH_STATE_DIR}/{item["slug"]}.json'


def load_health_state(item):
    default = {
        "fail_count": 0,
        "status": "unknown",
        "last_error": "",
        "last_ping": 0
    }

    try:
        with open(state_path(item)) as f:
            data = json.load(f)

        default.update(data)

    except Exception:
        pass

    return default


def save_health_state(item, state):
    path = state_path(item)
    tmp = path + ".tmp"

    with open(tmp, "w") as f:
        json.dump(state, f, indent=2)

    os.replace(tmp, path)


def push_result(push_url, status, msg, ping):
    params = urllib.parse.urlencode({
        "status": status,
        "msg": msg,
        "ping": ping
    })

    url = push_url.split("?")[0] + "?" + params

    try:
        with urllib.request.urlopen(url, timeout=10) as r:
            return r.status == 200

    except Exception as e:
        print(f"PUSH ERROR: {e}", flush=True)
        return False


def single_test(item, timeout, test_url):
    port = item["port"]

    cmd = [
        "/usr/bin/curl",
        "--socks5-hostname",
        f"127.0.0.1:{port}",
        "--max-time",
        str(timeout),
        "-o",
        "/dev/null",
        "-sS",
        "-w",
        "%{http_code} %{time_total}",
        test_url
    ]

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout + 2
        )

        output = result.stdout.strip().split()

        if (
            result.returncode == 0
            and len(output) >= 2
            and output[0] == "204"
        ):
            ping = round(float(output[1]) * 1000)
            return True, ping, ""

        error = result.stderr.strip()

        if not error:
            if output:
                error = f"HTTP_{output[0]}"
            else:
                error = "HTTP_FAILED"

        return False, 0, error

    except Exception as e:
        return False, 0, str(e)


def test_config(item, timeout, test_url):
    name = item["name"]
    push_url = item.get("push_url")

    if not push_url:
        return f"SKIP {name}: no push_url"

    state = load_health_state(item)

    # First attempt
    up, ping, error = single_test(
        item,
        timeout,
        test_url
    )

    if up:
        previous_status = state.get("status", "unknown")

        state["fail_count"] = 0
        state["status"] = "up"
        state["last_error"] = ""
        state["last_ping"] = ping
        state["last_success"] = time.time()

        save_health_state(item, state)

        msg = get_speed_message(item)

        pushed = push_result(
            push_url,
            "up",
            msg,
            ping
        )

        if previous_status == "down":
            label = "RECOVER"
        else:
            label = "UP"

        return (
            f"{label:<7}{name:<16} "
            f"{ping:>5}ms "
            f"push={'OK' if pushed else 'FAIL'}"
        )

    # First test failed. Retry before counting a real failed cycle.
    errors = [error]

    for attempt in range(2, FAIL_THRESHOLD + 1):
        time.sleep(RETRY_DELAY)

        retry_up, retry_ping, retry_error = single_test(
            item,
            timeout,
            test_url
        )

        if retry_up:
            state["fail_count"] = 0
            state["status"] = "up"
            state["last_error"] = ""
            state["last_ping"] = retry_ping
            state["last_success"] = time.time()

            save_health_state(item, state)

            msg = get_speed_message(item)

            pushed = push_result(
                push_url,
                "up",
                msg,
                retry_ping
            )

            return (
                f"RETRY-OK {name:<16} "
                f"{retry_ping:>5}ms "
                f"attempt={attempt} "
                f"push={'OK' if pushed else 'FAIL'}"
            )

        errors.append(retry_error)

    # All three attempts failed.
    state["fail_count"] = int(state.get("fail_count", 0)) + 1
    state["last_error"] = errors[-1][:300]
    state["last_failure"] = time.time()

    previous_status = state.get("status", "unknown")

    # One complete 3-attempt cycle is enough to mark DOWN.
    state["status"] = "down"
    save_health_state(item, state)

    error_msg = errors[-1][:100]

    pushed = push_result(
        push_url,
        "down",
        error_msg,
        0
    )

    return (
        f"DOWN   {name:<16} "
        f"3/3 failed "
        f"{error_msg[:45]} "
        f"push={'OK' if pushed else 'FAIL'}"
    )


def main():
    while True:
        started = time.time()

        try:
            db = load_db()

            settings = db.get("settings", {})

            interval = int(
                settings.get("check_interval", 30)
            )

            timeout = int(
                settings.get("timeout", 10)
            )

            test_url = settings.get(
                "test_url",
                "https://www.gstatic.com/generate_204"
            )

            configs = [
                x
                for x in db.get("configs", [])
                if x.get("enabled", True)
            ]

            print(
                f"\n=== CHECK {time.strftime('%F %T')} "
                f"configs={len(configs)} "
                f"retry={FAIL_THRESHOLD}x ===",
                flush=True
            )

            with ThreadPoolExecutor(
                max_workers=min(20, len(configs) or 1)
            ) as pool:

                futures = [
                    pool.submit(
                        test_config,
                        item,
                        timeout,
                        test_url
                    )
                    for item in configs
                ]

                for future in as_completed(futures):
                    try:
                        print(
                            future.result(),
                            flush=True
                        )
                    except Exception as e:
                        print(
                            f"WORKER ERROR: {e}",
                            flush=True
                        )

        except Exception as e:
            print(
                f"MANAGER ERROR: {e}",
                flush=True
            )
            interval = 30

        elapsed = time.time() - started
        sleep_for = max(1, interval - elapsed)

        time.sleep(sleep_for)


if __name__ == "__main__":
    main()

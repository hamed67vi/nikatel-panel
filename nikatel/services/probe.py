"""The probe: a server inside Iran that keeps one xray per config and tests them. The panel talks to its
HTTP API (bearer token). Several probes may be configured; the first that answers is used, and the
switch is remembered so the panel keeps working when the primary is cut off."""
from __future__ import annotations

import gzip
import json
import re
import time
from pathlib import Path

import httpx

from ..log import get_logger
from . import settings as st

log = get_logger("probe")
_STATE = {"active": None, "fails": 0, "since": 0}


def bootstrap() -> None:
    """First start: take the probe address + token from /etc/nikatel/probe.json when the settings are empty."""
    cur = st.get("probe")

    if cur.get("api_url"):
        return

    from ..config import get_settings

    p = Path(get_settings().bootstrap_dir) / "probe.json"

    if p.exists():
        try:
            d = json.loads(p.read_text())
            st.put("probe", {"api_url": str(d.get("api_url", "")).rstrip("/"), "api_token": str(d.get("api_token", ""))})
            log.info("probe settings taken from %s", p)
        except (OSError, ValueError) as e:
            log.warning("could not read %s: %s", p, e)


def candidates() -> list[dict]:
    c = st.get("probe")
    out = []

    if c.get("api_url") and c.get("api_token"):
        out.append({"name": "primary", "api_url": c["api_url"].rstrip("/"), "api_token": c["api_token"]})

    for x in c.get("standby") or []:
        if x.get("api_url") and x.get("api_token"):
            out.append({"name": x.get("name") or "standby", "api_url": x["api_url"].rstrip("/"), "api_token": x["api_token"]})

    return out


def _call(p: dict, method: str, path: str, body: dict | None = None, timeout: float = 30) -> dict:
    headers = {"Authorization": "Bearer " + p["api_token"], "Accept": "application/json", "Accept-Encoding": "gzip"}
    r = httpx.request(method, p["api_url"] + path, json=body, headers=headers, timeout=timeout)
    raw = r.content

    if r.headers.get("content-encoding") == "gzip" and raw[:2] == b"\x1f\x8b":
        raw = gzip.decompress(raw)

    try:
        d = json.loads(raw.decode("utf-8", "replace") or "{}")
    except ValueError:
        raise RuntimeError(f"the probe answered with something that is not JSON (HTTP {r.status_code})")

    if r.status_code >= 400 and not d.get("error"):
        d["error"] = f"HTTP {r.status_code}"

    return d


def request(method: str, path: str, body: dict | None = None, timeout: float = 30) -> dict:
    """Try the active probe first, then the others. Raises RuntimeError when none answers."""
    cands = candidates()

    if not cands:
        raise RuntimeError("no probe configured (Settings → Probe)")

    order = sorted(cands, key=lambda c: 0 if c["name"] == _STATE["active"] else 1)
    last = ""

    for c in order:
        try:
            d = _call(c, method, path, body, timeout)
        except (httpx.HTTPError, RuntimeError) as e:
            last = f"{c['name']}: {str(e)[:80]}"
            continue

        if _STATE["active"] != c["name"]:
            if _STATE["active"] is not None:
                log.warning("switched to probe %s", c["name"])

            _STATE.update(active=c["name"], since=int(time.time()))

        _STATE["fails"] = 0
        return d

    _STATE["fails"] += 1
    raise RuntimeError("no probe answers (" + last + ")")


def active() -> dict:
    return {"active": _STATE["active"], "fails": _STATE["fails"], "since": _STATE["since"], "probes": [c["name"] for c in candidates()]}


# ---- the probe's API, named
def list_configs() -> list[dict]:
    d = request("GET", "/api/config/list", timeout=30)
    return d.get("configs") or []


def health() -> dict:
    """Every config with its live state - the probe tests them all, this takes a while."""
    return request("GET", "/api/configs", timeout=120)


def config_uri(name: str) -> str:
    d = request("GET", "/api/config/uri?name=" + httpx.QueryParams({"n": name})["n"].replace("+", "%20"), timeout=20)

    if not d.get("ok"):
        raise RuntimeError(d.get("error") or "no link")

    return d.get("uri", "")


def add_config(uri: str, push_url: str = "") -> dict:
    d = request("POST", "/api/config/add", {"uri": uri, "push_url": push_url}, timeout=90)

    if not d.get("ok"):
        raise RuntimeError(str(d.get("error") or d.get("output") or "the probe refused the config")[:300])

    return d


def add_many(items: list[dict]) -> dict:
    """Several configs in ONE call (the probe restarts its services once for all, not once per config). items: [{"uri", "name"}].
    Returns {name: None when added | error text}."""
    d = request("POST", "/api/config/bulk", {"add": [{"uri": i["uri"], "name": i["name"]} for i in items]}, timeout=300)

    if not d.get("ok"):
        raise RuntimeError(str(d.get("error") or d.get("output") or "the probe refused the bulk add")[:300])

    res = {i["name"]: "no answer for this one" for i in items}

    for it in (d.get("results") or d).get("add", []):
        res[str(it.get("name", ""))] = None if it.get("ok") else str(it.get("error", "failed"))[:100]

    return res

def update_config(name: str, uri: str = "", new_name: str = "") -> dict:
    body = {"name": name}

    if uri:
        body["uri"] = uri

    if new_name and new_name != name:
        body["new_name"] = new_name

    d = request("POST", "/api/config/update", body, timeout=90)

    if not d.get("ok"):
        raise RuntimeError(str(d.get("error") or d.get("output") or "the probe refused the change")[:300])

    return d


def delete_config(name: str) -> dict:
    d = request("POST", "/api/config/delete", {"name": name}, timeout=90)

    if not d.get("ok"):
        raise RuntimeError(str(d.get("error") or d.get("output") or "the probe refused to delete")[:300])

    return d


def delete_many(names: list[str]) -> dict:
    """Several configs in ONE call: the probe restarts its services once for all of them (one call per config took ~3 s each).
    Returns {name: None when gone | error text}. A name the probe does not know counts as gone."""
    d = request("POST", "/api/config/bulk", {"delete": list(names)}, timeout=300)

    if not d.get("ok"):
        raise RuntimeError(str(d.get("error") or d.get("output") or "the probe refused the bulk delete")[:300])

    res = {n: "no answer for this one" for n in names}

    for it in (d.get("results") or d).get("delete", []):
        n = str(it.get("name", ""))
        res[n] = None if it.get("ok") or it.get("error") == "not found" else str(it.get("error", "failed"))[:100]

    return res

def speed_state(name: str) -> dict:
    return request("GET", "/api/speed?name=" + httpx.QueryParams({"n": name})["n"].replace("+", "%20"), timeout=30)


def filter_check(host: str, port: int) -> dict:
    return request("POST", "/api/filter/check", {"host": host, "port": port}, timeout=40)


def valid_url(u: str) -> bool:
    return bool(re.match(r"^https?://[A-Za-z0-9.\-\[\]:]+(:\d+)?$", u or ""))

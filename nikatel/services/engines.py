"""Tunnel cores on the servers: which version of backhaul (and xray) each server runs, against the
newest GitHub release. Cores never update by themselves; the update copies a verified binary from
the panel's cache to every server that has the core and restarts its tunnel units."""
from __future__ import annotations

import hashlib
import io
import re
import tarfile
import time
import zipfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import httpx
from sqlalchemy import select

from ..config import get_settings
from ..db import session
from ..log import get_logger
from ..models import Server
from ..ssh import Node, SSHError, run_action, run_script
from .events import emit

log = get_logger("engines")
ENGINES = {
    "backhaul": {"title": "Backhaul", "repo": "Musixal/Backhaul", "asset": r"backhaul_linux_amd64\.tar\.gz$", "member": "backhaul", "bin": "/usr/local/bin/backhaul", "units": "backhaul-*.service"},
    "xray": {"title": "Xray", "repo": "XTLS/Xray-core", "asset": r"^Xray-linux-64\.zip$", "member": "xray", "bin": "/opt/nikatel-xgw/xray", "units": "nikatel-xgw.service"},
}
_STATE: dict = {"checked_at": 0, "result": None, "job": None}


def parse_versions(out: str) -> dict:
    d = {}

    for ln in out.splitlines():
        if ln.startswith("ENGINE "):
            p = ln.split(" ", 2)

            if len(p) == 3:
                m = re.search(r"v?\d+\.\d+(\.\d+)?", p[2])
                d[p[1]] = (("v" + m.group(0).lstrip("v")) if m else ("none" if "NONE" in p[2] else p[2].strip()[:20]))

    return d


def latest(engine: str) -> dict:
    e = ENGINES[engine]
    r = httpx.get(f"https://api.github.com/repos/{e['repo']}/releases/latest", timeout=25, headers={"Accept": "application/vnd.github+json", "User-Agent": "nikatel-panel"})
    r.raise_for_status()
    rel = r.json()
    tag = str(rel.get("tag_name", "")).strip()
    asset = next((a for a in rel.get("assets", []) if re.search(e["asset"], a.get("name", ""))), None)

    if not tag or not asset:
        raise RuntimeError("the release has no linux amd64 build")

    return {"version": "v" + tag.lstrip("v"), "url": asset["browser_download_url"], "page": rel.get("html_url", ""), "digest": str(asset.get("digest", "")).replace("sha256:", "")}


def check() -> dict:
    with session() as db:
        nodes = [(s.name, s.role, Node(s.host, s.ssh_user, s.ssh_port)) for s in db.scalars(select(Server).where(Server.enabled.is_(True)))]

    result = {}

    for eng in ENGINES:
        try:
            lt = latest(eng)
            result[eng] = {"title": ENGINES[eng]["title"], "latest": lt["version"], "page": lt["page"], "nodes": []}
        except (httpx.HTTPError, RuntimeError, ValueError) as e:
            result[eng] = {"title": ENGINES[eng]["title"], "error": str(e)[:160], "nodes": []}

    def one(item):
        name, role, node = item

        try:
            return name, role, parse_versions(run_script(node, "engine_versions", timeout=25))
        except (SSHError, ValueError):
            return name, role, None

    with ThreadPoolExecutor(8) as ex:
        rows = list(ex.map(one, nodes))

    for eng, info in result.items():
        for name, role, vers in rows:
            v = None if vers is None else vers.get(eng, "none")
            state = "unreachable" if vers is None else "not installed" if v == "none" else "current" if ("latest" in info and v == info["latest"]) else "outdated" if "latest" in info else "unknown"
            info["nodes"].append({"name": name, "role": role, "version": v, "state": state})

        info["outdated"] = sum(1 for n in info["nodes"] if n["state"] == "outdated")
        info["current"] = sum(1 for n in info["nodes"] if n["state"] == "current")

    _STATE["result"], _STATE["checked_at"] = result, int(time.time())
    return {"engines": result, "checked_at": _STATE["checked_at"]}


def last() -> dict:
    return {"engines": _STATE["result"] or {}, "checked_at": _STATE["checked_at"], "job": _STATE["job"]}


def _download(engine: str) -> tuple[str, bytes]:
    lt = latest(engine)
    data = httpx.get(lt["url"], timeout=300, follow_redirects=True).content

    if lt["digest"] and hashlib.sha256(data).hexdigest() != lt["digest"]:
        raise RuntimeError("the download does not match the checksum published by the release: nothing was changed")

    member = ENGINES[engine]["member"]

    if lt["url"].endswith(".zip"):
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            binary = z.read(member)
    else:
        with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tf:
            m = next((x for x in tf.getmembers() if x.isfile() and Path(x.name).name == member), None)

            if m is None:
                raise RuntimeError("the archive has no " + member)

            binary = tf.extractfile(m).read()

    if len(binary) < 1_000_000:
        raise RuntimeError("the binary is too small to be real")

    return lt["version"], binary


def update(engine: str) -> dict:
    """Download, verify, copy to every server that runs this core and is outdated, restart its units. Synchronous; takes minutes."""
    if engine not in ENGINES:
        raise ValueError("unknown engine")

    if get_settings().read_only:
        raise ValueError("the panel is read-only")

    if _STATE["job"] and not _STATE["job"].get("done"):
        raise RuntimeError("an update is already running")

    job: dict = {"engine": engine, "started": int(time.time()), "items": [], "done": False, "version": ""}
    _STATE["job"] = job

    try:
        version, binary = _download(engine)
        job["version"] = version
        sha = hashlib.sha256(binary).hexdigest()
        cache = get_settings().data_dir.parent / "engines" / f"{engine}-{version}"
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_bytes(binary)
        cache.chmod(0o755)
        info = (_STATE["result"] or {}).get(engine) or check()["engines"][engine]
        targets = [n["name"] for n in info["nodes"] if n["state"] == "outdated"]

        with session() as db:
            nodes = [(s.name, Node(s.host, s.ssh_user, s.ssh_port)) for s in db.scalars(select(Server).where(Server.name.in_(targets)))]

        def one(item):
            name, node = item

            try:
                import base64

                run_action(node, "put_file", {"PATH": ENGINES[engine]["bin"] + ".nikatel.new", "B64": base64.b64encode(binary).decode(), "MODE": "0755"}, timeout=240)
                out = run_action(node, "engine_swap", {"BIN": ENGINES[engine]["bin"], "SHA": sha, "UNITS": ENGINES[engine]["units"]}, timeout=120)
                return {"name": name, "state": "ok" if "SWAPPED" in out else "failed", "detail": out.strip()[-120:]}
            except (SSHError, ValueError) as e:
                return {"name": name, "state": "failed", "detail": str(e)[:140]}

        with ThreadPoolExecutor(4) as ex:
            job["items"] = list(ex.map(one, nodes))

        with session() as db:
            emit(db, "info", "engines", f"{ENGINES[engine]['title']} {version}: {sum(1 for i in job['items'] if i['state'] == 'ok')} of {len(targets)} servers updated.", engine)
    except (httpx.HTTPError, RuntimeError, ValueError, OSError) as e:
        job["error"] = str(e)[:200]
    finally:
        job["done"] = True
        job["finished"] = int(time.time())

    return job

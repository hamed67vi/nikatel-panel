"""Tunnel cores on the servers: which version of backhaul (and xray) each server runs, against the
newest GitHub release. Cores never update by themselves; the update copies a verified binary from
the panel's cache to every server that has the core and restarts its tunnel units."""
from __future__ import annotations

import hashlib
import io
import re
import subprocess
import tarfile
import threading
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
from ..ssh import target as ssh_target
from .events import emit

log = get_logger("engines")
ENGINES = {
    "backhaul": {"title": "Backhaul", "repo": "Musixal/Backhaul", "asset": r"backhaul_linux_amd64\.tar\.gz$", "member": "backhaul", "bin": "/usr/local/bin/backhaul", "units": "backhaul-*.service"},
    "backpack": {"title": "BackPack", "repo": "AminMGMT/BackPack", "asset": r"(?i)backpack[_-]linux[_-]amd64.*\.(tar\.gz|zip)$", "member": "backpack", "bin": "/usr/local/bin/backpack", "units": "backpack-*.service"},
    "paqet": {"title": "Paqet", "repo": "hanselime/paqet", "asset": r"^paqet-linux-amd64.*\.tar\.gz$", "member": "paqet_linux_amd64", "bin": "/usr/local/bin/paqet", "units": "paqet-*.service"},
    "chisel": {"title": "Chisel", "repo": "jpillora/chisel", "asset": r"^chisel_.*_linux_amd64\.gz$", "member": "chisel", "bin": "/usr/local/bin/nikatel-chisel", "units": "chisel-*.service"},
    "xray": {"title": "Xray", "repo": "XTLS/Xray-core", "asset": r"^Xray-linux-64\.zip$", "member": "xray", "bin": "/opt/nikatel-xgw/xray", "units": "nikatel-xgw.service"},
}
_STATE: dict = {"checked_at": 0, "result": None, "job": None, "checking": False}
_CHECK_LOCK = threading.Lock()


def parse_versions(out: str) -> dict:
    d = {}

    for ln in out.splitlines():
        if ln.startswith("ENGINE "):
            p = ln.split(" ", 2)

            if len(p) == 3:
                m = re.search(r"v?\d+\.\d+(\.\d+)?(-[0-9A-Za-z.]+)?", p[2])   # keeps a pre-release suffix: v1.0.0-alpha.21 is not v1.0.0
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
        info["missing"] = sum(1 for n in info["nodes"] if n["state"] == "not installed")      # (the servers that have not got it: About -> Install puts it on them)

    _STATE["result"], _STATE["checked_at"] = result, int(time.time())
    return {"engines": result, "checked_at": _STATE["checked_at"]}


def check_start() -> bool:
    """Start the check in the background (it asks every server over SSH: up to half a minute). False when one is already running."""
    with _CHECK_LOCK:
        if _STATE.get("checking"):
            return False

        _STATE["checking"] = True

    def work() -> None:
        try:
            check()
        except Exception:
            log.exception("engine check failed")
        finally:
            _STATE["checking"] = False

    threading.Thread(target=work, name="engines-check", daemon=True).start()
    return True


def last() -> dict:
    return {"engines": _STATE["result"] or {}, "checked_at": _STATE["checked_at"], "job": _STATE["job"], "checking": bool(_STATE.get("checking"))}


def _download(engine: str) -> tuple[str, bytes]:
    lt = latest(engine)
    data = httpx.get(lt["url"], timeout=300, follow_redirects=True).content

    if lt["digest"] and hashlib.sha256(data).hexdigest() != lt["digest"]:
        raise RuntimeError("the download does not match the checksum published by the release: nothing was changed")

    if not lt["digest"]:          # an older release publishes no digest: it is used (the size and the archive are still checked), but the log says it was not verified
        log.warning("engine %s %s: the release publishes no sha256 digest - the download could not be verified", engine, lt["version"])

    member = ENGINES[engine]["member"]

    if lt["url"].endswith(".zip"):
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            binary = z.read(member)
    elif lt["url"].endswith(".gz") and not lt["url"].endswith(".tar.gz"):
        import gzip

        binary = gzip.decompress(data)
    else:
        with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tf:
            m = next((x for x in tf.getmembers() if x.isfile() and Path(x.name).name == member), None)

            if m is None:
                raise RuntimeError("the archive has no " + member)

            binary = tf.extractfile(m).read()

    if len(binary) < 1_000_000:
        raise RuntimeError("the binary is too small to be real")

    return lt["version"], binary


def cache_file(engine: str) -> Path:
    """The core binary in the panel's cache (<data>/../engines/<engine>); downloaded and verified when missing."""
    if engine not in ENGINES:
        raise ValueError("unknown engine")

    cache = get_settings().data_dir.parent / "engines"
    cache.mkdir(parents=True, exist_ok=True)
    f = cache / engine

    if f.exists() and f.stat().st_size > 1_000_000:
        return f

    version, binary = _download(engine)
    f.write_bytes(binary)
    f.chmod(0o755)
    (cache / f"{engine}-{version}").write_bytes(binary)
    log.info("cached %s %s (%d bytes)", engine, version, len(binary))
    return f


def prefetch() -> dict:
    """Put every core the panel's cache lacks into it (verified downloads), so that a server added later gets them at once - also an Iran server that has no internet (the panel copies the binary over SSH).
    A core that cannot be downloaded now (GitHub is not reachable from here) is tried again at the next round; {engine: "cached" | "downloaded" | "failed: why"}."""
    out = {}
    cache = get_settings().data_dir.parent / "engines"

    for name in ENGINES:
        had = (cache / name).exists()

        try:
            cache_file(name)
            out[name] = "cached" if had else "downloaded"
        except (httpx.HTTPError, RuntimeError, ValueError, OSError) as e:
            out[name] = "failed: " + str(e)[:120]
            log.info("engine cache: %s not downloaded now (%s)", name, str(e)[:120])

    return out


def ensure_on_node(node: Node, engine: str, say=None, only_missing: bool = False) -> bool:
    """The node runs the same build of the core as the panel's cache. Returns True when it was (re)installed. With `only_missing` a core that is there is left exactly as it is (another build may be what
    the tunnels of that server run - a server added to the panel often has tunnels already): it is put there only when it is not."""
    e = ENGINES[engine]

    if only_missing and "SHA=none" not in run_script(node, "engine_sha", {"BIN": e["bin"]}, timeout=30):
        return False

    f = cache_file(engine)
    sha = hashlib.sha256(f.read_bytes()).hexdigest()

    if f"SHA={sha}" in run_script(node, "engine_sha", {"BIN": e["bin"]}, timeout=30):
        return False

    if e["bin"].startswith("/opt/nikatel-xgw/"):
        run_action(node, "xgw_mkdir", timeout=30)                  # (the Xray gateway keeps its binary in a directory of its own: it is made before the copy)

    s = get_settings()
    opts = ["-i", str(s.ssh_key), "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=accept-new", "-o", f"UserKnownHostsFile={s.known_hosts}", "-o", "ConnectTimeout=15"]
    r = subprocess.run(["scp", *opts, "-P", str(node.port), "--", str(f), f"{ssh_target(node)}:{e['bin']}.nikatel.new"], capture_output=True, text=True, timeout=300, check=False)

    if r.returncode != 0:
        raise RuntimeError(f"could not copy {e['title']} to {node.host}: " + r.stderr.strip()[:120])

    out = run_action(node, "engine_swap", {"BIN": e["bin"], "SHA": sha, "UNITS": "nikatel-none.service"}, timeout=120)

    if "SWAPPED" not in out:
        raise RuntimeError(f"{e['title']} did not install on {node.host}: {out.strip()[-120:]}")

    if say:
        say(f"{e['title']} installed on {node.host}")

    return True


def install_on(server_id: int, names=None, step=None) -> dict:
    """Put the tunnel cores on ONE server from the panel's cache (a core the cache lacks is downloaded and verified first): {engine: "installed" | "present" | "failed: why"}.
    Only the cores that are NOT there are put: one that is there stays as it is (Update, in Settings → About, is the way to change a build). A core that fails does not stop the next one.
    `step(title, i, n)` is told before each core (the row of the server shows it)."""
    if get_settings().read_only:
        raise ValueError("the panel is read-only")

    with session() as db:
        s = db.get(Server, server_id)

        if s is None:
            raise ValueError("server not found")

        node = Node(s.host, s.ssh_user, s.ssh_port)

    todo = [n for n in (names or ENGINES) if n in ENGINES]
    out: dict = {}

    for i, name in enumerate(todo, 1):
        if step:
            step(ENGINES[name]["title"], i, len(todo))

        try:
            out[name] = "installed" if ensure_on_node(node, name, only_missing=True) else "present"
        except (SSHError, RuntimeError, ValueError, OSError, httpx.HTTPError, subprocess.SubprocessError) as e:
            out[name] = "failed: " + str(e)[:120]

    return out


def update(engine: str, only_missing: bool = False) -> dict:
    """Download, verify, copy to every server that runs this core and is outdated and restart its units - or, with `only_missing`, copy it to the servers that have not got it (nothing is restarted: no
    unit uses it yet). Synchronous; takes minutes."""
    if engine not in ENGINES:
        raise ValueError("unknown engine")

    if get_settings().read_only:
        raise ValueError("the panel is read-only")

    if _STATE["job"] and not _STATE["job"].get("done"):
        raise RuntimeError("an update is already running")

    job: dict = {"engine": engine, "started": int(time.time()), "items": [], "done": False, "version": "", "kind": "install" if only_missing else "update"}
    _STATE["job"] = job

    try:
        if only_missing:
            cache_file(engine)                                          # (the panel's own build - downloaded and verified when it has none; the servers that have not got the core get THAT one)
            binary, sha, version = b"", "", ""
        else:
            version, binary = _download(engine)
            sha = hashlib.sha256(binary).hexdigest()
            cache = get_settings().data_dir.parent / "engines" / f"{engine}-{version}"
            cache.parent.mkdir(parents=True, exist_ok=True)
            cache.write_bytes(binary)
            cache.chmod(0o755)
            newest = cache.parent / engine                              # (the cache of the panel follows the update: a tunnel built later must not put the older build back on a server)
            newest.write_bytes(binary)
            newest.chmod(0o755)

        job["version"] = version
        info = (_STATE["result"] or {}).get(engine) or check()["engines"][engine]
        targets = [n["name"] for n in info["nodes"] if n["state"] == ("not installed" if only_missing else "outdated")]

        with session() as db:
            nodes = [(s.name, Node(s.host, s.ssh_user, s.ssh_port)) for s in db.scalars(select(Server).where(Server.name.in_(targets)))]

        def one(item):
            name, node = item

            try:
                if only_missing:
                    ensure_on_node(node, engine, only_missing=True)
                    return {"name": name, "state": "ok", "detail": "installed"}

                import base64

                run_action(node, "put_file", {"PATH": ENGINES[engine]["bin"] + ".nikatel.new", "B64": base64.b64encode(binary).decode(), "MODE": "0755"}, timeout=240)
                out = run_action(node, "engine_swap", {"BIN": ENGINES[engine]["bin"], "SHA": sha, "UNITS": ENGINES[engine]["units"]}, timeout=120)
                return {"name": name, "state": "ok" if "SWAPPED" in out else "failed", "detail": out.strip()[-120:]}
            except (SSHError, ValueError, RuntimeError, OSError, subprocess.SubprocessError) as e:
                return {"name": name, "state": "failed", "detail": str(e)[:140]}

        with ThreadPoolExecutor(4) as ex:
            job["items"] = list(ex.map(one, nodes))

        done = sum(1 for i in job["items"] if i["state"] == "ok")

        with session() as db:
            emit(db, "info", "engines", f"{ENGINES[engine]['title']}: put on {done} of {len(targets)} servers." if only_missing else f"{ENGINES[engine]['title']} {version}: {done} of {len(targets)} servers updated.", engine)
    except (httpx.HTTPError, RuntimeError, ValueError, OSError) as e:
        job["error"] = str(e)[:200]
    finally:
        job["done"] = True
        job["finished"] = int(time.time())

    return job

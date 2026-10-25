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
_STATE: dict = {"checked_at": 0, "result": None, "job": None, "checking": False, "wireguard": None, "wg_job": None}
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


def parse_wg(out: str) -> dict:
    """Pure: what a server says about WireGuard in the answer of `engine_versions`: {"tool": wireguard-tools is there, "module": the kernel can load WireGuard, "apt": apt-get is there (the only way the panel installs it)}."""
    kv = {}

    for line in str(out).splitlines():
        p = line.split()

        if len(p) == 2 and p[0] in ("WGTOOL", "WGMOD", "WGAPT"):
            kv[p[0]] = p[1] == "1"

    return {"tool": kv.get("WGTOOL", False), "module": kv.get("WGMOD", False), "apt": kv.get("WGAPT", False)}


def wg_state(w: dict | None) -> str:
    """Pure: one word for the WireGuard state of a server: "installed", "kernel" (the tools are there, the kernel cannot load the module), "missing" (not installed, apt can install it), "no apt" (not installed, and this server has no apt-get: by hand), "unreachable"."""
    if w is None:
        return "unreachable"

    if w["tool"]:
        return "installed" if w["module"] else "kernel"

    return "missing" if w["apt"] else "no apt"


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
            raw = run_script(node, "engine_versions", timeout=25)
            return name, role, parse_versions(raw), parse_wg(raw)
        except (SSHError, ValueError):
            return name, role, None, None

    with ThreadPoolExecutor(8) as ex:
        rows4 = list(ex.map(one, nodes))

    rows = [(n, r, v) for n, r, v, _ in rows4]
    _STATE["wireguard"] = wg_summary([(n, r, w) for n, r, _, w in rows4])

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


def wg_summary(rows: list) -> dict:
    """Pure: the WireGuard card of the cores box - {"title", "nodes": [{"name", "role", "state", "tool", "module", "apt"}], "installed": n, "missing": n (the servers where the owner can have it installed), "stuck": n (kernel / no apt: by hand)}."""
    nodes = []

    for name, role, w in rows:
        s = wg_state(w)
        nodes.append({"name": name, "role": role, "state": s, **(w or {"tool": False, "module": False, "apt": False})})

    return {"title": "WireGuard tools", "nodes": nodes, "installed": sum(1 for n in nodes if n["state"] == "installed"), "missing": sum(1 for n in nodes if n["state"] == "missing"),
            "stuck": sum(1 for n in nodes if n["state"] in ("kernel", "no apt"))}


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
    return {"engines": _STATE["result"] or {}, "checked_at": _STATE["checked_at"], "job": _STATE["job"], "checking": bool(_STATE.get("checking")), "wireguard": _STATE.get("wireguard"), "wg_job": _STATE.get("wg_job")}


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


def _copy_to_node(node: Node, engine: str, f: Path) -> None:
    """Copy the core `f` (the panel's cache) next to its place on the server (`<bin>.nikatel.new`) with scp: nothing runs from it yet. The Xray gateway keeps its binary in a directory of its own, made first. RuntimeError with
    the sentence when the copy fails. (The Update button used to send the binary as base64 through the `put_file` action: its path and size rules refuse a core - on 2026-10-25 an update of Xray failed on all 29 servers at once.)"""
    e = ENGINES[engine]

    if e["bin"].startswith("/opt/nikatel-xgw/"):
        run_action(node, "xgw_mkdir", timeout=30)

    s = get_settings()
    opts = ["-i", str(s.ssh_key), "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=accept-new", "-o", f"UserKnownHostsFile={s.known_hosts}", "-o", "ConnectTimeout=15"]
    r = subprocess.run(["scp", *opts, "-P", str(node.port), "--", str(f), f"{ssh_target(node)}:{e['bin']}.nikatel.new"], capture_output=True, text=True, timeout=300, check=False)

    if r.returncode != 0:
        raise RuntimeError(f"could not copy {e['title']} to {node.host}: " + r.stderr.strip()[:120])


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

    _copy_to_node(node, engine, f)
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


_SWAP_LOCK = threading.Lock()


def swap_unhealthy(out: str) -> list[str]:
    """Pure: the units that did NOT come back after the restart of a swap (the line `NOT_ACTIVE: unit unit` of the `engine_swap` action), [] when all did or nothing was restarted."""
    for line in str(out).splitlines():
        if line.startswith("NOT_ACTIVE:"):
            return line.split(":", 1)[1].split()

    return []


def update(engine: str, only_missing: bool = False) -> dict:
    """Download, verify, copy to every server that runs this core and is outdated and restart its units - or, with `only_missing`, copy it to the servers that have not got it (nothing is restarted: no
    unit uses it yet). Synchronous; takes minutes. The copies go four at a time, the swaps (which restart the units that RUN the core) one server at a time, and the first unit that does not come back
    active after its restart stops the update there (`job["halt"]`): the other servers are not touched."""
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

                _copy_to_node(node, engine, newest)                      # (the slow part, four servers at a time: only a file next to the binary - nothing runs from it yet)

                with _SWAP_LOCK:                                         # (the swap restarts the units that run the core: ONE server at a time, so that what stands behind them is never all down at once)
                    if job.get("halt"):
                        return {"name": name, "state": "failed", "detail": "not tried: " + job["halt"]}

                    out = run_action(node, "engine_swap", {"BIN": ENGINES[engine]["bin"], "SHA": sha, "UNITS": ENGINES[engine]["units"]}, timeout=120)
                    bad = swap_unhealthy(out)

                    if bad:
                        job["halt"] = f"{', '.join(bad)} on {name} did not come back after its restart: the update stopped there, the other servers were not touched"
                        return {"name": name, "state": "failed", "detail": job["halt"][:140]}

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

# ------------------------------------------------------------------ WireGuard tools (the owner presses the button: the only way the panel puts a package on a server, apt only)
def wg_install_result(out: str) -> tuple[bool, str]:
    """Pure: (done, sentence) from the answer of the `wg_install` action: "WG_OK present" / "WG_OK installed (...)" or "WG_FAIL why" - an answer that is neither is a failure that says so."""
    text = str(out)

    if "WG_OK" in text:
        return True, "already there" if "WG_OK present" in text else "installed"

    m = text.split("WG_FAIL", 1)

    return False, (m[1].strip()[:240] if len(m) > 1 and m[1].strip() else "no answer from the server (is it on?)")


def install_wireguard_on(server_id: int) -> str:
    """Put wireguard-tools on ONE server with apt (the action `wg_install`: apt only, nothing is copied from another machine) and check that its kernel can load the module. Returns "installed" or "already there";
    RuntimeError with the sentence that says what to do when it cannot (no apt, apt fails, the kernel has no module). What the Doctor remembers as "not tested: tools missing" for this server is forgotten, so
    the next survey tests WireGuard at once."""
    if get_settings().read_only:
        raise ValueError("the panel is read-only")

    with session() as db:
        s = db.get(Server, server_id)

        if s is None:
            raise ValueError("server not found")

        node, name = Node(s.host, s.ssh_user, s.ssh_port), s.name

    ok, why = wg_install_result(run_action(node, "wg_install", timeout=330))

    if not ok:
        raise RuntimeError(f"{name}: {why}")

    from . import knowledge

    knowledge.forget_tool_skips(server_id)
    return why


def install_wireguard(server_ids: list[int] | None = None, _started: bool = False) -> dict:
    """The owner's button: wireguard-tools on every server that lacks it (or on `server_ids`), four at a time. {"started", "items": [{"name", "state": "ok" | "failed", "detail"}], "done"}. Runs in the thread of the caller;
    refused while another install runs."""
    if not _started and _STATE["wg_job"] and not _STATE["wg_job"].get("done"):
        raise RuntimeError("an install is already running")

    job: dict = {"started": int(time.time()), "items": [], "done": False}
    _STATE["wg_job"] = job

    try:
        with session() as db:
            rows = [(s.id, s.name) for s in db.scalars(select(Server).where(Server.enabled.is_(True)))
                    if server_ids is None or s.id in server_ids]

        def one(item):
            sid, name = item

            try:
                return {"name": name, "state": "ok", "detail": install_wireguard_on(sid)}
            except (SSHError, ValueError, RuntimeError, OSError) as e:
                return {"name": name, "state": "failed", "detail": str(e)[:240]}

        with ThreadPoolExecutor(4) as ex:
            job["items"] = list(ex.map(one, rows))

        done = sum(1 for i in job["items"] if i["state"] == "ok")

        with session() as db:
            emit(db, "info" if done == len(rows) else "warn", "engines", f"WireGuard tools: ready on {done} of {len(rows)} servers.", "wireguard")
    except (ValueError, RuntimeError, OSError) as e:
        job["error"] = str(e)[:200]
    finally:
        job["done"] = True
        job["finished"] = int(time.time())

    return job


def install_wireguard_start(server_ids: list[int] | None = None) -> bool:
    """`install_wireguard` in the background (apt can take minutes); False when one is running."""
    if get_settings().read_only:
        raise ValueError("the panel is read-only")

    j = _STATE["wg_job"]

    if j and not j.get("done"):
        return False

    def work() -> None:
        try:
            install_wireguard(server_ids, _started=True)
        except Exception:
            log.exception("wireguard install failed")

    _STATE["wg_job"] = {"started": int(time.time()), "items": [], "done": False}       # (the page sees it at once)
    threading.Thread(target=work, name="wg-install", daemon=True).start()
    return True
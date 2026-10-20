"""Node tools that the old panel had: console, connect with a password (installs the panel's key),
add many servers at once, open-in-PuTTY/ssh launchers with the operator's own key, Abuse-Defender,
reboot, country lookup of foreign servers, "is it filtered in Iran?" checks."""
from __future__ import annotations

import os
import re
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor

import httpx
from sqlalchemy import select

from ..config import get_settings
from ..db import session
from ..log import get_logger
from ..models import Server
from ..ssh import Node, SSHError, run_action, run_raw, run_script
from ..ssh import target as ssh_target
from . import profiles
from .events import emit

log = get_logger("nodes")

ABUSE_LIST_URL = "https://raw.githubusercontent.com/Kiya6955/Abuse-Defender/main/abuse-ips.ipv4"
PRESETS = {
    "logs": ("Last logs", "journalctl -n 60 --no-pager -o short-iso | cut -c1-220"),
    "ports": ("Listening ports", "ss -ltnp | awk 'NR==1 || /LISTEN/' | cut -c1-200"),
    "failed": ("Failed services", "systemctl --failed --no-legend --plain; echo; systemctl list-units --no-legend --plain 'backhaul-*' 'nikatel-*' 'backpack-*' 'chisel-*' 'paqet-*' | cut -c1-160"),
    "top": ("Busiest processes", "ps -eo pid,comm,pcpu,pmem,etime --sort=-pcpu | head -15"),
    "disk": ("Disk", "df -h / /var /opt 2>/dev/null | awk '!a[$1]++'; echo; du -xsh /var/log /root 2>/dev/null"),
    "net": ("Network and ports", "ip -br addr; echo; ip route; echo; ss -s; echo; echo 'Listening ports:'; ss -ltnp | awk 'NR>1 && /LISTEN/' | cut -c1-160"),
    "health": ("Health", "uptime; echo; free -h; echo; df -h / | tail -2"),
    "who": ("Who is logged in", "who; echo; last -n 8 -a | head -10"),
    "time": ("Clock", "date; echo; timedatectl 2>/dev/null | head -6"),
    "tunnels": ("Tunnel units", "systemctl list-units --all --no-legend --plain 'backhaul-*' 'nikatel-gre-*' 'backpack-*' 'chisel-*' 'paqet-*' | cut -c1-160; echo; ip -br link show type gre 2>/dev/null"),
}


def _srv(server_id: int) -> tuple[Node, str, str, str]:
    with session() as db:
        s = db.get(Server, server_id)

        if s is None:
            raise ValueError("server not found")

        return Node(s.host, s.ssh_user, s.ssh_port), s.name, s.role, s.host


def _set_extra(server_id: int, **kw) -> None:
    with session() as db:
        s = db.get(Server, server_id)

        if s is not None:
            d = dict(s.extra or {})
            d.update(kw)
            s.extra = d


def set_busy(server_id: int, what: str = "", detail: str = "", unless: str = "") -> None:
    """What the panel is doing with this server right now (connecting, installing): shown in the list instead of a dash. Empty `what` clears it. `unless`: leave it alone when it is of that kind (the steps of making a node are not overwritten by the install inside them)."""
    if unless:
        with session() as db:
            s = db.get(Server, server_id)

            if s is not None and ((s.extra or {}).get("busy") or {}).get("what") == unless:
                return

    _set_extra(server_id, busy=({"what": what, "detail": detail, "at": int(time.time())} if what else None))


# ------------------------------------------------------------------ console
def console(server_id: int, command: str) -> dict:
    """Run one command on a server (read-write mode only). Every command is logged with the server name."""
    cmd = str(command or "").strip()

    if cmd in PRESETS:
        cmd = PRESETS[cmd][1]

    if not cmd:
        raise ValueError("type a command")

    if len(cmd) > 4000:
        raise ValueError("the command is too long")

    node, name, _role, _host = _srv(server_id)
    log.info("console on %s: %s", name, cmd[:160].replace("\n", " "))
    t0 = time.time()

    try:
        out = run_raw(node, cmd, timeout=60)
        code = 0
    except SSHError as e:
        out = str(e)
        code = e.code

    with session() as db:
        emit(db, "info", "nodes", f"Console on {name}: {cmd[:80]}" + ("" if code == 0 else f" (exit {code})"), name)

    if len(out) > 30000:
        out = "...\n" + out[-30000:]

    return {"code": code, "output": out, "ms": int((time.time() - t0) * 1000)}


# ------------------------------------------------------------------ connect with a password
def _pubkey() -> str:
    path = get_settings().ssh_key.with_suffix(".pub") if get_settings().ssh_key.suffix else get_settings().ssh_key.parent / (get_settings().ssh_key.name + ".pub")

    try:
        return path.read_text().strip()
    except OSError:          # (no path in the sentence: it goes to the browser)
        raise ValueError("the panel's SSH public key is missing or cannot be read - make the key pair first (Settings shows how); without it the panel cannot log in to a server") from None


def connect(server_id: int, password: str) -> dict:
    """Connect with the SSH password; the list shows *connecting…* meanwhile."""
    if not password:
        raise ValueError("enter the SSH password of the server")

    set_busy(server_id, "connecting")

    try:
        return _connect(server_id, password)
    finally:
        set_busy(server_id, "")


def _connect(server_id: int, password: str) -> dict:
    """Put the panel's public key on the server using the SSH password once (sshpass); the password is not kept."""
    if not password:
        raise ValueError("enter the SSH password of the server")

    node, name, _role, host = _srv(server_id)
    s = get_settings()
    pub = _pubkey()
    script = ("umask 077; mkdir -p ~/.ssh; touch ~/.ssh/authorized_keys; chmod 700 ~/.ssh; chmod 600 ~/.ssh/authorized_keys; "
              f"grep -qxF '{pub}' ~/.ssh/authorized_keys || echo '{pub}' >> ~/.ssh/authorized_keys; echo KEY_INSTALLED")
    env = dict(os.environ, SSHPASS=password)

    try:
        r = subprocess.run(["sshpass", "-e", "ssh", "-p", str(node.port), "-o", "StrictHostKeyChecking=accept-new", "-o", f"UserKnownHostsFile={s.known_hosts}",
                            "-o", "ConnectTimeout=12", "-o", "PreferredAuthentications=password", "-o", "PubkeyAuthentication=no", "-o", "LogLevel=ERROR",
                            "--", ssh_target(Node(host, node.user, node.port)), script], check=False, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, timeout=30, env=env)
    except FileNotFoundError:
        raise RuntimeError("sshpass is not installed on the panel server (apt install sshpass)")
    except subprocess.TimeoutExpired:
        raise RuntimeError("SSH password connection timed out")

    low = r.stdout.lower()

    if r.returncode != 0 or "KEY_INSTALLED" not in r.stdout:
        if "permission denied" in low:
            msg = "wrong user or password"
        elif "refused" in low:
            msg = f"SSH port {node.port} is closed"
        else:
            msg = "SSH connection failed: " + (r.stdout.strip().splitlines() or ["no answer"])[-1][:140]

        with session() as db:                       # the server list shows why this server has no login, until it has one
            srv = db.get(Server, server_id)

            if srv is not None:
                srv.last_error = ("SSH login failed: " + msg)[:300]

        raise RuntimeError(msg)

    out = run_script(node, "ping", timeout=25)
    hostname = out.replace("HOST=", "").strip()[:80]
    _set_extra(server_id, hostname=hostname, connected_at=int(time.time()))

    with session() as db:
        emit(db, "ok", "nodes", f"SSH key installed on {name} ({hostname}).", name)

    return {"hostname": hostname}


def set_password(server_id: int, password: str) -> dict:
    """Change the login password of the server's SSH user (after the server was added). The panel itself logs in with its key."""
    import base64

    if not 8 <= len(password) <= 128 or any(ord(c) < 32 or ord(c) == 127 for c in password):
        raise ValueError("the password must be 8 to 128 characters, without control characters")

    node, name, _role, _host = _srv(server_id)
    user = node.user or "root"
    out = run_action(node, "set_password", {"USER": user, "B64": base64.b64encode(f"{user}:{password}".encode()).decode()}, timeout=30)

    if "PASSWORD_SET" not in out:
        raise RuntimeError("the server did not accept the new password: " + out.strip()[-120:])

    with session() as db:
        emit(db, "ok", "nodes", f"SSH password of {user}@{name} changed.", name)

    return {"user": user}


def jump_candidates(target: Node) -> list[Node]:
    """The servers that may carry an SSH session to a server whose direct session stalls (ssh.py): enabled Iran servers that answer, are not themselves reached through a
    jump, and are not the target - the probe server first (it sits where the users are), then by name."""
    from .. import ssh as _ssh
    from . import settings as st

    pid = int(st.get("probe").get("server_id") or 0)

    with session() as db:
        rows = [(s.id, s.name, Node(s.host, s.ssh_user, s.ssh_port)) for s in db.scalars(select(Server).where(Server.role == "iran", Server.enabled.is_(True), Server.online.is_(True)))
                if s.host != target.host and not _ssh.via_of(s.host)]

    rows.sort(key=lambda r: (0 if r[0] == pid else 1, r[1]))
    return [r[2] for r in rows]


def server_conflict(db, name: str, host: str, exclude_id: int = 0) -> str:
    """"" when the name and the address are free, else the sentence that says what is taken. Names are compared without capitals: TR1 and tr1 are the same name."""
    from sqlalchemy import func

    by_name = db.scalar(select(Server).where(func.lower(Server.name) == str(name).lower(), Server.id != exclude_id))

    if by_name is not None:
        return f"a server named {by_name.name} already exists ({'Iran' if by_name.role == 'iran' else 'foreign'}): choose another name"

    by_host = db.scalar(select(Server).where(Server.host == host, Server.id != exclude_id))

    if by_host is not None:
        return f"the address {host} already belongs to the server {by_host.name}"

    return ""

def add_many(role: str, lines: str, password: str = "", ssh_user: str = "root", ssh_port: int = 22, tag: str = "") -> list[dict]:
    """Lines of `name host [user] [port]`; every server is added to the panel and, with a password, connected in parallel."""
    if role not in ("iran", "foreign"):
        raise ValueError("bad role")

    items = []

    for ln in str(lines or "").splitlines():
        p = ln.replace(",", " ").split()

        if not p:
            continue

        if len(p) == 1:
            p = [re.sub(r"[^A-Za-z0-9._-]", "-", p[0])[:30], p[0]]

        name, host = p[0][:64], p[1]
        user = p[2] if len(p) > 2 else ssh_user
        user = str(user)[:64]
        port = int(p[3]) if len(p) > 3 and p[3].isdigit() else int(ssh_port)

        if not re.fullmatch(r"[a-z_][a-z0-9_-]{0,31}", user):
            items.append({"name": name, "host": host, "ok": False, "error": "bad ssh user"})
            continue

        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", name) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9.\-]*", host):
            items.append({"name": name, "host": host, "ok": False, "error": "bad name or host"})
            continue

        items.append({"name": name, "host": host, "user": user, "port": port, "ok": None})

    if not any(i["ok"] is None for i in items):
        raise ValueError("give at least one line: name host [user] [port]")

    for it in items:
        if it["ok"] is not None:
            continue

        with session() as db:
            why = server_conflict(db, it["name"], it["host"])

            if why:
                it.update(ok=False, error=why)
                continue

            srv = Server(name=it["name"], host=it["host"], role=role, ssh_user=it["user"], ssh_port=it["port"])
            db.add(srv)
            db.flush()
            profiles.ensure_server(db, srv, tag)          # (a made server is on the Lab page at once, under its tag)
            it["id"] = srv.id
            emit(db, "info", "servers", f"Server {srv.name} ({role}) added.", srv.name)

    def one(it):
        if it["ok"] is not None or "id" not in it:
            return it

        try:
            if password:
                r = connect(it["id"], password)
                it.update(ok=True, detail=r["hostname"])
            else:
                node = Node(it["host"], it["user"], it["port"])
                out = run_script(node, "ping", timeout=25)
                it.update(ok=True, detail=out.replace("HOST=", "").strip()[:80])
        except (RuntimeError, ValueError) as e:
            it.update(ok=False, error=str(e)[:160] + ("" if password else " — the panel key is not on this server yet: add it with a password"))

        return it

    with ThreadPoolExecutor(6) as ex:
        items = list(ex.map(one, items))

    return items


# ------------------------------------------------------------------ the operator's own key + launchers
def user_key_path():
    return get_settings().data_dir / "user_key"


def user_key_ensure() -> str:
    p = user_key_path()

    if not p.exists():
        subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-C", "nikatel-user", "-f", str(p)], check=True, capture_output=True, timeout=30)
        p.chmod(0o600)

    return (p.parent / (p.name + ".pub")).read_text().strip()


def launcher(server_id: int, app: str) -> tuple[str, bytes]:
    """A small .bat that opens the server in Windows ssh / PuTTY / MobaXterm with the operator's key (installed on the server first)."""
    if app not in ("ssh", "putty", "mobaxterm"):
        raise ValueError("bad app")

    node, name, _role, host = _srv(server_id)
    pub = user_key_ensure()
    run_action(node, "add_pubkey", {"PUB": pub}, timeout=30)
    user, port = node.user, node.port
    safe = re.sub(r"[^A-Za-z0-9._-]+", "_", name)

    if app == "ssh":
        body = f'@echo off\r\ntitle {safe}\r\nssh -i "%USERPROFILE%\\.ssh\\nikatel_user" -o StrictHostKeyChecking=accept-new -p {port} {user}@{host}\r\npause\r\n'
    elif app == "putty":
        body = (f'@echo off\r\nset "K=%USERPROFILE%\\.ssh\\nikatel_user.ppk"\r\nset "P=putty"\r\nwhere putty >nul 2>nul || set "P=%ProgramFiles%\\PuTTY\\putty.exe"\r\n'
                f'start "" "%P%" -ssh {user}@{host} -P {port} -i "%K%"\r\n')
    else:
        body = (f'@echo off\r\nset "M=%ProgramFiles(x86)%\\Mobatek\\MobaXterm\\MobaXterm.exe"\r\nif not exist "%M%" set "M=%ProgramFiles%\\Mobatek\\MobaXterm\\MobaXterm.exe"\r\n'
                f'if not exist "%M%" set "M=%LOCALAPPDATA%\\Programs\\MobaXterm\\MobaXterm.exe"\r\nstart "" "%M%" -newtab "ssh -p {port} -i /drives/c/Users/%USERNAME%/.ssh/nikatel_user {user}@{host}"\r\n')

    return f"open-{app}-{safe}.bat", body.encode()


# ------------------------------------------------------------------ abuse defender / reboot
def _whitelist() -> str:
    ips = set()

    with session() as db:
        for s in db.scalars(select(Server)):
            if re.fullmatch(r"\d+\.\d+\.\d+\.\d+", s.host):
                ips.add(s.host)

    try:
        for x in subprocess.run(["hostname", "-I"], check=False, capture_output=True, text=True, timeout=5).stdout.split():
            if re.fullmatch(r"\d+\.\d+\.\d+\.\d+", x) and not re.match(r"(10\.|192\.168\.|172\.(1[6-9]|2\d|3[01])\.|169\.254\.|127\.)", x):
                ips.add(x)
    except (OSError, subprocess.SubprocessError):
        pass

    return " ".join(sorted(ips)[:200])


def abuse_steps(out: str) -> list[str]:
    """Pure: the lines of the install script that tell what it did ("STEP 2/5 ...") - the log the page shows."""
    return [ln[5:].strip()[:200] for ln in str(out).splitlines() if ln.startswith("STEP ")][:20]


def abuse(server_id: int, action: str, say=None) -> dict:
    """`say(line)`: where the lines of the log go (the story of an action the page follows in a box)."""
    node, name, role, _host = _srv(server_id)

    if action == "install":
        if role != "foreign":
            raise ValueError("Abuse-Defender goes on foreign servers only, never on Iran servers")

        out = run_action(node, "abuse_install", {"URL": ABUSE_LIST_URL, "WL": _whitelist()}, timeout=300)

        for ln in abuse_steps(out) if say else []:
            say(ln)

        m = re.search(r"ABUSE_OK=(\d+)", out)

        if not m:
            raise RuntimeError("Abuse-Defender did not finish: " + out.strip()[-200:])

        rules = int(m.group(1))
        _set_extra(server_id, abuse={"installed": True, "rules": rules, "at": int(time.time())})

        with session() as db:
            emit(db, "ok", "nodes", f"Abuse-Defender installed on {name}: {rules} ranges blocked.", name)

        return {"installed": True, "rules": rules}

    if action == "remove":
        run_action(node, "abuse_remove", timeout=60)
        _set_extra(server_id, abuse={"installed": False, "rules": 0, "at": int(time.time())})

        with session() as db:
            emit(db, "info", "nodes", f"Abuse-Defender removed from {name}.", name)

        return {"installed": False, "rules": 0}

    raise ValueError("unknown action")


def abuse_start(server_id: int, action: str) -> int:
    """`abuse` as an action with a story that the page follows in a box (the owner, 2026-10-19: "when something is installed, put its log in the box"): what the script did, step by step, then the result - or what it
    failed with, which used to be a toast that went by."""
    from . import autopilot

    if action not in ("install", "remove"):
        raise ValueError("unknown action")

    _node, name, role, _host = _srv(server_id)

    if action == "install" and role != "foreign":
        raise ValueError("Abuse-Defender goes on foreign servers only, never on Iran servers")

    def work(aid: int) -> None:
        verb = "installing on" if action == "install" else "removing from"
        autopilot._say(aid, f"Abuse-Defender: {verb} {name} - the server does the work, up to a minute…")

        try:
            res = abuse(server_id, action, lambda line: autopilot._say(aid, line))
        except (SSHError, RuntimeError, ValueError) as e:
            autopilot._say(aid, f"Failed: {str(e)[:500]}", done=True, ok=False)
            return

        autopilot._say(aid, f"Done: Abuse-Defender is on, {res['rules']} ranges are blocked." if res["installed"] else "Done: Abuse-Defender is removed.", done=True, ok=True)

    return autopilot._start(f"abuse {name}", work, action="install")


REBOOTING = "Rebooting"      # the `what` of the busy mark of a restart asked from the panel


def reboot(server_id: int) -> None:
    node, name, _role, _host = _srv(server_id)
    run_action(node, "reboot", timeout=20)
    set_busy(server_id, REBOOTING, "waiting for it to come back")          # (the row says so, in blue, until the server answers again after a restart - services/facts.py; it was a red "offline" row that jumped to the top of the list)

    with session() as db:
        emit(db, "warn", "nodes", f"Server {name} is rebooting (asked from the panel).", name)


# ------------------------------------------------------------------ country of foreign servers
_RIR_DOMAINS = ("ripe.net", "arin.net", "apnic.net", "lacnic.net", "afrinic.net", "iana.org", "rdap.org", "example.com")


def provider_from(source: str, d: dict) -> dict:
    """Pure: the hosting provider (name, domain, asn) one geo service tells about an address."""
    p = {"isp": "", "org": "", "domain": "", "asn": ""}

    if source == "ipwho":
        c = d.get("connection") or {}
        p.update(isp=str(c.get("isp") or ""), org=str(c.get("org") or ""), domain=str(c.get("domain") or ""), asn=str(c.get("asn") or ""))
    elif source == "ip-api":
        m = re.match(r"AS(\d+)\s*(.*)", str(d.get("as") or ""))
        p.update(isp=str(d.get("isp") or ""), org=str(d.get("org") or ""), asn=m.group(1) if m else "")
    elif source == "ipinfo":
        m = re.match(r"AS(\d+)\s*(.*)", str(d.get("org") or ""))
        p.update(org=m.group(2) if m else str(d.get("org") or ""), asn=m.group(1) if m else "")

    p = {k: v.strip() for k, v in p.items()}
    p["domain"] = p["domain"].lower().removeprefix("www.")
    return p


def domain_from_rdap(doc: dict) -> str:
    """Pure: the provider's domain from the e-mail addresses in an RDAP answer (the registries' own domains are not it)."""
    found: dict[str, int] = {}

    def walk(x):
        if isinstance(x, dict):
            for v in x.values():
                walk(v)
        elif isinstance(x, list):
            if len(x) >= 4 and x[0] == "email" and isinstance(x[3], str) and "@" in x[3]:
                dom = x[3].rsplit("@", 1)[1].lower().strip(". ")

                if dom and not any(dom == r or dom.endswith("." + r) for r in _RIR_DOMAINS):
                    found[dom] = found.get(dom, 0) + 1

            for v in x:
                walk(v)

    walk(doc)
    return max(found.items(), key=lambda kv: kv[1])[0] if found else ""


def _geo_sources(host: str) -> list[dict]:
    out = []

    for name, url, pick in (
        ("ip-api", f"http://ip-api.com/json/{host}?fields=status,country,countryCode,city,isp,org,as", lambda d: (d.get("countryCode"), d.get("country"), d.get("city")) if d.get("status") == "success" else None),
        ("ipwho", f"https://ipwho.is/{host}", lambda d: (d.get("country_code"), d.get("country"), d.get("city")) if d.get("success") else None),
        ("ipinfo", f"https://ipinfo.io/{host}/json", lambda d: (d.get("country"), "", d.get("city"))),
        ("country.is", f"https://api.country.is/{host}", lambda d: (d.get("country"), "", "")),
    ):
        try:
            d = httpx.get(url, timeout=8, headers={"User-Agent": "nikatel-panel", "Accept": "application/json"}).json()
            r = pick(d) if isinstance(d, dict) else None
        except (httpx.HTTPError, ValueError):
            continue

        if not r:
            continue

        code = str(r[0] or "").strip().upper()

        if re.fullmatch(r"[A-Z]{2}", code):
            out.append({"source": name, "code": code, "country": str(r[1] or "").strip(), "city": str(r[2] or "").strip(), **provider_from(name, d)})

    return out


def _rdap_domain(host: str) -> str:
    try:
        d = httpx.get(f"https://rdap.org/ip/{host}", timeout=8, follow_redirects=True, headers={"User-Agent": "nikatel-panel", "Accept": "application/rdap+json, application/json"}).json()
        return domain_from_rdap(d) if isinstance(d, dict) else ""
    except (httpx.HTTPError, ValueError):
        return ""


def lookup(host: str, want_provider: bool = True) -> dict:
    """Country / city by majority vote of up to four services, plus the hosting provider (name, website domain, ASN)."""
    src = _geo_sources(host)
    votes: dict[str, list] = {}

    for s in src:
        votes.setdefault(s["code"], []).append(s)

    first = lambda key: next((s[key] for s in src if s.get(key)), "")
    provider = {"provider": first("isp") or first("org"), "domain": first("domain"), "asn": first("asn")}

    if want_provider and src and not provider["domain"]:
        provider["domain"] = _rdap_domain(host)

    if not votes:
        return {"country_code": "", "country": "", "city": "", "sources": 0, "total": len(src), **provider}

    code, group = max(votes.items(), key=lambda kv: (len(kv[1]), any(x["source"] == "ip-api" for x in kv[1])))
    return {"country_code": code, "country": next((x["country"] for x in group if x["country"]), ""), "city": next((x["city"] for x in group if x["city"]), ""), "sources": len(group), "total": len(src), **provider}


def geo_refresh(only_missing: bool = False) -> list[dict]:
    """Country of the foreign servers and the hosting provider of every server (`only_missing`: just the ones not known yet)."""
    with session() as db:
        targets = [(s.id, s.host, s.country, s.role) for s in db.scalars(select(Server))
                   if not only_missing or (s.role == "foreign" and not s.country) or not ((s.extra or {}).get("geo") or {}).get("provider_at")]

    changes = []

    for sid, host, old, role in targets:
        loc = lookup(host)

        with session() as db:
            s = db.get(Server, sid)

            if s is None:
                continue

            d = dict(s.extra or {})
            g = dict(d.get("geo") or {})

            if loc["provider"] or loc["domain"]:
                g.update(provider=loc["provider"], domain=loc["domain"], asn=loc["asn"])

            g["provider_at"] = int(time.time())

            if role == "foreign" and loc["country_code"] and not (loc["sources"] < 2 and old):
                if s.country != loc["country_code"]:
                    changes.append({"name": s.name, "from": s.country, "to": loc["country_code"], "city": loc["city"]})

                s.country = loc["country_code"]
                g.update(country=loc["country"], city=loc["city"], agree=f"{loc['sources']}/{loc['total']}", at=int(time.time()))
            elif loc["country"]:
                g.setdefault("country", loc["country"])
                g.setdefault("city", loc["city"])

            d["geo"] = g
            s.extra = d

    return changes


def geo_missing_job() -> None:
    """Fill in the country of foreign servers that have none and the provider of servers that have none (runs once an hour)."""
    geo_refresh(only_missing=True)


# ------------------------------------------------------------------ "filtered in Iran?" check of a foreign server
def parse_ping(out: str) -> tuple[int, int, float | None]:
    m = re.search(r"(\d+) packets transmitted, (\d+) (?:packets )?received", out)
    tx, rx = (int(m.group(1)), int(m.group(2))) if m else (4, 0)
    a = re.search(r"= [\d.]+/([\d.]+)/", out)
    return tx, rx, (round(float(a.group(1)), 1) if a else None)


def _own_rows(host: str) -> list[dict]:
    with session() as db:
        iran = [(s.name, Node(s.host, s.ssh_user, s.ssh_port)) for s in db.scalars(select(Server).where(Server.role == "iran", Server.enabled.is_(True), Server.online.is_(True)))]

    def one(item):
        name, node = item
        row = {"node": name, "city": name, "asn": "your server", "ok": 0, "total": 4, "avg_ms": None, "pending": False}

        try:
            tx, rx, avg = parse_ping(run_script(node, "ping_host", {"HOST": host}, timeout=30))
            row.update(total=tx or 4, ok=rx, avg_ms=avg)
        except (SSHError, ValueError):
            row["pending"] = True

        return row

    with ThreadPoolExecutor(8) as ex:
        return list(ex.map(one, iran))


def _checkhost_rows(host: str, wait: int = 28) -> list[dict]:
    base = "https://check-host.net"
    h = {"Accept": "application/json", "User-Agent": "nikatel-panel"}
    nodes = httpx.get(base + "/nodes/hosts", headers=h, timeout=15).json().get("nodes", {})
    iran = {k: v for k, v in nodes.items() if k.startswith("ir")}

    if not iran:
        raise RuntimeError("check-host.net lists no Iranian nodes right now")

    start = httpx.get(base + "/check-ping", params=[("host", host)] + [("node", k) for k in sorted(iran)], headers=h, timeout=20).json()
    rid = start.get("request_id")

    if not rid:
        raise RuntimeError(start.get("error") or "check-host.net refused the request")

    deadline = time.time() + wait
    res: dict = {}

    while time.time() < deadline:
        time.sleep(2.5)
        res = httpx.get(f"{base}/check-result/{rid}", headers=h, timeout=15).json()

        if all(res.get(k) is not None for k in iran):
            break

    rows = []

    for k in sorted(iran):
        meta = iran[k]
        loc = meta.get("location") or []
        out = res.get(k)
        row = {"node": k, "city": loc[2] if len(loc) > 2 else k, "asn": meta.get("asn", ""), "ok": 0, "total": 0, "avg_ms": None, "pending": out is None}

        if out and isinstance(out, list) and out[0]:
            times = []

            for p in out[0]:
                row["total"] += 1

                if isinstance(p, list) and p and p[0] == "OK":
                    row["ok"] += 1

                    try:
                        times.append(float(p[1]) * 1000)
                    except (TypeError, ValueError, IndexError):
                        pass

            if times:
                row["avg_ms"] = round(sum(times) / len(times), 1)
        elif out is not None:
            row["total"] = 4

        rows.append(row)

    return rows


def verdict(rows: list[dict]) -> tuple[str, str]:
    """A server is filtered when MANY Iranian locations cannot reach it, not when a few lose a ping: a lost packet or one weak ISP route is normal
    (a clean address shows 1-3 of 21 locations with some loss). Per location: dead = no reply, bad = half or more of the pings lost, weak = any loss."""
    done = [r for r in rows if not r["pending"]]
    weak = [r for r in done if r["ok"] < r["total"]]
    bad = [r for r in done if r["ok"] * 2 <= r["total"]]
    dead = [r for r in done if r["ok"] == 0]

    if not done:
        return "unknown", "No answer from any Iranian location"

    if len(dead) == len(done):
        return "blocked", "Blocked in Iran (0 replies everywhere)"

    if len(bad) / len(done) >= 0.4 or len(dead) >= max(3, 0.25 * len(done)):
        return "filtered", f"Filtered in Iran - {len(bad)} of {len(done)} locations lose half or more of the packets" + (f" ({len(dead)} fully blocked)" if dead else "")

    note = f"; {len(weak)} lose a few packets (a weak route of that ISP, not a filter)" if weak else ""
    return "clean", f"Not filtered - {len(done) - len(bad)} of {len(done)} Iranian locations answer well{note}"


def parse_gre_links(out: str) -> list[dict]:
    """GRE lines of the scan_tunnels script -> [{iface, addr, peer, unit}]; peer = the other host of the link's /30."""
    import ipaddress

    links = []

    for ln in str(out).splitlines():
        p = ln.split()

        if p and p[0] == "GRE" and len(p) >= 3 and "/" in p[2]:
            try:
                itf = ipaddress.ip_interface(p[2])
                peer = next((str(h) for h in itf.network.hosts() if h != itf.ip), "")
            except ValueError:
                continue

            links.append({"iface": p[1], "addr": str(itf.ip), "peer": peer, "unit": p[-1] if len(p) >= 5 else "none"})

    return links


def gre_check(server_id: int) -> dict:
    """Is GRE fine on this server: every GRE link it has is up and the other end of each answers 8 of 10 pings.
    The answer is kept in the server's extra data so the list can show it."""
    with session() as db:
        s = db.get(Server, server_id)

        if s is None:
            raise ValueError("server not found")

        node, name = Node(s.host, s.ssh_user, s.ssh_port), s.name

    links = parse_gre_links(run_script(node, "scan_tunnels", timeout=30))
    bad = []

    for ln in links:
        try:
            got = re.search(r"(\d+) received", run_script(node, "gre_verify", {"IFACE": ln["iface"], "PEER": ln["peer"]}, timeout=40))
            ok = bool(got and int(got.group(1)) >= 8)
        except (SSHError, ValueError):
            ok = False

        if not ok:
            bad.append(ln["iface"])

    res = {"ok": bool(links) and not bad, "links": len(links), "bad": bad, "at": int(time.time()),
           "detail": "no GRE link on this server" if not links else ("GRE not OK: " + ", ".join(bad)) if bad else f"{len(links)} GRE link(s) answer"}

    with session() as db:
        s = db.get(Server, server_id)

        if s is not None:
            d = dict(s.extra or {})
            d["gre"] = res
            s.extra = d
            emit(db, "ok" if res["ok"] else "warn", "servers", f"GRE check of {name}: {res['detail']}.", name)

    return res


def _probe_pair(tester: dict, target: dict) -> dict:
    """One GRE reach test between two servers: a temporary GRE link (a small private network from 10.253.x.x) is made on BOTH, the tester pings the target's inner address,
    and the link is removed again - always, also when something fails in between. Nothing stays on either server."""
    import ipaddress
    import random

    rnd = random.SystemRandom()
    name, key = f"nkgp{rnd.randint(0, 99)}", rnd.randint(100000, 999999)
    net = ipaddress.ip_network(f"10.253.{rnd.randint(0, 255)}.{rnd.randrange(0, 256, 4)}/30")
    ip_t, ip_s = (str(h) for h in list(net.hosts())[:2])
    nt, ns = Node(tester["host"], tester["user"], tester["port"]), Node(target["host"], target["user"], target["port"])
    res = {"tester": tester["name"], "target": target["name"], "ok": False, "received": 0, "detail": ""}

    try:
        run_action(nt, "gre_probe_up", {"NAME": name, "LOCAL": tester["host"], "REMOTE": target["host"], "KEY": str(key), "ADDR": f"{ip_t}/30"}, timeout=40)
        run_action(ns, "gre_probe_up", {"NAME": name, "LOCAL": target["host"], "REMOTE": tester["host"], "KEY": str(key), "ADDR": f"{ip_s}/30"}, timeout=40)
        got = re.search(r"(\d+) received", run_script(nt, "gre_verify", {"IFACE": name, "PEER": ip_s}, timeout=40))
        res["received"] = int(got.group(1)) if got else 0
        res["ok"] = res["received"] >= 8
        res["detail"] = (f"{res['received']} of 10 pings came back through a temporary GRE link from {tester['name']}" if res["received"]
                         else f"no ping came back through a temporary GRE link from {tester['name']}: GRE (protocol 47) does not pass between the two, or a firewall in front of one of them drops it")
    except (SSHError, ValueError) as e:
        res["detail"] = f"the test could not run from {tester['name']}: {str(e)[:120]}"
    finally:
        for node in (nt, ns):        # a half-made link is removed as well: the removal does nothing where nothing was made
            try:
                run_action(node, "gre_probe_down", {"NAME": name}, timeout=30)
            except (SSHError, ValueError):
                log.warning("gre probe: could not remove %s on %s", name, node.host)

    return res


def gre_probe(server_id: int) -> dict:
    """Does GRE work to this server? Not 'does it have a GRE link': an Iran server of the nodes (another one when this IS an Iran server) builds a temporary GRE link
    to it, pings the inner address and removes the link. The first Iran server that gets through is enough; when it fails, a second one is tried to tell a route problem
    from a problem of one server. The answer is kept in the server's extra data so the list can show a mark."""
    with session() as db:
        s = db.get(Server, server_id)

        if s is None:
            raise ValueError("server not found")

        me = {"id": s.id, "name": s.name, "host": s.host, "user": s.ssh_user, "port": s.ssh_port}
        iran_side = s.role == "iran"
        others = [{"id": x.id, "name": x.name, "host": x.host, "user": x.ssh_user, "port": x.ssh_port}
                  for x in db.scalars(select(Server).where(Server.role == ("foreign" if iran_side else "iran"), Server.enabled.is_(True), Server.id != server_id).order_by(Server.name)) if x.online is not False]

    if not others:
        raise ValueError("no foreign server is online to test toward: add or enable one in Servers → Foreign" if iran_side else "no Iran server is online to test from: add or enable one in Servers → Iran")

    # an Iran server is tested the way it is used - FROM Iran TOWARD abroad (the owner, 2026-10-19: "the Iran server must test toward a foreign server, not toward an Iran server"); a foreign server is tested from an Iran server
    pairs = [(me, o) for o in others] if iran_side else [(o, me) for o in others]
    tried = []

    for tester, target in pairs[:2]:
        r = _probe_pair(tester, target)
        tried.append(r)

        if r["ok"]:
            break

    ok = any(r["ok"] for r in tried)
    detail = tried[-1]["detail"] if ok else " ; ".join(r["detail"] for r in tried)
    peer = lambda r: r["target"] if iran_side else r["tester"]
    target = me
    res = {"ok": ok, "links": len(tried), "bad": [] if ok else [peer(r) for r in tried], "at": int(time.time()), "detail": detail, "via": peer(tried[-1]), "mode": "probe", "tried": tried}

    with session() as db:
        s = db.get(Server, server_id)

        if s is not None:
            d = dict(s.extra or {})
            d["gre"] = res
            s.extra = d
            emit(db, "ok" if ok else "warn", "servers", f"GRE test of {target['name']}: {detail}.", target["name"])

    return res


def gre_check_all(role: str = "") -> dict:
    """gre_check on every enabled server (of one role when given), ten at a time. Servers that do not answer count as failed."""
    with session() as db:
        stmt = select(Server).where(Server.enabled.is_(True))

        if role in ("iran", "foreign"):
            stmt = stmt.where(Server.role == role)

        todo = [(s.id, s.name) for s in db.scalars(stmt)]

    def one(item):
        sid, name = item

        try:
            return {"server": name, **gre_check(sid)}
        except (SSHError, ValueError) as e:   # an unreachable server must not stop the others
            return {"server": name, "ok": False, "links": 0, "bad": [], "detail": f"check failed: {str(e)[:80]}"}

    with ThreadPoolExecutor(10) as ex:
        rows = list(ex.map(one, todo))

    ok = [r for r in rows if r["ok"]]
    nolinks = [r for r in rows if not r["ok"] and not r.get("links") and not r["detail"].startswith("check failed")]
    bad = [r for r in rows if not r["ok"] and r not in nolinks]
    return {"rows": rows, "total": len(rows), "ok": len(ok), "no_gre": len(nolinks), "bad": [f"{r['server']}: {r['detail']}" for r in bad]}


def _tcp_rows(host: str, port: int, limit: int = 5) -> tuple[int, int]:
    """(opened, asked): does a TCP connection to host:port open from up to `limit` of the Iran servers of the panel? Pings can be closed by a firewall; a connection that opens from Iran proves the address is not cut."""
    with session() as db:
        iran = [Node(s.host, s.ssh_user, s.ssh_port) for s in db.scalars(select(Server).where(Server.role == "iran", Server.enabled.is_(True), Server.online.is_(True)).order_by(Server.name))][:limit]

    def one(node: Node) -> bool | None:
        try:
            return f"REACH {host}:{int(port)} 1 " in run_script(node, "tcp_reach", {"TARGETS": f"{host}:{int(port)}"}, timeout=20)      # (the script of the configs: "REACH host:port 1 ms" when it opens)
        except (SSHError, ValueError):
            return None

    with ThreadPoolExecutor(5) as ex:
        got = [g for g in ex.map(one, iran) if g is not None]

    return sum(1 for g in got if g), len(got)


def tcp_override(level: str, text: str, opened: int, asked: int) -> tuple[str, str]:
    """Pure: a verdict of "blocked" / "filtered" that came from PINGS only is taken back when a TCP connection opens from at least half of the Iran servers asked (the owner, 2026-10-19: "this server shows filtered, a public site
    says it is not"; its tunnel ran): the server does not answer ping (ICMP is closed on it), which is not a filter."""
    if level in ("blocked", "filtered") and asked and opened * 2 >= asked:
        return "clean", f"Not filtered - a TCP connection opens from {opened} of {asked} Iran servers; the server does not answer ping (ICMP is closed on it), which is not a filter"

    return level, text


def filter_check(server_id: int, wait: int = 28) -> dict:
    _node, name, _role, host = _srv(server_id)
    rows = _own_rows(host)
    note = ""

    try:
        rows += _checkhost_rows(host, wait)
    except (httpx.HTTPError, RuntimeError, ValueError) as e:
        note = "check-host.net unavailable (" + str(e)[:60] + ")"

    level, text = verdict(rows)

    if level in ("blocked", "filtered"):
        try:
            level, text = tcp_override(level, text, *_tcp_rows(host, _node.port))
        except (SSHError, ValueError, RuntimeError):
            pass                                                           # (the second look could not be made: the ping verdict stands)

    res = {"checked_at": int(time.time()), "level": level, "verdict": text, "nodes": rows, "source": "your Iran servers" + ("" if note else " + check-host.net"), "note": note}
    _set_extra(server_id, iran_filter=res)

    with session() as db:
        emit(db, "bad" if level in ("blocked", "filtered") else "ok" if level == "clean" else "warn", "nodes", f"Filter check of {name}: {text}.", name)

    return res


# ------------------------------------------------------------------ spoof test: does the provider of a server let packets with a FORGED source address out?
SPOOF_CONTROL = 5          # ordinary pings first: they prove that ICMP reaches the other server at all
SPOOF_COUNT = 10           # the same pings with the forged source
SPOOF_WINDOW = 22          # seconds the other server listens


def spoof_verdict(sources: list[str], spoof_ip: str, count: int = SPOOF_COUNT, control: int = SPOOF_CONTROL) -> tuple[str, str, int, int]:
    """Pure: (level, a sentence, forged, real) from the source addresses the receiving server saw on the test pings (the owner's method, 2026-10-17: "tcpdump on the other server: if the packets came with the forged
    address, the server takes spoofs; if nothing came, it does not"). The ordinary pings sent first tell a provider that drops FORGED sources from one that drops ping altogether, which tcpdump alone cannot."""
    forged = sum(1 for s in sources if s == spoof_ip)
    real = len(sources) - forged
    half = (count + 1) // 2

    if forged >= half:
        return "yes", f"This server can spoof: {forged} of {count} forged packets arrived with the source {spoof_ip} (the {real} ordinary ones arrived too).", forged, real

    if forged:
        return "part", f"Only some forged packets get through ({forged} of {count}): the provider drops part of them. Try another address to forge.", forged, real

    if real >= control + half:
        return "norewrite", "The forged pings arrived with the REAL source: the rewrite did not happen on the sending server (no NAT support?). Nothing can be said.", forged, real

    if real:
        return "no", f"This server does not spoof: the {real} ordinary pings arrived, none of the {count} forged ones did - the provider drops packets with a forged source.", forged, real

    return "blind", "Nothing arrived at the other server - not even the ordinary pings: ICMP is filtered between the two (or tcpdump saw nothing). Try another target server.", forged, real


def parse_spoof_listen(out: str) -> tuple[list[str], str]:
    """Pure: (the source of every test ping that arrived, a problem sentence or "") from the answer of `spoof_listen`."""
    text = str(out or "")

    if "NOTCPDUMP" in text:
        return [], "tcpdump is not installed on the other server: install it (apt install tcpdump) and test again"

    if "LISTENING" not in text:
        return [], "the other server did not start listening"

    return [m for m in re.findall(r"^PKT (\d{1,3}(?:\.\d{1,3}){3})\s*$", text, re.MULTILINE)], ""


def _spoof_ready(node: Node, role: str, name: str) -> str:
    """Make a server ready for its part of the spoof test. What it needs (iptables and ping to send, tcpdump to listen) is installed with apt when it is missing - the owner started the test. Returns the packages that were
    installed ("" when nothing was missing); a ValueError says what is still missing when apt could not."""
    try:
        out = run_action(node, "spoof_prepare", {"ROLE": role}, timeout=300)
    except (SSHError, ValueError) as e:
        raise ValueError(f"the test could not run on {name}: {str(e)[:160]}") from None

    for word, why in (("NOIPTABLES", "iptables is not installed on the sending server and could not be installed (no apt, or apt failed): install it by hand, apt install iptables"), ("NOPING", "ping is not installed on the sending server and could not be installed: apt install iputils-ping"),
                      ("NOTCPDUMP", "tcpdump is not installed on the other server and could not be installed: apt install tcpdump")):
        if word in out:
            raise ValueError(f"{name}: {why}")

    tried = re.search(r"^TRIED (.+)$", out, re.MULTILINE)
    return tried.group(1).strip() if tried and "READY" in out else ""


def spoof_test(server_id: int, target_id: int, spoof_ip: str) -> dict:
    """Does the provider of this server let packets with a forged source address out? This server pings the target five times as it is and ten times with the source rewritten to `spoof_ip` (a tagged SNAT rule for ICMP echo
    requests to the target only, taken out again whatever happens); the target listens with tcpdump and says what arrived. The answer is kept in the extra data of the server."""
    import ipaddress
    import threading

    try:
        ip = ipaddress.IPv4Address(str(spoof_ip).strip())
    except ValueError:
        raise ValueError("the forged source must be an IPv4 address, for example 10.10.10.10") from None

    if ip.is_loopback or ip.is_multicast or ip.is_unspecified or ip.is_reserved or ip.is_link_local:
        raise ValueError("choose an ordinary address to forge (not loopback, multicast, link-local or reserved)")

    if server_id == target_id:
        raise ValueError("choose another server as the target")

    with session() as db:
        s, t = db.get(Server, server_id), db.get(Server, target_id)

        if s is None or t is None:
            raise ValueError("server not found")

        for x in (s, t):
            if not x.enabled or x.online is False:
                raise ValueError(f"{x.name} is off or does not answer")

        if str(ip) in (s.host, t.host):
            raise ValueError("the forged source must not be the address of one of the two servers")

        ns, nt = Node(s.host, s.ssh_user, s.ssh_port), Node(t.host, t.ssh_user, t.ssh_port)
        sender, target, target_host = s.name, t.name, t.host

    ready = {"send": _spoof_ready(ns, "send", sender), "listen": _spoof_ready(nt, "listen", target)}          # (what is missing is installed first, before the listener starts: a slow apt must not eat its listening time)
    got: dict = {}

    def listen() -> None:
        try:
            got["rx"] = run_script(nt, "spoof_listen", {"SECS": str(SPOOF_WINDOW)}, timeout=SPOOF_WINDOW + 30)
        except (SSHError, ValueError) as e:
            got["err"] = f"{target} could not listen: {str(e)[:140]}"

    th = threading.Thread(target=listen, daemon=True)
    th.start()
    time.sleep(3)                                                      # (tcpdump needs a moment to start)

    try:
        tx = run_action(ns, "spoof_send", {"TARGET": target_host, "SPOOF": str(ip), "CONTROL": str(SPOOF_CONTROL), "COUNT": str(SPOOF_COUNT)}, timeout=70)
    except (SSHError, ValueError) as e:
        th.join(timeout=SPOOF_WINDOW + 30)
        raise ValueError(f"the test could not run on {sender}: {str(e)[:160]}") from None

    th.join(timeout=SPOOF_WINDOW + 30)

    for word, why in (("NOIPTABLES", "iptables is not installed on the sending server"), ("NOPING", "ping is not installed on the sending server"), ("RULEFAIL", "the sending server refused the NAT rule (no iptables nat support)")):
        if word in tx:
            raise ValueError(f"{sender}: {why}")

    if "err" in got:
        raise ValueError(got["err"])

    sources, problem = parse_spoof_listen(got.get("rx", ""))

    if problem:
        raise ValueError(problem)

    level, text, forged, real = spoof_verdict(sources, str(ip))
    left = re.search(r"^LEFT (\d+)", tx, re.MULTILINE)
    note = "".join(f" ({pk.strip()} was missing on {name} and was installed for this test.)" for name, pk in ((sender, ready["send"]), (target, ready["listen"])) if pk)
    note += " The temporary NAT rule could not be removed: run  iptables -t nat -S POSTROUTING | grep nikatel-spoof-test  on the sending server and delete it." if left and int(left.group(1)) else ""
    res = {"can": level in ("yes", "part"), "level": level, "verdict": text + note, "forged": forged, "real": real, "sent": SPOOF_COUNT, "control": SPOOF_CONTROL, "spoof_ip": str(ip), "target": target, "target_id": target_id, "at": int(time.time())}
    _set_extra(server_id, spoof=res)

    with session() as db:
        emit(db, "ok" if res["can"] else "warn", "servers", f"Spoof test {sender} -> {target} (source {ip}): {text}", sender)

    return res


SPOOF_TRIES = 3             # the targets the automatic spoof test tries at most


def spoof_candidates(sender: dict, servers: list[dict]) -> list[int]:
    """Pure: the ids of the servers the spoof test may use as the listening target, best first: servers of the OTHER kind (a foreign server for an Iran sender - the owner, 2026-10-19: "it must bring a foreign one") that
    are on, those that were proven listeners in anyone's last test first, then by name. Servers of the sender's own kind only when no other kind is there. `servers`: {"id", "name", "role", "enabled", "online", "spoof_target_ok"}."""
    live = [x for x in servers if x["id"] != sender["id"] and x["enabled"] and x["online"] is not False]
    other = [x for x in live if x["role"] != sender["role"]] or live
    return [x["id"] for x in sorted(other, key=lambda x: (not x.get("spoof_target_ok"), str(x["name"])))]


def spoof_auto(server_id: int, spoof_ip: str) -> dict:
    """The spoof test with the target chosen BY THE SYSTEM (no list for the owner to pick from): the candidates of `spoof_candidates` one after the other - at most SPOOF_TRIES - until a test gives an answer about THIS server
    (it spoofs / only in part / it does not / the rewrite did not happen). A target that cannot listen or that no ping reaches (ICMP filtered between the two) is left and the next one is tried. The answer says which target it came from."""
    with session() as db:
        s = db.get(Server, server_id)

        if s is None:
            raise ValueError("server not found")

        rows = db.scalars(select(Server)).all()
        proven = {int((x.extra or {}).get("spoof", {}).get("target_id") or 0) for x in rows if ((x.extra or {}).get("spoof") or {}).get("can")}
        servers = [{"id": x.id, "name": x.name, "role": x.role, "enabled": x.enabled, "online": x.online, "spoof_target_ok": x.id in proven} for x in rows]
        sender = {"id": s.id, "role": s.role}

    ids = spoof_candidates(sender, servers)

    if not ids:
        raise ValueError("another server that is on is needed as the target")

    names = {x["id"]: x["name"] for x in servers}
    failed: list[str] = []

    for tid in ids[:SPOOF_TRIES]:
        try:
            res = spoof_test(server_id, tid, spoof_ip)
        except ValueError as e:
            if names[server_id] in str(e) and "listen" not in str(e):
                raise                                                      # (a problem of the SENDER - no iptables, the test could not run on it, it is off - is the same with every target)

            failed.append(f"{names[tid]}: {str(e)[:120]}")
            continue

        if res["level"] == "blind":
            failed.append(f"{names[tid]}: {res['verdict'][:100]}")
            continue

        return {**res, "tried_targets": [names[tid]] if not failed else [x.split(":")[0] for x in failed] + [names[tid]]}

    raise ValueError("no target could be used - " + " | ".join(failed))


def filter_daily_job() -> None:
    """Every foreign server once a day (the job runs hourly and only does work when the last check is older than a day)."""
    with session() as db:
        due = [s.id for s in db.scalars(select(Server).where(Server.role == "foreign", Server.enabled.is_(True)))
               if int(((s.extra or {}).get("iran_filter") or {}).get("checked_at", 0)) < time.time() - 86400]

    for sid in due[:12]:
        try:
            filter_check(sid, wait=20)
        except (RuntimeError, ValueError, SSHError) as e:
            log.warning("filter check %s failed: %s", sid, e)

        time.sleep(5)


def where(host: str) -> dict:
    """The role of a server from where it is: Iran -> `iran`, any other country -> `foreign`, no answer -> '' (the operator is asked)."""
    try:
        g = lookup(host, want_provider=False)
    except Exception as e:  # noqa: BLE001  the geo services are outside: no answer is an answer
        log.warning("where %s: %s", host, str(e)[:100])
        return {"country_code": "", "country": "", "city": "", "role": "", "sources": 0}

    code = (g.get("country_code") or "").upper()
    return {"country_code": code, "country": g.get("country") or "", "city": g.get("city") or "", "role": "iran" if code == "IR" else ("foreign" if code else ""), "sources": g.get("sources", 0)}


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


def set_busy(server_id: int, what: str = "", detail: str = "") -> None:
    """What the panel is doing with this server right now (connecting, installing): shown in the list instead of a dash. Empty `what` clears it."""
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
    return (get_settings().ssh_key.with_suffix(".pub") if get_settings().ssh_key.suffix else get_settings().ssh_key.parent / (get_settings().ssh_key.name + ".pub")).read_text().strip()


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
                            f"{node.user}@{host}", script], check=False, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, timeout=30, env=env)
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

def add_many(role: str, lines: str, password: str = "", ssh_user: str = "root", ssh_port: int = 22) -> list[dict]:
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
        port = int(p[3]) if len(p) > 3 and p[3].isdigit() else int(ssh_port)

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


def abuse(server_id: int, action: str) -> dict:
    node, name, role, _host = _srv(server_id)

    if action == "install":
        if role != "foreign":
            raise ValueError("Abuse-Defender goes on foreign servers only, never on Iran servers")

        out = run_action(node, "abuse_install", {"URL": ABUSE_LIST_URL, "WL": _whitelist()}, timeout=300)
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


def reboot(server_id: int) -> None:
    node, name, _role, _host = _srv(server_id)
    run_action(node, "reboot", timeout=20)

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

    first = lambda key: next((s[key] for s in src if s.get(key)), "")   # noqa: E731
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
    res = {"tester": tester["name"], "ok": False, "received": 0, "detail": ""}

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

        target = {"id": s.id, "name": s.name, "host": s.host, "user": s.ssh_user, "port": s.ssh_port}
        testers = [{"id": x.id, "name": x.name, "host": x.host, "user": x.ssh_user, "port": x.ssh_port}
                   for x in db.scalars(select(Server).where(Server.role == "iran", Server.enabled.is_(True), Server.id != server_id).order_by(Server.name)) if x.online is not False]

    if not testers:
        raise ValueError("no other Iran server is online to test from: add or enable one in Servers → Iran")

    tried = []

    for t in testers[:2]:
        r = _probe_pair(t, target)
        tried.append(r)

        if r["ok"]:
            break

    ok = any(r["ok"] for r in tried)
    detail = tried[-1]["detail"] if ok else " ; ".join(r["detail"] for r in tried)
    res = {"ok": ok, "links": len(tried), "bad": [] if ok else [r["tester"] for r in tried], "at": int(time.time()), "detail": detail, "via": tried[-1]["tester"], "mode": "probe", "tried": tried}

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


def filter_check(server_id: int, wait: int = 28) -> dict:
    _node, name, _role, host = _srv(server_id)
    rows = _own_rows(host)
    note = ""

    try:
        rows += _checkhost_rows(host, wait)
    except (httpx.HTTPError, RuntimeError, ValueError) as e:
        note = "check-host.net unavailable (" + str(e)[:60] + ")"

    level, text = verdict(rows)
    res = {"checked_at": int(time.time()), "level": level, "verdict": text, "nodes": rows, "source": "your Iran servers" + ("" if note else " + check-host.net"), "note": note}
    _set_extra(server_id, iran_filter=res)

    with session() as db:
        emit(db, "bad" if level in ("blocked", "filtered") else "ok" if level == "clean" else "warn", "nodes", f"Filter check of {name}: {text}.", name)

    return res


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


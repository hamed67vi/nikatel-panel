"""The L2TP product (1.69.241): a VPN account (L2TP over IPsec, no app on the phone) the customer gets from the shop. The account manager `nk-l2tp` runs on the ENTRY - an Iran server with strongSwan + xl2tpd - and
keeps the accounts (a name, a password, how many devices at once, an end date, a speed); the traffic leaves through the EXIT, a foreign server, over a tunnel of ours. This module drives that manager over SSH through two
reviewed reads (`l2tp_state`, `l2tp_exit_state`) and ONE reviewed action (`l2tp_cmd`: a word of a short list and its arguments, handed to the manager one by one, never through a shell), and builds the text a customer is sent.
The shop bot reaches the same manager by its own restricted key; the accounts it makes are named with a prefix (`bot_prefix`, "l2_") and the page marks them. Nothing here installs the entry or the exit."""
from __future__ import annotations

import base64
import json
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from ..db import session
from ..log import get_logger
from ..models import Server, Tunnel, is_protected
from ..ssh import Node, run_action, run_script
from . import settings as st

log = get_logger("l2tp")

NAME = re.compile(r"^[a-z][a-z0-9_]{2,23}$")                       # the manager's own rules: lower case, 3-24, starts with a letter
PASS = re.compile(r"^[A-Za-z0-9._~@#%+=!-]{6,64}$")
ADDRESS = re.compile(r"^[A-Za-z0-9][A-Za-z0-9.-]{0,252}$")
DAY = 86400
TEHRAN = 12600                                                    # +03:30: the end date is told in the owner's day
MAX_MBIT = 1000.0
MAX_DAYS = 3650
CONTROL = re.compile(r"[\x00-\x1f\x7f]")


def conf() -> dict:
    return st.get("l2tp")


def _clean_text(v, what: str, lo: int, hi: int) -> str:
    s = str(v or "").strip()

    if CONTROL.search(s) or not lo <= len(s) <= hi:
        raise ValueError(f"{what} must be {lo}-{hi} characters, without line breaks" if lo else f"{what} may be at most {hi} characters, without line breaks")

    return s


def choices() -> dict:
    """What the settings form offers: the Iran servers (entries), the foreign servers (exits) and the tunnels (names only)."""
    with session() as db:
        servers = db.query(Server).order_by(Server.name).all()
        tunnels = db.query(Tunnel).order_by(Tunnel.name).all()
        return {"iran": [{"id": s.id, "name": s.name} for s in servers if s.role == "iran"],
                "foreign": [{"id": s.id, "name": s.name} for s in servers if s.role == "foreign"],
                "tunnels": [{"id": t.id, "name": t.name, "iran": t.iran.name if t.iran else "", "foreign": t.foreign.name if t.foreign else ""} for t in tunnels]}


def set_conf(entry_id: int, exit_id: int, tunnel_id: int, title: str, address: str, direct_id: int = 0) -> dict:
    """Which servers carry the product and how the customer sees it. The entry must be an Iran server, the exit a foreign one (0 = not told); `direct_id` (1.69.257) = the optional second entry with a direct
    address, a foreign server that is an entry and the way out at once. The tunnel's row in the Tunnel manager is labelled L2TP at once."""
    title = _clean_text(title, "the connection name", 1, 40)
    address = str(address or "").strip()

    if address and not ADDRESS.match(address):
        raise ValueError("the address shown to the customer is a name or an IP, nothing else")

    with session() as db:
        if entry_id:
            s = db.get(Server, entry_id)

            if s is None or s.role != "iran":
                raise ValueError("the entry must be one of your Iran servers")

        for sid, what in ((exit_id, "the exit"), (direct_id, "the direct server")):
            if sid:
                s = db.get(Server, sid)

                if s is None or s.role != "foreign":
                    raise ValueError(f"{what} must be one of your foreign servers")

        if direct_id and direct_id == entry_id:
            raise ValueError("the direct server cannot be the entry itself")

        if tunnel_id and db.get(Tunnel, tunnel_id) is None:
            raise ValueError("no such tunnel")

    c = st.put("l2tp", {"entry_id": int(entry_id), "exit_id": int(exit_id), "tunnel_id": int(tunnel_id), "direct_id": int(direct_id), "title": title, "address": address})

    from . import purpose

    purpose.label_l2tp()
    return c


def _node(sid: int, what: str) -> Node:
    if not sid:
        raise ValueError(f"choose the {what} server first (the gear on this page)")

    with session() as db:
        s = db.get(Server, sid)

        if s is None:
            raise ValueError(f"the {what} server is gone from the panel: choose it again")

        return Node(s.host, s.ssh_user, s.ssh_port)


def _entry_host() -> str:
    with session() as db:
        s = db.get(Server, conf()["entry_id"])
        return s.host if s is not None else ""


# ------------------------------------------------------------------ reading (pure parsers + one SSH read each)
def _json(text: str):
    try:
        return json.loads(text)
    except ValueError:
        return None


def parse_state(out: str) -> dict:
    """The output of the `l2tp_state` read."""
    d: dict = {"installed": False, "units": {}, "udp": 0, "load": None, "info": {}, "accounts": [], "sessions": [], "list_ok": False}

    for ln in out.splitlines():
        key, _, rest = ln.partition(" ")
        rest = rest.strip()

        if key == "L2TP":
            d["installed"] = rest == "ok"
        elif key == "UNIT":
            a = rest.split()

            if len(a) == 2:
                d["units"][a[0]] = a[1]
        elif key == "UDP":
            d["udp"] = int(rest) if rest.isdigit() else 0
        elif key == "LOAD":
            try:
                d["load"] = float(rest)
            except ValueError:
                pass
        elif key == "INFO":
            v = _json(rest)
            d["info"] = v if isinstance(v, dict) else {}
        elif key == "LIST":
            v = _json(rest)

            if isinstance(v, dict) and isinstance(v.get("accounts"), list):
                d["accounts"], d["list_ok"] = v["accounts"], True
        elif key == "SESS":
            v = _json(rest)
            d["sessions"] = v.get("sessions", []) if isinstance(v, dict) else []

    return d


def parse_exit(out: str) -> dict:
    """The output of the `l2tp_exit_state` read."""
    d: dict = {"installed": False, "routes": [], "rules": 0, "forward": False, "timer": "unknown"}

    for ln in out.splitlines():
        key, _, rest = ln.partition(" ")
        rest = rest.strip()

        if key == "EXIT":
            d["installed"] = rest == "ok"
        elif key == "ROUTES":
            d["routes"] = [r.strip() for r in rest.split(";") if r.strip()]
        elif key == "RULES":
            d["rules"] = int(rest) if rest.isdigit() else 0
        elif key == "FORWARD":
            d["forward"] = rest == "1"
        elif key == "TIMER":
            d["timer"] = rest or "unknown"

    return d


BOT_NOTE = re.compile(r"^order \d+ user \d+")                       # the note the shop bot writes on every account it makes


def made_by_bot(name: str, note: str, prefix: str) -> bool:
    """An account of the shop bot: its note is `order N user U` (the bot's own mark, whatever the name), or its name starts with the bot's prefix (the older `l2_` ones)."""
    return bool(BOT_NOTE.match(note or "")) or (bool(prefix) and name.startswith(prefix))


def shape_account(a: dict, now: float, prefix: str) -> dict:
    """One account as the page shows it (no password)."""
    exp = a.get("expires")
    name = str(a.get("user") or "")
    rx, tx = int(a.get("rx") or 0), int(a.get("tx") or 0)
    state = "disabled" if a.get("disabled") else "expired" if exp is not None and exp <= now else "over" if a.get("over") else "active"
    return {"user": name, "limit": a.get("limit", 1), "online": int(a.get("online") or 0), "expires": exp, "days_left": None if exp is None else round((exp - now) / DAY, 1), "state": state,
            "down": a.get("down"), "up": a.get("up"), "rx": rx, "tx": tx, "quota": a.get("quota") or None, "used": int(a.get("used") or (rx + tx)), "over": bool(a.get("over")),
            "created": a.get("created"), "last_seen": a.get("last_seen"), "note": str(a.get("note") or "")[:120], "bot": made_by_bot(name, str(a.get("note") or ""), prefix)}


def _read_entry(c: dict, now: float) -> tuple[dict, list, list]:
    """One SSH read of the entry: its state, its accounts and its sessions. It never raises for a server that cannot be reached - the answer says why."""
    entry: dict = {"ok": False, "error": "", "installed": False}
    accounts: list = []
    sessions: list = []

    try:
        p = parse_state(run_script(_node(c["entry_id"], "entry"), "l2tp_state", timeout=40))
        entry.update(ok=p["installed"] and p["list_ok"], installed=p["installed"], units=p["units"], udp=p["udp"], load=p["load"], pool=p["info"].get("pool", ""), version=p["info"].get("version"), features=p["info"].get("features", []))
        entry["quota"] = "quota" in entry["features"]          # (an entry whose manager is older than version 3 cannot keep a traffic quota: the page says so)

        if not p["installed"]:
            entry["error"] = "the L2TP manager is not installed on this server"
        elif not p["list_ok"]:
            entry["error"] = "the manager did not answer"

        accounts = [shape_account(a, now, c["bot_prefix"]) for a in p["accounts"] if isinstance(a, dict)]
        sessions = [{"user": str(s.get("user") or ""), "start": s.get("start"), "ifname": str(s.get("ifname") or "")} for s in p["sessions"] if isinstance(s, dict)]
        first: dict = {}

        for s in sessions:                                                    # (since when an account has a device connected: the oldest session it has now)
            if s["user"] and isinstance(s["start"], (int, float)):
                first[s["user"]] = min(first.get(s["user"], s["start"]), s["start"])

        for a in accounts:
            a["since"] = first.get(a["user"])
    except (RuntimeError, ValueError, OSError) as e:                      # (an unreachable server, a refused login, a server that was removed: the page says it)
        entry["error"] = str(e)[:200]

    return entry, accounts, sessions


def _read_exit(c: dict) -> dict:
    """One SSH read of the exit: are its rules, routes and forwarding there."""
    ex: dict = {"ok": False, "error": "", "installed": False}

    try:
        p = parse_exit(run_script(_node(c["exit_id"], "exit"), "l2tp_exit_state", timeout=40))
        ex.update(installed=p["installed"], routes=p["routes"], rules=p["rules"], forward=p["forward"], timer=p["timer"], ok=p["installed"] and p["rules"] > 0 and p["forward"])

        if not p["installed"]:
            ex["error"] = "the L2TP exit is not installed on this server"
        elif not ex["ok"]:
            ex["error"] = "the exit's routes or rules are missing"
    except (RuntimeError, ValueError, OSError) as e:
        ex["error"] = str(e)[:200]
        ex["silent"] = True                                                   # (the server itself could not be asked: the next read does not wait for it)

    return ex


# The exit never holds the page (1.69.250). The owner, 2026-11-09: "I want to delete these accounts, they are not deleted" - they WERE deleted, but the exit server was off for the night, its SSH read took 30 seconds
# to give up, and the tab waited for it after every change (and when it opened), so the deleted rows stayed on the screen. Now the exit is read by a thread of its own, one read at a time: the page waits a few seconds
# for it; an exit that is slower answers into the NEXT read, and one that did not answer the last time is waited for only a moment.
EXIT_WAIT = 6.0
EXIT_WAIT_SILENT = 1.0
SILENT = "the server does not answer (the panel keeps trying)"
_EXIT: dict = {"id": 0, "job": None, "last": None}
_EXIT_LOCK = threading.Lock()


def forget_exit() -> None:
    """Another exit was chosen (or a test starts): what was known of the old one is dropped."""
    with _EXIT_LOCK:
        _EXIT.update(id=0, job=None, last=None)


def _exit_job(c: dict, job: dict) -> None:
    try:
        ex = _read_exit(c)
    except Exception as e:  # thread boundary: a read that breaks in a way nobody thought of must still end its job, or the exit would never be read again
        log.exception("l2tp: the read of the exit broke")
        ex = {"ok": False, "error": str(e)[:200] or type(e).__name__, "installed": False, "silent": True}

    with _EXIT_LOCK:
        job["out"] = ex

        if _EXIT["job"] is job:
            _EXIT["job"] = None
            _EXIT["last"] = ex

    job["done"].set()


def _exit_known(c: dict) -> dict | None:
    """The newest answer of this exit, if there is one (an answer that came after the page stopped waiting is seen by the next light read)."""
    with _EXIT_LOCK:
        return _EXIT["last"] if _EXIT["id"] == c["exit_id"] else None


def _exit_view(c: dict) -> dict:
    """The exit's state for a full read: a read is started unless one is already on its way, and the page waits for it only so long."""
    with _EXIT_LOCK:
        if _EXIT["id"] != c["exit_id"]:
            _EXIT.update(id=c["exit_id"], job=None, last=None)

        job, last = _EXIT["job"], _EXIT["last"]

        if job is None:
            job = _EXIT["job"] = {"done": threading.Event(), "out": None}
            threading.Thread(target=_exit_job, args=(dict(c), job), name="l2tp-exit", daemon=True).start()

    silent = bool(last and last.get("silent"))

    if job["done"].wait(EXIT_WAIT_SILENT if silent else EXIT_WAIT):
        return job["out"]

    if silent:
        return last

    late = {"ok": False, "error": SILENT, "installed": False, "silent": True}

    with _EXIT_LOCK:
        if _EXIT["job"] is job:                                               # (still being read: the next read waits only a moment for it)
            _EXIT["last"] = late

    return late


def state(base: dict | None = None) -> dict:
    """The page: the settings, the entry (services, accounts, who is online), the exit and the tunnel between them - each part answers for itself, a part that cannot be read says why and the rest still shows.
    The entry and the exit are two SSH reads that are made AT THE SAME TIME (1.69.244: one after the other, a slow Iran server held the whole tab for as long as both took - the owner, 2026-11-07: "it loads very late").
    With `base` (a full answer from a moment ago) only the ENTRY is read again and the exit is taken from it: that is the light read the open tab makes every few seconds to show who is connected (1.69.245).
    The exit never holds the answer for long (1.69.250, `_exit_view`): an exit that is off costs the page a moment, not the 30 seconds its SSH read takes to give up."""
    c = conf()
    now = time.time()
    out: dict = {"conf": c, "configured": bool(c["entry_id"]), "entry": None, "exit": None, "tunnel": None, "accounts": [], "sessions": [], "now": int(now)}
    keep_exit = base is not None and base.get("exit") is not None and base.get("conf", {}).get("exit_id") == c["exit_id"]

    with ThreadPoolExecutor(max_workers=1) as pool:
        fe = pool.submit(_read_entry, c, now) if c["entry_id"] else None

        if keep_exit:
            out["exit"] = _exit_known(c) or base["exit"]
        elif c["exit_id"]:
            out["exit"] = _exit_view(c)                                       # (its own thread, at the same time as the entry's read)

        if fe is not None:
            out["entry"], out["accounts"], out["sessions"] = fe.result()

    with session() as db:
        out["names"] = {}                                                     # (1.69.251: the strip names the servers - "pnt-a", not "Entry up")

        for k in ("entry", "exit", "direct"):
            s = db.get(Server, c.get(f"{k}_id") or 0) if c.get(f"{k}_id") else None
            out["names"][k] = s.name if s is not None else ""

        t = db.get(Tunnel, c["tunnel_id"]) if c["tunnel_id"] else None

        if t is not None:
            out["tunnel"] = {"id": t.id, "name": t.name, "state": t.state, "rtt_ms": t.rtt_ms, "since": t.state_since, "protected": is_protected(t), "error": t.last_error or ""}

    out["online"] = len(out["sessions"])
    return out


# ------------------------------------------------------------------ changing (one reviewed action each)
def _call(cmd: str, args: list[str]) -> dict:
    """One command of the manager on the entry: its one JSON line, or ValueError with the manager's own words."""
    b64 = base64.b64encode(json.dumps(args).encode()).decode()
    raw = run_action(_node(conf()["entry_id"], "entry"), "l2tp_cmd", {"CMD": cmd, "B64": b64}, timeout=60)
    lines = [ln for ln in raw.strip().splitlines() if ln.strip()]
    d = _json(lines[-1]) if lines else None

    if not isinstance(d, dict):
        d = {"error": "the entry did not answer in the expected way"}

    if d.get("error"):
        raise ValueError(str(d["error"]))

    return d


def _name(name: str) -> str:
    n = str(name or "").strip()

    if not NAME.match(n):
        raise ValueError("the account name is 3-24 characters: lower-case letters, digits and _, starting with a letter")

    return n


def _speed(v, what: str):
    """None = not given; 0 = no limit; else Mbit/s."""
    if v is None or v == "":
        return None

    try:
        x = float(v)
    except (TypeError, ValueError):
        raise ValueError(f"{what} is a number of Mbit/s (0 = no limit)")

    if x < 0 or x > MAX_MBIT or (0 < x < 0.1):
        raise ValueError(f"{what} is 0 (no limit) or 0.1 to {int(MAX_MBIT)} Mbit/s")

    return x


def _num(x: float) -> str:
    return str(int(x)) if float(x).is_integer() else str(x)


MAX_GB = 100000.0


def _quota(v):
    """None = not given; 0 = no quota; else GB (10^9 bytes, download + upload)."""
    if v is None or v == "":
        return None

    try:
        x = float(v)
    except (TypeError, ValueError):
        raise ValueError("the traffic is a number of GB (0 = no limit)")

    if x < 0 or x > MAX_GB or (0 < x < 0.01):
        raise ValueError(f"the traffic is 0 (no limit) or 0.01 to {int(MAX_GB)} GB")

    return x


def _gb_args(v) -> list[str]:
    q = _quota(v)
    return [] if q is None else ["--gb", _num(q)]


def _expires(v):
    """An end date as unix seconds, or None; a year back to ten years ahead."""
    if v is None:
        return None

    now = time.time()

    try:
        e = int(v)
    except (TypeError, ValueError):
        raise ValueError("the end date is not understood")

    if not now - 365 * DAY <= e <= now + 3650 * DAY:
        raise ValueError("the end date is from a year ago to ten years ahead")

    return e


def _speed_args(down, up) -> list[str]:
    if down is not None and up is not None and down == up:
        return ["--speed", _num(down)]

    out: list[str] = []

    if down is not None:
        out += ["--down", _num(down)]

    if up is not None:
        out += ["--up", _num(up)]

    return out


def _password(p) -> str:
    pw = str(p or "").strip()

    if pw and not PASS.match(pw):
        raise ValueError("the password is 6-64 characters: letters, digits and . _ ~ @ # % + = ! -")

    return pw


def add(name: str, days: int = 30, devices: int = 1, down=None, up=None, password: str = "", note: str = "", gb=None) -> dict:
    """A new account; the answer carries the password (made by the manager unless one is given)."""
    n = _name(name)

    if not 1 <= int(days) <= MAX_DAYS:
        raise ValueError(f"days is 1 to {MAX_DAYS}")

    if not 1 <= int(devices) <= 10:
        raise ValueError("devices is 1 to 10")

    args = [n, "--days", str(int(days)), "--limit", str(int(devices))] + _speed_args(_speed(down, "the download speed"), _speed(up, "the upload speed")) + _gb_args(gb)
    pw = _password(password)
    note = _clean_text(note, "the note", 0, 80)

    if pw:
        args += ["--password", pw]

    if note:
        args += ["--note", note]

    r = _call("add", args)
    log.info("l2tp: account %s added (%s days, %s devices)", n, days, devices)
    return r


def renew(name: str, days: int = 30, reset_usage: bool = False) -> dict:
    """Add days to the end date (from today, or from the end date if that is later); a disabled account comes back; with `reset_usage` the traffic is counted from zero again (a new period)."""
    if not 1 <= int(days) <= MAX_DAYS:
        raise ValueError(f"days is 1 to {MAX_DAYS}")

    return _call("renew", [_name(name), "--days", str(int(days))] + (["--reset-usage"] if reset_usage else []))


def change(name: str, password: str = "", devices=None, down=None, up=None, disabled=None, note=None, gb=None, reset_usage: bool = False, expires=None) -> dict:
    """Change what is given, nothing else: a password, the devices, a speed (0 = no limit), the traffic in GB (0 = no limit), the end date, on / off, the note, and with `reset_usage` the count of the traffic. The speed of an account that is online changes at once."""
    n = _name(name)
    args = [n]
    pw = _password(password)
    end = _expires(expires)

    if pw:
        args += ["--password", pw]

    if devices is not None:
        if not 1 <= int(devices) <= 10:
            raise ValueError("devices is 1 to 10")

        args += ["--limit", str(int(devices))]

    args += _speed_args(_speed(down, "the download speed"), _speed(up, "the upload speed")) + _gb_args(gb)

    if end is not None:
        args += ["--expires", str(end)]

    if reset_usage:
        args += ["--reset-usage"]

    if disabled is True:
        args += ["--disable"]
    elif disabled is False:
        args += ["--enable"]

    if note is not None:
        args += ["--note", _clean_text(note, "the note", 0, 80)]          # (an empty note clears it)

    if len(args) == 1:
        raise ValueError("nothing to change")

    return _call("set", args)


def remove(name: str) -> dict:
    return _call("del", [_name(name)])


def kick(name: str) -> dict:
    return _call("kick", [_name(name)])


def profile(name: str) -> dict:
    """Everything a customer needs to connect (it holds the password and the shared key: for the owner's eyes and the message he sends)."""
    n = _name(name)
    acc = _call("show", [n])
    info = _call("info", [])
    c = conf()
    address = c["address"] or _entry_host()
    d = {"title": c["title"], "address": address, "user": n, "password": acc.get("password", ""), "psk": info.get("psk", ""), "expires": acc.get("expires"), "limit": acc.get("limit", 1), "down": acc.get("down"), "up": acc.get("up"), "quota": acc.get("quota")}
    d["text"] = customer_text(d)
    return d


# ------------------------------------------------------------------ backup and restore (the encrypted file itself is made by services/backup.py)
MAX_BACKUP_B64 = 6_000_000                                          # (the action takes up to 8 MB of base64)


def parse_backup(out: str) -> dict:
    """The output of the `l2tp_backup` read: the manager's accounts (a dict name -> account, WITH passwords), the shared key and the pool. Raises RuntimeError with a sentence when the entry has no manager or its file cannot be read."""
    got: dict = {"installed": False, "state": None, "psk": "", "pool": "", "error": ""}

    for ln in out.splitlines():
        key, _, rest = ln.partition(" ")
        rest = rest.strip()

        if key == "L2TP":
            got["installed"] = rest == "ok"
        elif key == "ERROR":
            got["error"] = rest[:120]
        elif key in ("ACCOUNTS", "PSK", "POOL"):
            try:
                text = base64.b64decode(rest, validate=True).decode() if rest else ""
            except (ValueError, UnicodeDecodeError):
                got["error"] = f"the {key.lower()} line is damaged"
                continue

            if key == "ACCOUNTS":
                v = _json(text)
                got["state"] = v if isinstance(v, dict) and isinstance(v.get("users"), dict) else None
            else:
                got[key.lower()] = text.strip()

    if not got["installed"]:
        raise RuntimeError("the L2TP manager is not installed on the entry server")

    if got["state"] is None:
        raise RuntimeError("the entry's accounts could not be read" + (f": {got['error']}" if got["error"] else ""))

    return got


def backup_data() -> dict:
    """The entry's accounts and shared key as the entry holds them (for the encrypted backup). Raises RuntimeError with a sentence when they cannot be read."""
    c = conf()

    if not c["entry_id"]:
        raise RuntimeError("choose the L2TP entry server first (the gear on the L2TP page)")

    try:
        return parse_backup(run_script(_node(c["entry_id"], "entry"), "l2tp_backup", timeout=60))
    except ValueError as e:                                             # (a server that was removed from the panel)
        raise RuntimeError(str(e))


def backup_ready() -> bool:
    """Is there an entry to back up at all (a panel that does not use L2TP has nothing to save)."""
    return bool(conf()["entry_id"])


def restore(users: dict, mode: str) -> dict:
    """Put the accounts of a backup on the entry. `missing` adds only the accounts the entry does not have; `all` makes the entry's accounts exactly the backup's. The entry keeps the file it had (accounts.json.before-restore-*), the
    shared key is never changed. Returns what was done, in numbers."""
    why = "the way to restore is 'missing' (only the accounts that are gone) or 'all' (everything as the backup)" if mode not in ("missing", "all") else "" if isinstance(users, dict) else "the backup holds no accounts"

    if why:
        raise ValueError(why)

    b64 = base64.b64encode(json.dumps({"users": users}, separators=(",", ":")).encode()).decode()

    if len(b64) > MAX_BACKUP_B64:
        raise ValueError("the backup holds too many accounts for one restore")

    raw = run_action(_node(conf()["entry_id"], "entry"), "l2tp_restore", {"MODE": mode, "B64": b64}, timeout=180)
    lines = [ln for ln in raw.strip().splitlines() if ln.strip()]
    d = _json(lines[-1]) if lines else None

    if not isinstance(d, dict):
        d = {"error": "the entry did not answer in the expected way"}

    if d.get("error"):
        raise RuntimeError(str(d["error"]))

    log.info("l2tp: restore (%s) added %s updated %s removed %s skipped %s", mode, d.get("added"), d.get("updated"), d.get("removed"), d.get("skipped"))
    return d


def restore_text(d: dict) -> str:
    """What a restore did, in one sentence for the owner."""
    bits = [f"{d.get('added', 0)} account(s) put back"]

    if d.get("updated"):
        bits.append(f"{d['updated']} set as in the backup")

    if d.get("removed"):
        bits.append(f"{d['removed']} removed (they were not in the backup)")

    if d.get("skipped"):
        bits.append(f"{d['skipped']} skipped (not valid: {', '.join(d.get('skipped_names') or [])})")

    return "L2TP restored: " + ", ".join(bits) + f". The entry has {d.get('total', '?')} accounts now." + (f" What it held before is kept there as {d['safety']}." if d.get("safety") else "")


def _date(ts) -> str:
    return time.strftime("%Y-%m-%d", time.gmtime(float(ts) + TEHRAN)) if ts else ""


def customer_text(p: dict) -> str:
    """The message a customer is sent (the connection name first; Persian and English labels, the three platforms)."""
    speed = ""
    down, up = p.get("down"), p.get("up")

    if down or up:
        speed = f"\nسرعت (Speed): {_num(down) if down else '—'} / {_num(up) if up else '—'} Mbit/s"

    until = _date(p.get("expires"))
    title = p["title"]
    volume = f"{_num(round(p['quota'] / 1e9, 2))} GB" if p.get("quota") else "نامحدود"
    return f"""{title}
اشتراک L2TP

نام اتصال (Name): {title}
نوع (Type): L2TP/IPsec PSK
آدرس سرور (Server): {p['address']}
نام کاربری (Username): {p['user']}
رمز عبور (Password): {p['password']}
کلید IPsec (Secret / Pre-shared key): {p['psk']}

اعتبار تا: {until or '—'}
تعداد دستگاه همزمان: {p['limit']}
حجم: {volume}{speed}

آیفون: Settings > General > VPN & Device Management > VPN > Add VPN Configuration
Type را L2TP بگذار، Description را {title}، و Server و Account و Password و Secret را از بالا وارد کن؛ Send All Traffic روشن باشد.

اندروید: Settings > Network > VPN > +
Name را {title}، Type را L2TP/IPSec PSK بگذار و Server address و IPSec pre-shared key و Username و Password را از بالا وارد کن.

ویندوز: Settings > Network > VPN > Add VPN
VPN provider را Windows (built-in)، Connection name را {title}، VPN type را L2TP/IPsec with pre-shared key بگذار و بقیه را از بالا وارد کن.

اگر وصل نشد: اینترنت دیگری را امتحان کن (مثلاً موبایل به‌جای وای‌فای) و دوباره تلاش کن.
"""

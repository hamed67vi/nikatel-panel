"""PasarGuard: the cores (Xray configs) of a PasarGuard panel, read and edited safely (roadmap #94).

A PasarGuard panel keeps one or more CORES: a name, a type (xray or wireguard), the Xray config as JSON and two lists of inbound tags. The inbound tags of a core
are what the hosts (the addresses in the users' links) and the groups (what a user is given) point at, so one careless edit can cut customers without any error.
This module reads the cores, checks an edited config BEFORE it goes anywhere (structure, a line diff, warnings), writes a BACKUP of the current config before every
change and puts any backup back. Nothing secret leaves it: the private keys and passwords of a config are shown as placeholders and put back from the config on
the panel when the edited text comes in.

How a PasarGuard 5.x panel applies a config: `PUT /api/core/{id}?restart_nodes=false` only STORES it (and checks it); the nodes run the new config after their
next restart. `POST /api/core/{id}/restart` restarts Xray on every node of the core, which cuts their customers for a moment. This module never restarts without
an explicit `confirm`."""
from __future__ import annotations

import base64
import copy
import hashlib
import hmac
import ipaddress
import json
import os
import re
import secrets
import threading
import time
from pathlib import Path

from ..config import get_settings
from ..db import session
from . import panels
from .events import emit
from .panels import PanelError

KEEP = 20                       # backups kept per panel and core
MAX_TEXT = 2_000_000            # characters of an edited config
MAX_DIFF_D = 1500               # the diff gives up (and shows "everything changed") beyond this many different lines
MAX_ROWS = 3000                 # rows of a diff that are sent
HOW_APPLIED = ("Save stores the config on the panel and checks it; the nodes run it after their next restart. "
               "Restart restarts Xray on every node of the core: their customers are cut for a moment.")
_WRITE_LOCK = threading.Lock()  # one change of a core at a time (a save, a restore, a restart)
_TLS_PROTOCOLS = ("vless", "vmess", "trojan")       # protocols that rely on TLS / REALITY to hide their traffic (shadowsocks has its own encryption)
_SECRET_KEYS = frozenset({"privatekey", "private_key", "mldsa65seed", "password", "pass", "psk", "presharedkey", "secretkey", "secret", "token"})
_PLACEHOLDER = re.compile(r"^<secret:[0-9a-f]{10}>$")
_BACKUP_NAME = re.compile(r"^cores-(\d+)-(\d+)-(\d{10,16})\.json$")


class NeedsConfirm(ValueError):
    """The change has warnings and the caller did not say `confirm: true` (HTTP 409)."""

    def __init__(self, warns: list[dict]):
        self.warnings = warns
        super().__init__(f"{len(warns)} warning{'s' if len(warns) != 1 else ''}: " + " | ".join(w["text"] for w in warns)[:700] + " - send confirm: true to go on")


class Conflict(ValueError):
    """The core changed on the panel after the caller read it (HTTP 409)."""


# ------------------------------------------------------------------ pure: reading an edited text
def _no_dupes(pairs: list) -> dict:
    out: dict = {}

    for k, v in pairs:
        if k in out:
            raise ValueError(f"the key “{k}” appears twice in one object")

        out[k] = v

    return out


def _bad_constant(name: str):
    raise ValueError(f"{name} is not valid JSON")


def parse_text(text: str):
    """The JSON value of an editor text; the ValueError says what is wrong and where. Strict: no NaN, no duplicate keys."""
    try:
        return json.loads(text, object_pairs_hook=_no_dupes, parse_constant=_bad_constant)
    except json.JSONDecodeError as e:
        raise ValueError(f"not valid JSON: {e.msg} (line {e.lineno}, column {e.colno})") from None
    except RecursionError:
        raise ValueError("not valid JSON: it is nested too deeply") from None


def config_text(cfg) -> str:
    """The text the editor shows and the diff compares: two spaces, no ASCII escaping."""
    return json.dumps(cfg, indent=2, ensure_ascii=False)


def _canon(value) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _obj(v) -> dict:
    return v if isinstance(v, dict) else {}


def _dicts(v) -> list[dict]:
    return [x for x in v if isinstance(x, dict)] if isinstance(v, list) else []


# ------------------------------------------------------------------ pure: secrets stay on this side
def _token(value) -> str:
    """A placeholder for a secret: the same secret always gets the same one, nobody can get the secret back from it (keyed hash)."""
    return "<secret:" + hmac.new(get_settings().secret_key(), _canon(value).encode(), hashlib.sha256).hexdigest()[:10] + ">"


def _is_secret(path: tuple, key: str, parent: dict, value) -> bool:
    """Is this value a secret: a private key, a password, a client id, the key of an inline certificate?"""
    k = key.lower()

    if not value or not isinstance(value, (str, list)):
        return False

    if k in _SECRET_KEYS:
        return isinstance(value, str)

    if k == "id":                                                       # the UUID of a client is its password
        return isinstance(value, str) and any(p in ("clients", "users") for p in path)

    if k == "key":                                                      # the private key of a certificate written into the config
        return "certificate" in parent or "certificateFile" in parent or "keyFile" in parent

    if k == "auth":                                                     # the password of a hysteria client
        return isinstance(value, str) and bool(path) and path[-1] == "hysteriaSettings"

    return False


def _walk_secrets(node, path: tuple, fn):
    """A copy of node where fn(value) replaces every secret value."""
    if isinstance(node, dict):
        return {k: (fn(v) if _is_secret(path, str(k), node, v) else _walk_secrets(v, path + (str(k),), fn)) for k, v in node.items()}

    if isinstance(node, list):
        return [_walk_secrets(x, path, fn) for x in node]

    return node


def mask_secrets(cfg) -> tuple[object, int]:
    """(a copy of cfg with every secret replaced by a placeholder, how many were replaced)."""
    n = 0

    def put(value):
        nonlocal n
        n += 1
        return _token(value)

    return _walk_secrets(cfg, (), put), n


def unmask_secrets(cfg, current):
    """cfg as the editor sent it, with every placeholder replaced by the secret it stands for in `current` (the config on the panel).
    A placeholder that is not a secret of `current` is refused: the panel's config changed since the editor was opened."""
    known: dict[str, object] = {}

    def learn(value):
        known[_token(value)] = value
        return value

    _walk_secrets(current, (), learn)

    def back(node, where: str):
        if isinstance(node, str):
            if _PLACEHOLDER.match(node):
                if node not in known:
                    raise ValueError(f"the placeholder {node} at {where or 'the top'} is not a secret of the config on the panel (it may have changed): reload the core")

                return copy.deepcopy(known[node])

            return node

        if isinstance(node, dict):
            return {k: back(v, f"{where}.{k}" if where else str(k)) for k, v in node.items()}

        if isinstance(node, list):
            return [back(x, f"{where}[{i}]") for i, x in enumerate(node)]

        return node

    return back(cfg, "")


# ------------------------------------------------------------------ pure: the structure of a config
def stream_of(ib: dict) -> tuple[str, str]:
    """(network, security) of an inbound, with Xray's defaults (tcp, none)."""
    ss = _obj(ib.get("streamSettings"))
    return str(ss.get("network") or "tcp").lower(), str(ss.get("security") or "none").lower()


def is_public_listen(listen) -> bool:
    """Does an inbound with this `listen` accept connections from outside the machine? No `listen` means every address, so only the machine itself
    (localhost, a loopback address, a unix or abstract socket) is not public; a private-network address still counts, because a warning that is too careful costs less than one that is missing."""
    s = str(listen or "").strip().lower().strip("[]")

    if not s:
        return True

    if s.startswith(("/", "@")) or s == "localhost":
        return False

    try:
        return not ipaddress.ip_address(s).is_loopback
    except ValueError:
        return True                                                     # a name: assume it resolves to an outside address


def inbound_rows(cfg) -> list[dict]:
    """One row per inbound: tag, port, listen, protocol, network, security, and whether it accepts outside connections."""
    rows = []

    for ib in _dicts(_obj(cfg).get("inbounds")):
        network, security = stream_of(ib)
        rows.append({"tag": str(ib.get("tag") if ib.get("tag") is not None else ""), "port": ib.get("port"), "listen": "" if ib.get("listen") is None else str(ib.get("listen")),
                     "protocol": str(ib.get("protocol") or ""), "network": network, "security": security, "public": is_public_listen(ib.get("listen"))})

    return rows


def outbound_rows(cfg) -> list[dict]:
    """One row per outbound: tag, protocol, network and where it goes (address:port of the first server, when it has one)."""
    rows = []

    for ob in _dicts(_obj(cfg).get("outbounds")):
        st = _obj(ob.get("settings"))
        first = next(iter(_dicts(st.get("vnext")) or _dicts(st.get("servers"))), {})
        target = f"{first.get('address')}:{first.get('port')}" if first.get("address") else ""
        rows.append({"tag": str(ob.get("tag") if ob.get("tag") is not None else ""), "protocol": str(ob.get("protocol") or ""), "network": stream_of(ob)[0], "target": target})

    return rows


def rule_count(cfg) -> int:
    return len(_dicts(_obj(_obj(cfg).get("routing")).get("rules")))


def check_config(cfg, core_type: str = "xray") -> list[str]:
    """The reasons this config cannot be saved (empty list = fine): a JSON object; for an Xray core also `inbounds` and `outbounds` as arrays,
    and every inbound with a tag that is unique, not empty and free of the two spellings the panel reserves (a comma and <=>)."""
    if not isinstance(cfg, dict):
        return ["the config must be a JSON object"]

    if core_type != "xray":
        return [] if cfg else ["the config must not be empty"]

    errors: list[str] = []
    inbounds, outbounds = cfg.get("inbounds"), cfg.get("outbounds")

    if not isinstance(inbounds, list):
        errors.append("“inbounds” must be an array")
    elif not inbounds:
        errors.append("the config has no inbounds: the panel refuses it")
    else:
        errors += _check_inbounds(inbounds)

    if not isinstance(outbounds, list):
        errors.append("“outbounds” must be an array")
    elif not outbounds:
        errors.append("the config has no outbounds: the panel refuses it")
    else:
        for i, ob in enumerate(outbounds, 1):
            if not isinstance(ob, dict):
                errors.append(f"outbound #{i} is not an object")
            elif not isinstance(ob.get("tag"), str) or not ob["tag"].strip():
                errors.append(f"outbound #{i} has no tag: the panel refuses it")

    return errors


def _check_inbounds(inbounds: list) -> list[str]:
    errors: list[str] = []
    seen: dict[str, int] = {}

    for i, ib in enumerate(inbounds, 1):
        if not isinstance(ib, dict):
            errors.append(f"inbound #{i} is not an object")
            continue

        tag = ib.get("tag")

        if not isinstance(tag, str) or not tag.strip():
            errors.append(f"inbound #{i} has no tag")
            continue

        if "," in tag or "<=>" in tag:
            errors.append(f"the tag “{tag}” of inbound #{i} has a character the panel does not allow (a comma or <=>)")

        if tag in seen:
            errors.append(f"the tag “{tag}” is used by inbound #{seen[tag]} and inbound #{i}: every tag must be unique")
        else:
            seen[tag] = i

    return errors


# ------------------------------------------------------------------ pure: warnings
def usage_by_tag(hosts: list[dict] | None, groups: list[dict] | None) -> dict[str, dict]:
    """tag -> {hosts, hosts_on, own_port (hosts with a port of their own), groups: [{name, users}]} from the hosts and groups of the panel."""
    use: dict[str, dict] = {}

    def slot(tag: str) -> dict:
        return use.setdefault(tag, {"hosts": 0, "hosts_on": 0, "own_port": 0, "groups": []})

    for h in hosts or []:
        tag = h.get("inbound_tag") or h.get("tag")

        if not isinstance(tag, str) or not tag:
            continue

        s = slot(tag)
        s["hosts"] += 1
        s["hosts_on"] += 0 if h.get("is_disabled") else 1
        s["own_port"] += 0 if h.get("port") in (None, "", 0) else 1

    for g in groups or []:
        tags = g.get("inbound_tags") if isinstance(g.get("inbound_tags"), list) else []

        for tag in tags:
            if isinstance(tag, str) and tag:
                slot(tag)["groups"].append({"name": str(g.get("name") or g.get("id") or "?")[:60], "users": g.get("total_users")})

    return use


def _plural(n: int, word: str) -> str:
    return f"{n} {word}{'' if n == 1 else 's'}"


def _port_key(p) -> str:
    return "" if p is None else str(p).strip()


def _dangling(cfg) -> set[tuple[str, str]]:
    """(kind, tag) of the routing rules that name an outbound or a balancer which the config does not have."""
    obs = {str(o.get("tag")) for o in _dicts(_obj(cfg).get("outbounds")) if o.get("tag")}
    rt = _obj(_obj(cfg).get("routing"))
    bal = {str(b.get("tag")) for b in _dicts(rt.get("balancers")) if b.get("tag")}
    out = set()

    for r in _dicts(rt.get("rules")):
        t, bt = r.get("outboundTag"), r.get("balancerTag")

        if isinstance(t, str) and t and t not in obs:
            out.add(("outbound", t))

        if isinstance(bt, str) and bt and bt not in bal:
            out.add(("balancer", bt))

    return out


def _duplicate_outbounds(cfg) -> set[str]:
    seen: set[str] = set()
    dup: set[str] = set()

    for r in outbound_rows(cfg):
        if r["tag"] in seen:
            dup.add(r["tag"])

        seen.add(r["tag"])

    return dup


def _removed_warning(tag: str, old_row: dict, new_rows: list[dict], old_tags: set[str], use: dict, hosts, groups) -> dict | None:
    if hosts is None or groups is None:
        return {"code": "tag_removed", "level": "warn", "tag": tag, "text": f"inbound “{tag}” is removed, and the hosts and groups of the panel could not be read to see whether they still use it"}

    u = use.get(tag)

    if not u or not (u["hosts"] or u["groups"]):
        return None

    parts = []

    if u["hosts"]:
        parts.append(_plural(u["hosts"], "host"))

    if u["groups"]:
        parts.append("the " + ("group " if len(u["groups"]) == 1 else "groups ") + ", ".join(f"“{g['name']}”" + (f" ({_plural(int(g['users']), 'user')})" if isinstance(g["users"], int) else "") for g in u["groups"][:4]))

    renamed = next((r for r in new_rows if r["tag"] not in old_tags and r["port"] == old_row["port"] and r["protocol"] == old_row["protocol"]), None)
    hint = f" It looks renamed to “{renamed['tag']}”: hosts and groups keep the old name until they are changed." if renamed else " Those users lose it until the tag is back or the hosts and groups are changed."
    return {"code": "tag_removed", "level": "danger", "tag": tag, "text": f"inbound “{tag}” is removed but {' and '.join(parts)} still use it.{hint}"}


def _exposed(row: dict) -> bool:
    """An inbound that carries proxy traffic with no TLS or REALITY and accepts connections from outside."""
    return row["protocol"] in _TLS_PROTOCOLS and row["security"] in ("none", "") and row["public"]


def _first_by_tag(rows: list[dict]) -> dict[str, dict]:
    """tag -> its row (the first one when a tag is used twice), in the order of the config; inbounds without a tag are left out."""
    out: dict[str, dict] = {}

    for r in rows:
        if r["tag"]:
            out.setdefault(r["tag"], r)

    return out


def _port_note(u: dict | None) -> str:
    """The sentence that says how many hosts feel a port change."""
    if not u or not u["hosts"]:
        return ""

    note = f" {_plural(u['hosts'], 'host')} {'uses' if u['hosts'] == 1 else 'use'} this inbound"
    follow = u["hosts"] - u["own_port"]

    if follow:
        note += f", {follow} of them {'follows' if follow == 1 else 'follow'} its port, so their links change"

    return note + "."


def warnings(old, new, hosts: list[dict] | None, groups: list[dict] | None) -> list[dict]:
    """What a change from `old` to `new` can break without any error, as [{code, level (danger | warn), tag, text}]. `hosts` / `groups` are the lists of the
    panel (None = could not be read). Only what the change brings in is reported: a problem the old config already had is not repeated."""
    out: list[dict] = []
    new_rows = inbound_rows(new)
    old_by, new_by = _first_by_tag(inbound_rows(old)), _first_by_tag(new_rows)
    use = usage_by_tag(hosts, groups)

    for tag, orow in old_by.items():
        if tag not in new_by:
            w = _removed_warning(tag, orow, new_rows, set(old_by), use, hosts, groups)

            if w:
                out.append(w)

    for tag, nr in new_by.items():
        orow = old_by.get(tag)

        if orow is not None and _port_key(orow["port"]) != _port_key(nr["port"]):
            out.append({"code": "port_changed", "level": "warn", "tag": tag, "text": f"inbound “{tag}”: port {orow['port']} → {nr['port']}.{_port_note(use.get(tag))} A firewall, tunnel or CDN that points at the old port must change too."})

        if _exposed(nr) and not (orow is not None and _exposed(orow)):
            out.append({"code": "plain_exposed", "level": "warn", "tag": tag, "text": f"inbound “{tag}” ({nr['protocol']}, {nr['network']}) has no TLS or REALITY and accepts connections from outside on port {nr['port']}: its traffic is readable on the way, which is only fine behind a CDN or a tunnel that adds TLS."})

    out += _routing_warnings(old, new)
    return out


def _routing_warnings(old, new) -> list[dict]:
    out: list[dict] = []

    for kind, tag in sorted(_dangling(new) - _dangling(old)):
        if kind == "outbound":
            out.append({"code": "rule_target_missing", "level": "danger", "tag": tag, "text": f"a routing rule sends traffic to the outbound “{tag}”, which the config does not have: that traffic falls to the first outbound instead."})
        else:
            out.append({"code": "rule_balancer_missing", "level": "danger", "tag": tag, "text": f"a routing rule uses the balancer “{tag}”, which the config does not have."})

    old_first = (outbound_rows(old) or [{"tag": ""}])[0]["tag"]
    new_first = (outbound_rows(new) or [{"tag": ""}])[0]["tag"]

    if old_first and new_first and old_first != new_first:
        out.append({"code": "default_outbound", "level": "warn", "tag": new_first, "text": f"the default outbound (the first one) changes from “{old_first}” to “{new_first}”: all traffic that no routing rule matches goes there now."})

    for tag in sorted(_duplicate_outbounds(new) - _duplicate_outbounds(old)):
        out.append({"code": "outbound_twice", "level": "warn", "tag": tag, "text": f"two outbounds are called “{tag}”: Xray keeps only one of them."})

    return out


# ------------------------------------------------------------------ pure: the line diff (the JS twin is in web/pg_cores.js; a test runs both on the same inputs)
def _myers(a: list[str], b: list[str], max_d: int) -> list[tuple[str, str]] | None:
    """Myers' O(ND) diff: the shortest list of ('=' | '-' | '+', line) that turns a into b, None when it needs more than max_d changes."""
    n, m = len(a), len(b)

    if n == 0:
        return [("+", x) for x in b]

    if m == 0:
        return [("-", x) for x in a]

    top = min(n + m, max_d)
    off = top + 1
    v = [0] * (2 * top + 3)                      # v[off + k]: the furthest x reached on diagonal k
    trace: list[list[int]] = []

    for d in range(top + 1):
        trace.append(v[off - d: off + d + 1])    # the state before round d, for k = -d .. d

        for k in range(-d, d + 1, 2):
            x = v[off + k + 1] if k == -d or (k != d and v[off + k - 1] < v[off + k + 1]) else v[off + k - 1] + 1
            y = x - k

            while x < n and y < m and a[x] == b[y]:
                x += 1
                y += 1

            v[off + k] = x

            if x >= n and y >= m:
                return _walk_back(a, b, trace, d)

    return None


def _walk_back(a: list[str], b: list[str], trace: list[list[int]], last: int) -> list[tuple[str, str]]:
    x, y = len(a), len(b)
    out: list[tuple[str, str]] = []

    for d in range(last, 0, -1):
        s = trace[d]
        k = x - y
        pk = k + 1 if k == -d or (k != d and s[k - 1 + d] < s[k + 1 + d]) else k - 1
        px = s[pk + d]
        py = px - pk

        while x > px and y > py:
            out.append(("=", a[x - 1]))
            x -= 1
            y -= 1

        out.append(("+", b[y - 1]) if x == px else ("-", a[x - 1]))
        x, y = px, py

    while x > 0:                                 # round 0 is a plain run of equal lines
        out.append(("=", a[x - 1]))
        x -= 1

    out.reverse()
    return out


def diff_ops(a: list[str], b: list[str], max_d: int = MAX_DIFF_D) -> tuple[list[tuple[str, str]], bool]:
    """(the edit script from a to b, whether the diff gave up and shows the middle part as removed and added as a whole)."""
    n, m = len(a), len(b)
    s = 0

    while s < n and s < m and a[s] == b[s]:
        s += 1

    e = 0

    while e < n - s and e < m - s and a[n - 1 - e] == b[m - 1 - e]:
        e += 1

    mid = _myers(a[s:n - e], b[s:m - e], max_d)
    coarse = mid is None

    if mid is None:
        mid = [("-", x) for x in a[s:n - e]] + [("+", x) for x in b[s:m - e]]

    return [("=", x) for x in a[:s]] + mid + [("=", x) for x in a[n - e:]], coarse


def diff_rows(ops: list[tuple[str, str]], context: int = 2) -> list[list]:
    """The compact form: changed lines with `context` unchanged lines around them; a longer unchanged run becomes ['~', how many lines]."""
    n = len(ops)
    keep = [False] * n

    for i, (op, _t) in enumerate(ops):
        if op != "=":
            for j in range(max(0, i - context), min(n, i + context + 1)):
                keep[j] = True

    rows: list[list] = []
    hidden = 0

    for i, (op, t) in enumerate(ops):
        if keep[i]:
            if hidden:
                rows.append(["~", hidden])
                hidden = 0

            rows.append([op, t])
        else:
            hidden += 1

    if hidden:
        rows.append(["~", hidden])

    return rows


def line_diff(old_text: str, new_text: str, context: int = 2) -> dict:
    """{same, added, removed, coarse, truncated, rows}: the line diff of two texts in the compact form."""
    ops, coarse = diff_ops(old_text.split("\n"), new_text.split("\n"))
    added, removed = sum(1 for o, _t in ops if o == "+"), sum(1 for o, _t in ops if o == "-")
    rows = diff_rows(ops, context)
    return {"same": added == 0 and removed == 0, "added": added, "removed": removed, "coarse": coarse, "truncated": len(rows) > MAX_ROWS, "rows": rows[:MAX_ROWS]}


# ------------------------------------------------------------------ the cores of the panel
def _str_list(v) -> list[str]:
    if isinstance(v, str):
        v = v.split(",")

    return sorted({str(x).strip() for x in v if str(x).strip()}) if isinstance(v, (list, tuple, set)) else []


def _norm_core(raw: dict) -> dict:
    """A core as this module uses it. The fields differ between PasarGuard versions, so a missing key gets a default."""
    return {"id": int(raw["id"]), "name": str(raw.get("name") or ""), "type": str(raw.get("type") or "xray").lower(), "config": _obj(raw.get("config")),
            "exclude": _str_list(raw.get("exclude_inbound_tags")), "fallbacks": _str_list(raw.get("fallbacks_inbound_tags")),
            "created_at": raw.get("created_at"), "updated_at": raw.get("updated_at") or raw.get("modified_at")}


def rev_of(core: dict) -> str:
    """A short fingerprint of everything a save writes: a save that carries an older one is refused (someone else changed the core in between)."""
    return hashlib.sha256(_canon({k: core[k] for k in ("name", "type", "config", "exclude", "fallbacks")}).encode()).hexdigest()[:16]


def _meta(core: dict) -> dict:
    return {"id": core["id"], "name": core["name"], "type": core["type"], "created_at": core["created_at"], "updated_at": core["updated_at"],
            "exclude_inbound_tags": core["exclude"], "fallbacks_inbound_tags": core["fallbacks"]}


def _get_core(c: dict, core_id: int) -> dict:
    raw = panels.call(c, "GET", f"/api/core/{int(core_id)}")

    if not isinstance(raw, dict):
        raise PanelError("the panel answered with something that is not a core")

    return _norm_core({**raw, "id": raw.get("id", core_id)})


def list_cores(c: dict) -> list[dict]:
    """The cores of the panel, one row each: id, name, type, created / updated, how many inbounds, outbounds and routing rules, and how many backups are kept."""
    raw = panels.call(c, "GET", "/api/cores")
    items = raw.get("cores") if isinstance(raw, dict) else raw
    rows = []

    for x in items if isinstance(items, list) else []:
        try:
            core = _norm_core(x)
        except (KeyError, TypeError, ValueError):
            continue                                                    # not a core: no usable id

        cfg = core["config"]
        rows.append({**_meta(core), "inbounds": len(_dicts(cfg.get("inbounds"))), "outbounds": len(_dicts(cfg.get("outbounds"))), "rules": rule_count(cfg), "backups": len(backups(c["id"], core["id"]))})

    return rows


def _as_list(raw, key: str) -> list[dict]:
    items = raw.get(key) if isinstance(raw, dict) else raw
    return _dicts(items)


def _usage_sources(c: dict) -> tuple[list[dict] | None, list[dict] | None]:
    """(hosts, groups) of the panel; None for one that could not be read (the warnings then say that they could not check)."""
    hosts = groups = None

    try:
        hosts = _as_list(panels.call(c, "GET", "/api/hosts"), "hosts")
    except (PanelError, ValueError):
        pass

    try:
        groups = _as_list(panels.call(c, "GET", "/api/groups"), "groups")
    except (PanelError, ValueError):
        pass

    return hosts, groups


def core_view(c: dict, core_id: int) -> dict:
    """One core for the editor: its config with the secrets replaced by placeholders (also as text), the inbounds with how many hosts and groups use each,
    the outbounds, a fingerprint (`rev`) to send back with a save, and the backups kept for it."""
    cur = _get_core(c, core_id)
    hosts, groups = _usage_sources(c)
    use = usage_by_tag(hosts, groups)
    rows = inbound_rows(cur["config"])

    for r in rows:
        u = use.get(r["tag"], {})
        r["hosts"] = u.get("hosts", 0) if hosts is not None else None
        r["groups"] = [g["name"] for g in u.get("groups", [])] if groups is not None else None

    masked, n = mask_secrets(cur["config"])
    return {"core": _meta(cur), "config": masked, "text": config_text(masked), "rev": rev_of(cur), "masked": n, "inbounds": rows, "outbounds": outbound_rows(cur["config"]),
            "rules": rule_count(cur["config"]), "usage_known": {"hosts": hosts is not None, "groups": groups is not None}, "backups": backups(c["id"], cur["id"]), "applies": HOW_APPLIED}


# ------------------------------------------------------------------ checking an edit (nothing is changed here)
def _candidate(cur: dict, text: str | None, config) -> tuple[object, list[str]]:
    """(the config that would be saved, errors): the editor text parsed and its placeholders put back, or the config object given by an API client."""
    if text is None and config is None:
        return None, ["send the config as `text` (the editor's text) or as `config` (an object)"]

    try:
        value = parse_text(text) if text is not None else config
        return unmask_secrets(value, cur["config"]), []
    except ValueError as e:
        return None, [str(e)]


def _verdict(c: dict, cur: dict, new) -> tuple[list[str], list[dict]]:
    errors = check_config(new, cur["type"])
    hosts, groups = _usage_sources(c)
    return errors, warnings(cur["config"], new, hosts, groups)


def _diff_of(cur: dict, new) -> dict:
    return line_diff(config_text(mask_secrets(cur["config"])[0]), config_text(mask_secrets(new)[0]))


def dry_run(c: dict, core_id: int, text: str | None = None, config=None) -> dict:
    """Everything a save would check, and nothing is written: {valid, errors, warnings, changed, needs_confirm, diff, rev}. The diff is from the config on the
    panel to the edited one, with secrets as placeholders on both sides."""
    cur = _get_core(c, core_id)
    new, errors = _candidate(cur, text, config)
    return _dry_of(c, cur, new, errors)


def _dry_of(c: dict, cur: dict, new, errors: list[str]) -> dict:
    """The answer of a dry run for a config `new` (None when it could not even be read, then `errors` say why) against the core `cur` as the panel has it."""
    base = {"valid": False, "errors": errors, "warnings": [], "changed": None, "needs_confirm": False, "diff": None, "rev": rev_of(cur), "inbounds": 0, "outbounds": 0}

    if new is None:
        return base

    errors, warns = _verdict(c, cur, new)
    return {**base, "valid": not errors, "errors": errors, "warnings": warns, "changed": _canon(new) != _canon(cur["config"]), "needs_confirm": bool(warns), "diff": _diff_of(cur, new),
            "inbounds": len(_dicts(_obj(new).get("inbounds"))), "outbounds": len(_dicts(_obj(new).get("outbounds")))}


# ------------------------------------------------------------------ backups
def _bdir() -> Path:
    """The folder of the backups (shared with the other PasarGuard tabs, each with its own file prefix): only this server's user may look into it, the files hold keys."""
    d = get_settings().data_dir / "pg-backups"
    d.mkdir(parents=True, exist_ok=True)

    try:
        d.chmod(0o700)
    except OSError:
        pass                                                            # not ours to change (a shared mount): the files are mode 600 anyway

    return d


def backup_write(panel_id: int, cur: dict, note: str) -> str:
    """Write the whole core as it is now (mode 600: it holds keys) and keep the last KEEP files of this panel and core. Returns the file name."""
    d = _bdir()
    stamp = int(time.time() * 1000)
    name = f"cores-{int(panel_id)}-{int(cur['id'])}-{stamp}.json"

    while (d / name).exists():                                          # two changes in the same millisecond: never overwrite a backup
        stamp += 1
        name = f"cores-{int(panel_id)}-{int(cur['id'])}-{stamp}.json"

    body = {"panel": int(panel_id), "core": int(cur["id"]), "at": int(time.time()), "note": str(note)[:120], "name": cur["name"], "type": cur["type"],
            "exclude_inbound_tags": cur["exclude"], "fallbacks_inbound_tags": cur["fallbacks"], "config": cur["config"]}
    tmp = d / (name + ".tmp")

    try:
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)

        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(body, f, ensure_ascii=False)

        os.replace(tmp, d / name)
    except OSError as e:
        raise RuntimeError(f"the backup could not be written, so nothing was changed ({type(e).__name__})") from None

    for old in sorted(d.glob(f"cores-{int(panel_id)}-{int(cur['id'])}-*.json"))[:-KEEP]:
        old.unlink(missing_ok=True)

    return name


def _backup_load(panel_id: int, core_id: int, name: str) -> dict:
    m = _BACKUP_NAME.match(str(name))

    if not m or int(m.group(1)) != int(panel_id) or int(m.group(2)) != int(core_id):
        raise ValueError("unknown backup")

    try:
        body = json.loads((_bdir() / name).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise ValueError("unknown backup") from None

    if not isinstance(body, dict) or not isinstance(body.get("config"), dict):
        raise ValueError("this backup file is damaged")  # noqa: TRY004 - the caller shows the ValueError text to the operator

    return body


def backups(panel_id: int, core_id: int) -> list[dict]:
    """The kept backups of one core, newest first: file, time, note, how many inbounds and outbounds the saved config has."""
    out = []

    for f in sorted(_bdir().glob(f"cores-{int(panel_id)}-{int(core_id)}-*.json"), reverse=True)[:KEEP]:
        try:
            body = json.loads(f.read_text(encoding="utf-8"))
            cfg = _obj(body.get("config"))
            out.append({"file": f.name, "at": body.get("at"), "note": str(body.get("note") or ""), "inbounds": len(_dicts(cfg.get("inbounds"))), "outbounds": len(_dicts(cfg.get("outbounds")))})
        except (OSError, ValueError, AttributeError):
            continue                                                    # a damaged file is skipped, never a reason to fail the list

    return out


def backup_view(c: dict, core_id: int, name: str) -> dict:
    """One backup for the screen: its config (secrets as placeholders), the diff from the config on the panel to it, and the errors and warnings that restoring it would meet."""
    stored = _backup_load(c["id"], core_id, name)
    cur = _get_core(c, core_id)
    old = stored["config"]
    errors, warns = _verdict(c, cur, old)
    masked = mask_secrets(old)[0]
    meta = next((b for b in backups(c["id"], core_id) if b["file"] == name), {"file": name, "at": stored.get("at"), "note": str(stored.get("note") or "")})
    return {"backup": meta, "config": masked, "text": config_text(masked), "same": _canon(old) == _canon(cur["config"]), "diff": _diff_of(cur, old), "errors": errors, "warnings": warns}


# ------------------------------------------------------------------ changing the panel
def _writable() -> None:
    if get_settings().read_only:
        raise ValueError("this panel is read-only; set NIKATEL_READ_ONLY=false to change a core config")


def _essentials(cfg) -> tuple[list[tuple[str, str]], list[str]]:
    """What must survive a save unchanged in the panel's answer: the inbounds with their ports, and the outbounds."""
    return [(r["tag"], _port_key(r["port"])) for r in inbound_rows(cfg)], [r["tag"] for r in outbound_rows(cfg)]


def _put(c: dict, cur: dict, cfg: dict) -> None:
    """PUT the config with the core's own name, type and tag lists (a PUT that leaves them out may clear them). `restart_nodes=false`: only store it.
    (`modify_users=false` is what PasarGuard 1.x called the same safety; a panel ignores a query parameter it does not know.)"""
    body: dict = {"config": cfg, "exclude_inbound_tags": cur["exclude"], "fallbacks_inbound_tags": cur["fallbacks"]}

    if cur["name"]:
        body["name"] = cur["name"]

    if cur["type"]:
        body["type"] = cur["type"]

    panels.call(c, "PUT", f"/api/core/{cur['id']}?restart_nodes=false&modify_users=false", body=body, timeout=120)


def _scrub(text: str, *configs) -> str:
    """The text of a panel's error with every secret of these configs replaced by <hidden>: a 422 of PasarGuard can quote the body it was given."""
    found: list[str] = []

    def grab(value):
        found.extend(str(x) for x in (value if isinstance(value, list) else [value]))
        return value

    for cfg in configs:
        _walk_secrets(cfg, (), grab)

    for secret in sorted({s for s in found if len(s) >= 4}, key=len, reverse=True):
        text = text.replace(secret, "<hidden>")

    return text


def _put_back(c: dict, cur: dict, name: str, why: str) -> RuntimeError:
    """The new config is not (reliably) on the panel: PUT the old one again. Returns the error to raise, which says whether that worked."""
    try:
        _put(c, cur, cur["config"])
    except PanelError as e:
        return RuntimeError(f"not applied ({why[:100]}) and the old config could not be put back ({_scrub(str(e), cur['config'])[:80]}): restore the backup {name}")

    return RuntimeError(f"not applied ({why[:140]}); the old config was put back")


def _store(c: dict, cur: dict, new: dict, note: str) -> tuple[str, dict]:
    """Back the current config up, PUT the new one and read it back; when the panel did not keep it, put the old one back. Returns (backup name, the core as stored now).
    What the panel says about a failure is scrubbed of the secrets of both configs before it is passed on."""
    name = backup_write(c["id"], cur, note)

    try:
        _put(c, cur, new)
    except PanelError as e:
        why = _scrub(str(e), cur["config"], new)

        if 400 <= e.code < 500:                                         # the panel refused it: nothing changed there, so the backup is not needed
            (_bdir() / name).unlink(missing_ok=True)
            raise PanelError(f"the panel refused the config ({why})", e.code) from None

        raise _put_back(c, cur, name, why) from None                    # no answer or a server error: it may or may not have been stored

    try:
        back = _get_core(c, cur["id"])
    except (PanelError, ValueError) as e:
        raise _put_back(c, cur, name, "the panel could not be read back: " + _scrub(str(e), cur["config"], new)) from None

    if _essentials(back["config"]) != _essentials(new):
        raise _put_back(c, cur, name, "the panel did not keep the new config")

    return name, back


def nodes_summary(c: dict) -> dict:
    """How the nodes look now: {known, total, connected, connecting, problems: [{name, status, message}]}."""
    try:
        rows = panels.parse_nodes(panels.call(c, "GET", "/api/nodes"))
    except (PanelError, ValueError):
        return {"known": False, "total": 0, "connected": 0, "connecting": 0, "problems": []}

    live = [n for n in rows if n["status"] != "disabled"]
    return {"known": True, "total": len(live), "connected": sum(1 for n in live if n["status"] in ("connected", "healthy")), "connecting": sum(1 for n in live if n["status"] == "connecting"),
            "problems": [{"name": n["name"], "status": n["status"], "message": n["message"]} for n in live if n["status"] not in ("connected", "healthy", "connecting")][:10]}


def _restart_nodes(c: dict, core_id: int) -> dict:
    panels.call(c, "POST", f"/api/core/{int(core_id)}/restart", timeout=180)
    return nodes_summary(c)


def _record(c: dict, text: str) -> None:
    with session() as db:
        emit(db, "info", "panels", text, str(c["id"]))


def _apply(c: dict, cur: dict, new: dict, note: str, restart_after: bool, warns: list[dict]) -> dict:
    """The part of a save and a restore that writes. Call it with the write lock held."""
    name, back = _store(c, cur, new, note)
    n_in, n_out = len(_dicts(new.get("inbounds"))), len(_dicts(new.get("outbounds")))
    _record(c, f"Core “{cur['name']}” (#{cur['id']}) of panel #{c['id']} changed: {n_in} inbounds, {n_out} outbounds (backup {name}).")
    out = {"saved": True, "backup": name, "inbounds": n_in, "outbounds": n_out, "warnings": warns, "rev": rev_of(back), "restarted": False, "restart_error": "", "nodes": None}

    if restart_after:
        try:
            out["nodes"] = _restart_nodes(c, cur["id"])
            out["restarted"] = True
            _record(c, f"Xray restarted on the nodes of core “{cur['name']}” (#{cur['id']}) of panel #{c['id']} after the change.")
        except PanelError as e:
            out["restart_error"] = _scrub(str(e), cur["config"], new)[:200]         # the config IS saved; only the restart failed

    return out


def save(c: dict, core_id: int, text: str | None = None, config=None, confirm: bool = False, restart_after: bool = False, base_rev: str | None = None, note: str = "") -> dict:
    """Save an edited config: refused with a reason when it is invalid (ValueError), when the core changed since it was read (Conflict) or when it has warnings and
    `confirm` is not true (NeedsConfirm). Otherwise the current config is backed up first, the new one PUT and read back, and (restart_after) Xray restarted on the nodes."""
    _writable()

    if restart_after and not confirm:
        raise ValueError("restarting the nodes cuts their customers for a moment: send confirm: true")

    with _WRITE_LOCK:
        cur = _get_core(c, core_id)

        if base_rev and base_rev != rev_of(cur):
            raise Conflict("the core was changed on the panel after you opened it: reload it and make the change again")

        new, errors = _candidate(cur, text, config)

        if new is None:
            raise ValueError(errors[0])

        errors, warns = _verdict(c, cur, new)

        if errors:
            raise ValueError("; ".join(errors)[:700])

        if _canon(new) == _canon(cur["config"]):
            raise ValueError("nothing to save: the config is the same as the one on the panel")

        if warns and not confirm:
            raise NeedsConfirm(warns)

        return _apply(c, cur, new, str(note or "before a change from the panel"), restart_after, warns)


def restore(c: dict, core_id: int, name: str, confirm: bool = False, restart_after: bool = False) -> dict:
    """Put a backup back: the current config is backed up first, then the backup's config is PUT, with the same checks and the same `confirm` rule as a save."""
    _writable()

    if restart_after and not confirm:
        raise ValueError("restarting the nodes cuts their customers for a moment: send confirm: true")

    stored = _backup_load(c["id"], core_id, name)

    with _WRITE_LOCK:
        cur = _get_core(c, core_id)
        new = stored["config"]
        errors, warns = _verdict(c, cur, new)

        if errors:
            raise ValueError("this backup cannot be put back: " + "; ".join(errors)[:600])

        if _canon(new) == _canon(cur["config"]):
            raise ValueError("nothing to restore: the core already has this config")

        if warns and not confirm:
            raise NeedsConfirm(warns)

        return _apply(c, cur, new, f"before restoring {name}", restart_after, warns)


def restart(c: dict, core_id: int, confirm: bool = False) -> dict:
    """Restart Xray on every node of the core (POST /api/core/{id}/restart) and report how the nodes look afterwards. Needs `confirm`: customers are cut for a moment."""
    _writable()

    if not confirm:
        raise ValueError("restarting the nodes cuts their customers for a moment: send confirm: true")

    with _WRITE_LOCK:
        cur = _get_core(c, core_id)
        nodes = _restart_nodes(c, cur["id"])
        _record(c, f"Xray restarted on the nodes of core “{cur['name']}” (#{cur['id']}) of panel #{c['id']}.")

    return {"restarted": True, "nodes": nodes}


# ------------------------------------------------------------------ a new core, deleting one, the two tag lists, one inbound at a time (the owner asked for add / edit / delete of cores and inbounds)
CORE_TYPES = ("xray", "wg", "mtproto", "singbox")           # CoreType of PasarGuard 5.4
DEFAULT_CORE_ID = 1                                         # the panel refuses to delete the first core (403)
PROTOCOLS = ("vless", "vmess", "trojan", "shadowsocks")     # what the inbound builder makes; anything else (hysteria, wireguard, ...) is written as JSON
NETWORKS = ("tcp", "ws", "grpc", "httpupgrade", "xhttp")
SECURITIES = ("none", "tls", "reality")
XHTTP_MODES = ("auto", "packet-up", "stream-up", "stream-one")
FLOWS = ("xtls-rprx-vision",)
_DEFAULT_XRAY = {      # the config a 5.4 panel starts with (read from a real one): one Shadowsocks inbound, DIRECT and BLOCK, private addresses blocked
    "policy": {"levels": {"0": {"statsUserOnline": True}}},
    "log": {"loglevel": "warning"},
    "inbounds": [{"tag": "Shadowsocks TCP", "listen": "0.0.0.0", "port": 1080, "protocol": "shadowsocks", "settings": {"clients": [], "network": "tcp,udp"}}],
    "outbounds": [{"protocol": "freedom", "tag": "DIRECT"}, {"protocol": "blackhole", "tag": "BLOCK"}],
    "routing": {"rules": [{"ip": ["geoip:private"], "outboundTag": "BLOCK", "type": "field"}]},
}
_HOSTPORT = re.compile(r"^[A-Za-z0-9._\-\[\]:]{1,253}$")


def check_core_name(name) -> str:
    n = str(name or "").strip()

    if not n or len(n) > 256:
        raise ValueError("a core needs a name of 1 to 256 characters")

    if any(ord(ch) < 32 for ch in n):
        raise ValueError("the name of a core cannot hold a control character")

    return n


def tag_list(v, what: str = "tags") -> list[str]:
    """A list of inbound tags from a list or from a text (comma or line separated): sorted, without doubles; the panel takes at most 2048 characters in all."""
    if v in (None, ""):
        return []

    if isinstance(v, str):
        v = re.split(r"[,\n]", v)

    if not isinstance(v, (list, tuple, set)):
        raise ValueError(f"the {what} are a list of inbound tags")  # noqa: TRY004 - the caller shows the ValueError text to the operator

    out = sorted({str(x).strip() for x in v if str(x).strip()})

    if len(",".join(out)) > 2048:
        raise ValueError(f"the {what} are too long (2048 characters in all)")

    return out


def _all_tags(c: dict) -> set[str]:
    """Every inbound tag of every core of the panel (a tag is one name for the hosts and groups, so a new core should not reuse one)."""
    try:
        raw = panels.call(c, "GET", "/api/cores")
    except (PanelError, ValueError):
        return set()

    items = raw.get("cores") if isinstance(raw, dict) else raw
    return {r["tag"] for x in (items if isinstance(items, list) else []) if isinstance(x, dict) for r in inbound_rows(_obj(x.get("config"))) if r["tag"]}


def _free_tag(tag: str, taken: set[str]) -> str:
    if tag not in taken:
        return tag

    n = 2

    while f"{tag} {n}" in taken:
        n += 1

    return f"{tag} {n}"


def default_config(core_type: str = "xray", taken: set[str] | None = None) -> dict:
    """The config a new core starts with: the one of the panel for Xray (its inbound gets a tag that no core uses yet), an empty object for the other types (the editor says what is missing)."""
    if core_type != "xray":
        return {}

    cfg = copy.deepcopy(_DEFAULT_XRAY)
    cfg["inbounds"][0]["tag"] = _free_tag(cfg["inbounds"][0]["tag"], taken or set())
    return cfg


def nodes_on(c: dict, core_id: int) -> list[dict]:
    """The nodes of the panel that run this core: [{id, name, status}]. An unreadable list is an empty one (the panel's own answer to a delete still decides)."""
    try:
        raw = panels.call(c, "GET", "/api/nodes")
    except (PanelError, ValueError):
        return []

    items = raw.get("nodes") if isinstance(raw, dict) else raw
    return [{"id": n.get("id"), "name": str(n.get("name") or ""), "status": str(n.get("status") or "")} for n in (items if isinstance(items, list) else [])
            if isinstance(n, dict) and n.get("core_config_id") == int(core_id)]


def create(c: dict, name, core_type: str = "xray", text: str | None = None, config=None, exclude=None, fallbacks=None, copy_from: int | None = None) -> dict:
    """A new core with its own name, type, config and tag lists. The config is the editor's `text`, an object, a copy of another core (`copy_from`, secrets and all) or the
    starting config of the panel. The panel checks it and answers 400 with its words; nothing restarts: a new core runs on no node until a node is told to use it."""
    _writable()
    t = str(core_type or "xray").strip().lower()

    if t not in CORE_TYPES:
        raise ValueError(f"the type of a core is one of {', '.join(CORE_TYPES)}")

    name = check_core_name(name)

    if copy_from:
        cfg = copy.deepcopy(_get_core(c, int(copy_from))["config"])
    elif text is not None:
        cfg = unmask_secrets(parse_text(text), {})                # a placeholder of another core's editor stands for nothing here: refused with a sentence
    elif config is not None:
        cfg = config
    else:
        cfg = default_config(t, _all_tags(c))

    errors = check_config(cfg, t)

    if errors:
        raise ValueError("; ".join(errors)[:700])

    body = {"name": name, "type": t, "config": cfg, "exclude_inbound_tags": tag_list(exclude, "excluded tags"), "fallbacks_inbound_tags": tag_list(fallbacks, "fallback tags")}

    with _WRITE_LOCK:
        try:
            made = panels.call(c, "POST", "/api/core", body=body, timeout=120)
        except PanelError as e:
            raise PanelError(f"the panel refused the core ({_scrub(str(e), cfg)})", e.code) from None

        if not isinstance(made, dict) or not isinstance(made.get("id"), int):
            raise PanelError("the panel answered without the id of the new core: look at its cores before you try again")

        core = _norm_core(made)
        _record(c, f"Core “{name}” (#{core['id']}, {t}) made on panel #{c['id']}: {len(_dicts(cfg.get('inbounds')))} inbounds.")

    return {"created": True, "core": _meta(core), "inbounds": len(_dicts(cfg.get("inbounds"))), "outbounds": len(_dicts(cfg.get("outbounds")))}


def delete_warnings(c: dict, cur: dict) -> list[dict]:
    """What deleting a core takes with it: the nodes that run it, and every inbound that hosts and groups of the panel still use."""
    hosts, groups = _usage_sources(c)
    out = [w for w in warnings(cur["config"], {"inbounds": [], "outbounds": [], "routing": {}}, hosts, groups) if w["code"] == "tag_removed"]
    nodes = nodes_on(c, cur["id"])

    if nodes:
        out.insert(0, {"code": "nodes_use_core", "level": "danger", "tag": "", "text": f"{_plural(len(nodes), 'node')} run this core ({', '.join(n['name'] for n in nodes[:6])}): the panel leaves them without a core config until another one is chosen for them."})

    return out


def delete_check(c: dict, core_id: int) -> dict:
    """What deleting this core would take with it, nothing is changed: {allowed, reason, name, warnings, nodes}. The first core is not allowed (the panel keeps it)."""
    cur = _get_core(c, core_id)
    nodes = nodes_on(c, core_id)
    allowed = int(core_id) != DEFAULT_CORE_ID
    return {"allowed": allowed, "reason": "" if allowed else "the first core of the panel is its default one: the panel does not delete it", "name": cur["name"], "warnings": delete_warnings(c, cur), "nodes": nodes,
            "inbounds": len(inbound_rows(cur["config"]))}


def delete(c: dict, core_id: int, confirm: bool = False) -> dict:
    """Delete a core (never the first one, which the panel keeps). Needs `confirm`; the warnings (nodes that run it, hosts and groups that use its inbounds) are listed in the refusal.
    The whole core is written to a backup first: `recreate` makes a new core from it (new id, nodes must be pointed at it again)."""
    _writable()

    if int(core_id) == DEFAULT_CORE_ID:
        raise ValueError("the first core of the panel is its default one: the panel does not delete it")

    with _WRITE_LOCK:
        cur = _get_core(c, core_id)
        warns = delete_warnings(c, cur)

        if warns and not confirm:
            raise NeedsConfirm(warns)

        if not confirm:
            raise ValueError("deleting a core cannot be undone on the panel: send confirm: true (a backup is kept here, see the deleted cores)")

        name = backup_write(c["id"], cur, "before deleting the core")

        try:
            panels.call(c, "DELETE", f"/api/core/{int(core_id)}")
        except PanelError as e:
            (_bdir() / name).unlink(missing_ok=True)                    # nothing was deleted: the backup is not needed
            raise PanelError(f"the panel did not delete the core ({_scrub(str(e), cur['config'])})", e.code) from None

        _record(c, f"Core “{cur['name']}” (#{cur['id']}) of panel #{c['id']} deleted (backup {name}).")

    return {"deleted": True, "backup": name, "warnings": warns}


MAX_BULK_CORES = 20


def delete_many(c: dict, ids, confirm: bool = False) -> dict:
    """Delete several cores in ONE call (the panel's own `POST /api/cores/bulk/delete`, all or none): never the first core; what each of them takes with it (the nodes that run it, the hosts and
    groups that use its inbounds) is collected and needs `confirm`; every core is written to a backup first (`recreate` makes one again)."""
    _writable()
    want = sorted({int(i) for i in ids})

    if not 1 <= len(want) <= MAX_BULK_CORES:
        raise ValueError(f"pick 1 to {MAX_BULK_CORES} cores")

    if DEFAULT_CORE_ID in want:
        raise ValueError("the first core of the panel is its default one: the panel does not delete it")

    with _WRITE_LOCK:
        curs = [_get_core(c, i) for i in want]
        warns = [{**w, "core": cur["name"]} for cur in curs for w in delete_warnings(c, cur)]

        if warns and not confirm:
            raise NeedsConfirm(warns)

        if not confirm:
            raise ValueError("deleting cores cannot be undone on the panel: send confirm: true (a backup of each is kept here, see the deleted cores)")

        backups = [backup_write(c["id"], cur, "before deleting the cores (several at once)") for cur in curs]

        try:
            r = panels.call(c, "POST", "/api/cores/bulk/delete", body={"ids": want}, timeout=120)
        except PanelError as e:
            for name in backups:
                (_bdir() / name).unlink(missing_ok=True)                    # nothing was deleted: the backups are not needed

            raise PanelError(f"the panel did not delete the cores ({_scrub(str(e), *[cur['config'] for cur in curs])})", e.code) from None

        gone = [str(x) for x in r["cores"]] if isinstance(r, dict) and isinstance(r.get("cores"), list) and r["cores"] else [cur["name"] for cur in curs]
        _record(c, f"Cores {', '.join('“' + n + '”' for n in gone)} of panel #{c['id']} deleted in one call (backups {len(backups)}).")

    return {"deleted": gone, "count": len(gone), "backups": backups, "warnings": warns}


def deleted_backups(c: dict) -> list[dict]:
    """The newest backup of every core of this panel that is not on the panel any more (deleted here or there): {core, name, at, file, inbounds, outbounds, type}."""
    try:
        raw = panels.call(c, "GET", "/api/cores")
    except (PanelError, ValueError):
        return []

    items = raw.get("cores") if isinstance(raw, dict) else raw
    live = {int(x["id"]) for x in (items if isinstance(items, list) else []) if isinstance(x, dict) and isinstance(x.get("id"), int)}
    best: dict[int, dict] = {}

    for f in sorted(_bdir().glob(f"cores-{int(c['id'])}-*-*.json")):
        m = _BACKUP_NAME.match(f.name)

        if not m or int(m.group(2)) in live:
            continue

        try:
            body = json.loads(f.read_text(encoding="utf-8"))
            cfg = _obj(body.get("config"))
        except (OSError, ValueError, AttributeError):
            continue

        best[int(m.group(2))] = {"core": int(m.group(2)), "name": str(body.get("name") or ""), "type": str(body.get("type") or "xray"), "at": body.get("at"), "file": f.name,
                                 "inbounds": len(_dicts(cfg.get("inbounds"))), "outbounds": len(_dicts(cfg.get("outbounds"))), "note": str(body.get("note") or "")}

    return sorted(best.values(), key=lambda r: -(r["at"] or 0))


def recreate(c: dict, core_id: int, name: str) -> dict:
    """Make a new core from the backup of a core that is gone (the name, type, config and tag lists of the backup; the new core has a new id)."""
    stored = _backup_load(c["id"], core_id, name)
    return create(c, stored.get("name") or f"core {core_id}", stored.get("type") or "xray", config=stored["config"], exclude=stored.get("exclude_inbound_tags"), fallbacks=stored.get("fallbacks_inbound_tags"))


def update_meta(c: dict, core_id: int, name=None, exclude=None, fallbacks=None, base_rev: str | None = None) -> dict:
    """Rename a core and/or change its two tag lists (excluded and fallback inbound tags); the config is sent back as it is. The core is backed up first and read back."""
    _writable()

    with _WRITE_LOCK:
        cur = _get_core(c, core_id)

        if base_rev and base_rev != rev_of(cur):
            raise Conflict("the core was changed on the panel after you opened it: reload it and make the change again")

        new = {**cur, "name": check_core_name(name) if name is not None else cur["name"], "exclude": tag_list(exclude, "excluded tags") if exclude is not None else cur["exclude"],
               "fallbacks": tag_list(fallbacks, "fallback tags") if fallbacks is not None else cur["fallbacks"]}

        if (new["name"], new["exclude"], new["fallbacks"]) == (cur["name"], cur["exclude"], cur["fallbacks"]):
            raise ValueError("nothing to change: the name and the tag lists are the same")

        bk = backup_write(c["id"], cur, "before a change of the name or the tag lists")

        try:
            _put(c, new, cur["config"])
        except PanelError as e:
            (_bdir() / bk).unlink(missing_ok=True)
            raise PanelError(f"the panel refused the change ({_scrub(str(e), cur['config'])})", e.code) from None

        back = _get_core(c, core_id)
        _record(c, f"Core “{cur['name']}” (#{cur['id']}) of panel #{c['id']}: name / tag lists changed (backup {bk}).")

    return {"saved": True, "backup": bk, "core": _meta(back), "rev": rev_of(back)}


def reality_scan(c: dict, target: str, timeout: float | None = None) -> dict:
    """Ask the panel whether a site can be a REALITY target (POST /api/core/reality-scan): TLS 1.3, h2, x25519, the names of its certificate, the delay. Reads only."""
    t = str(target or "").strip()

    if not t or not _HOSTPORT.match(t) or "://" in t:
        raise ValueError("the target is a name or an address, with a port if it is not 443 (for example www.example.com:443)")

    body: dict = {"target": t}

    if timeout is not None:
        body["timeout"] = max(1.0, min(30.0, float(timeout)))

    r = panels.call(c, "POST", "/api/core/reality-scan", body=body, timeout=60)
    return r if isinstance(r, dict) else {}


# ---- one inbound at a time: the builder (a flat form -> the Xray inbound), and the change of a config
def _int_port(v) -> int:
    try:
        p = int(str(v).strip())
    except (TypeError, ValueError):
        raise ValueError("the port is a number from 1 to 65535") from None

    if not 1 <= p <= 65535:
        raise ValueError("the port is a number from 1 to 65535")

    return p


def reality_keys() -> dict:
    """A new X25519 key pair for REALITY, written the way Xray writes it (URL-safe base64 without padding)."""
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import x25519

    k = x25519.X25519PrivateKey.generate()
    return {"private_key": _b64(k.private_bytes(serialization.Encoding.Raw, serialization.PrivateFormat.Raw, serialization.NoEncryption())),
            "public_key": _b64(k.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw))}


def _b64(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).decode().rstrip("=")


def public_of(private_key: str) -> str:
    """The public key of a REALITY private key (both URL-safe base64 without padding); ValueError when it is not an X25519 key."""
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import x25519

    try:
        raw = base64.urlsafe_b64decode(str(private_key).strip() + "=" * (-len(str(private_key).strip()) % 4))
        k = x25519.X25519PrivateKey.from_private_bytes(raw)
    except (ValueError, TypeError):
        raise ValueError("that is not a REALITY private key (43 characters of URL-safe base64)") from None

    return _b64(k.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw))


def _words(v) -> list[str]:
    if isinstance(v, str):
        v = re.split(r"[,\s]+", v)

    return [str(x).strip() for x in (v or []) if str(x).strip()]


def build_inbound(spec: dict, taken: set[str] | None = None, preview: bool = False) -> tuple[dict, str | None]:
    """The Xray inbound for a flat form: (the inbound, the public key of its REALITY key). The REALITY private key is `reality_private_key` of the form (its public key is worked out) or a new
    pair; a `preview` never makes a real key (a dummy stands in, and no public key is given). `taken` are the tags already used. The inbound has no clients: the panel puts its users in at run time."""
    s = dict(spec or {})
    proto, network, security = (str(s.get(k) or d).strip().lower() for k, d in (("protocol", "vless"), ("network", "tcp"), ("security", "none")))

    if proto not in PROTOCOLS:
        raise ValueError(f"the protocol is one of {', '.join(PROTOCOLS)} (anything else: write the JSON)")

    if network not in NETWORKS:
        raise ValueError(f"the network is one of {', '.join(NETWORKS)}")

    if security not in SECURITIES:
        raise ValueError(f"the security is one of {', '.join(SECURITIES)}")

    if security == "reality" and (proto != "vless" and proto != "trojan" and proto != "vmess"):
        raise ValueError("REALITY needs a vless, vmess or trojan inbound")

    port = _int_port(s.get("port"))
    tag = str(s.get("tag") or "").strip() or _free_tag(f"{proto.upper()} {network.upper()} {security.upper()}".replace(" NONE", ""), taken or set())

    if "," in tag or "<=>" in tag:
        raise ValueError("a tag cannot hold a comma or <=>")

    settings: dict = {"clients": []}
    flow = str(s.get("flow") or "").strip()

    if flow:
        if proto != "vless" or flow not in FLOWS:
            raise ValueError(f"the flow is one of {', '.join(FLOWS)} and only a vless inbound has one")

        if network != "tcp" or security == "none":
            raise ValueError(f"the flow {flow} needs a TCP inbound with TLS or REALITY")

    enc, dec = str(s.get("encryption") or "").strip(), str(s.get("decryption") or "").strip()

    bad_enc = proto != "vless" or not (enc.startswith("mlkem768x25519plus.") and dec.startswith("mlkem768x25519plus.")) or re.search(r"\s", enc + dec)      # VLESS Encryption (ML-KEM): the server string (decryption) and the client string (encryption) of `xray vlessenc`

    if (enc or dec) and bad_enc:
        raise ValueError("VLESS Encryption needs a vless inbound and the two strings of `xray vlessenc` (both start with mlkem768x25519plus.)")

    if proto == "vless":
        settings["decryption"] = dec or "none"

        if enc:
            settings["encryption"] = enc                              # (PasarGuard reads the encryption of the links from the inbound)

        if flow:
            settings["flow"] = flow                                    # (PasarGuard reads the flow of the links from the inbound)
    elif proto == "shadowsocks":
        settings["network"] = "tcp,udp"

    stream: dict = {"network": network, "security": security}
    path, host = str(s.get("path") or "").strip(), str(s.get("host") or "").strip()

    if network == "tcp" and str(s.get("header") or "").strip().lower() == "http":      # TCP with the HTTP header: the links say headerType=http and a path (and a Host)
        req: dict = {"path": [path or "/"]}

        if host:
            req["headers"] = {"Host": [host]}

        stream["tcpSettings"] = {"header": {"type": "http", "request": req}}
    elif network == "tcp":
        stream["tcpSettings"] = {}
    elif network == "ws":
        ws: dict = {"path": path or "/"}

        if host:
            ws["headers"] = {"Host": host}

        stream["wsSettings"] = ws
    elif network == "grpc":
        stream["grpcSettings"] = {"serviceName": str(s.get("service") or "").strip(), "multiMode": True}
    elif network == "httpupgrade":
        stream["httpupgradeSettings"] = {"path": path or "/", "host": host}
    else:
        mode = str(s.get("mode") or "auto").strip().lower()

        if mode not in XHTTP_MODES:
            raise ValueError(f"the xhttp mode is one of {', '.join(XHTTP_MODES)}")

        stream["xhttpSettings"] = {"path": path or "/", "host": host, "mode": mode}

        if s.get("xhttp_tuned"):                                        # the three settings the owner's links carry in their extra (the panel puts them into the link)
            stream["xhttpSettings"].update({"scMaxEachPostBytes": "1000000", "scMinPostsIntervalMs": 30, "xPaddingBytes": "100-1000"})

    pub = None

    if security == "tls":
        cert, kf = str(s.get("tls_cert") or "").strip(), str(s.get("tls_key") or "").strip()
        pem_c, pem_k = str(s.get("tls_cert_pem") or "").strip(), str(s.get("tls_key_pem") or "").strip()

        if pem_c and pem_k:                                            # a certificate written into the config itself (the lines of its PEM): nothing to put on the nodes
            one = {"certificate": pem_c.splitlines(), "key": pem_k.splitlines()}
        elif cert and kf:
            one = {"certificateFile": cert, "keyFile": kf}
        else:
            raise ValueError("TLS needs the path of the certificate and of its key on the node (for example /var/lib/pg-node/certs/ssl_cert.pem)")

        tls: dict = {"certificates": [one], "minVersion": "1.2"}
        alpn = _words(s.get("alpn"))

        if alpn:
            tls["alpn"] = alpn

        stream["tlsSettings"] = tls
    elif security == "reality":
        dest = str(s.get("reality_dest") or "").strip()
        names = _words(s.get("reality_names"))

        if not dest or not _HOSTPORT.match(dest) or "://" in dest:
            raise ValueError("REALITY needs the site it imitates, like www.example.com:443")

        if not names:
            names = [dest.rsplit(":", 1)[0] if dest.count(":") == 1 else dest]

        ids = _words(s.get("reality_short_ids")) or [secrets.token_hex(8)]

        if not all(re.fullmatch(r"[0-9a-fA-F]{0,16}", i) and len(i) % 2 == 0 for i in ids):
            raise ValueError("a REALITY short id is up to 16 hex characters, an even number of them")

        given = str(s.get("reality_private_key") or "").strip()

        if given:
            keys = {"private_key": given, "public_key": public_of(given)}
        elif preview:
            keys = {"private_key": "PREVIEW-ONLY-KEY-NOT-STORED", "public_key": None}
        else:
            keys = reality_keys()

        pub = keys["public_key"]
        stream["realitySettings"] = {"show": False, "dest": dest if ":" in dest else dest + ":443", "xver": 0, "serverNames": names, "privateKey": keys["private_key"], "shortIds": ids}
        spx = str(s.get("reality_spx") or "").strip()

        if spx:
            if not spx.startswith("/") or len(spx) > 200 or any(ord(ch) < 33 for ch in spx):
                raise ValueError("the SpiderX path starts with a slash and has no space (for example /api)")

            stream["realitySettings"]["spiderX"] = spx                # (the panel puts it into the links as spx)

    inbound: dict = {"tag": tag, "listen": str(s.get("listen") or "0.0.0.0").strip(), "port": port, "protocol": proto, "settings": settings, "streamSettings": stream}

    if s.get("sniffing", True):
        inbound["sniffing"] = {"enabled": True, "destOverride": ["http", "tls", "quic"]}

    return inbound, pub


def inbound_change(cfg: dict, op: str, tag: str | None = None, inbound: dict | None = None) -> dict:
    """Pure: the config after one change of one inbound - op "add" (a new inbound), "set" (replace the inbound called `tag`, its tag may change) or "delete"."""
    new = copy.deepcopy(cfg)
    lst = new.get("inbounds")

    if not isinstance(lst, list):
        raise ValueError("the config has no inbounds list")  # noqa: TRY004 - the caller shows the ValueError text to the operator

    idx = next((i for i, ib in enumerate(lst) if isinstance(ib, dict) and ib.get("tag") == tag), None) if tag is not None else None

    if op == "add":
        if not isinstance(inbound, dict) or not str(inbound.get("tag") or "").strip():
            raise ValueError("the inbound needs a tag")

        if any(isinstance(ib, dict) and ib.get("tag") == inbound["tag"] for ib in lst):
            raise ValueError(f"an inbound called “{inbound['tag']}” exists already")

        lst.append(inbound)
    elif op == "set":
        if idx is None:
            raise ValueError(f"no inbound is called “{tag}”")

        if not isinstance(inbound, dict):
            raise ValueError("the inbound must be a JSON object")

        lst[idx] = inbound
    elif op == "delete":
        if idx is None:
            raise ValueError(f"no inbound is called “{tag}”")

        del lst[idx]
    else:
        raise ValueError("op is add, set or delete")

    return new


def _inbound_candidate(cur: dict, op: str, tag: str | None, spec: dict | None, text: str | None, taken: set[str], preview: bool) -> tuple[dict, dict | None, str | None]:
    """(the new config, the inbound as it will be stored, the public key made now): from the form (`spec`) or the inbound's JSON (`text`, its placeholders put back from the core)."""
    ib, pub = None, None

    if op == "delete":
        return inbound_change(cur["config"], op, tag), None, None

    if spec is not None:
        ib, pub = build_inbound(spec, taken, preview=preview)
    elif text is not None:
        ib = unmask_secrets(parse_text(text), cur["config"])
    else:
        raise ValueError("send the inbound as `spec` (the form) or `text` (its JSON)")

    return inbound_change(cur["config"], op, tag, ib), ib, pub


def inbound_dry_run(c: dict, core_id: int, op: str, tag: str | None = None, spec: dict | None = None, text: str | None = None) -> dict:
    """What one change of one inbound would do, written nowhere: the answer of a dry run of the whole config (checks, warnings about the hosts and groups that use the inbound, the diff) plus
    `inbound`, the inbound as it would be stored (secrets as placeholders)."""
    cur = _get_core(c, core_id)

    try:
        new, ib, _pub = _inbound_candidate(cur, op, tag, spec, text, _all_tags(c) if op == "add" else set(), True)
    except ValueError as e:
        return {**_dry_of(c, cur, None, [str(e)]), "inbound": None}

    out = _dry_of(c, cur, new, [])
    out["inbound"] = mask_secrets(ib)[0] if ib is not None else None
    return out


def inbound_apply(c: dict, core_id: int, op: str, tag: str | None = None, spec: dict | None = None, text: str | None = None, confirm: bool = False, restart_after: bool = False,
                  base_rev: str | None = None) -> dict:
    """Add, replace or delete ONE inbound of a core: everything a save does (the checks, the warnings that need `confirm`, the backup first, the read back, the optional restart)
    with the change built here. A REALITY inbound made from the form gets a new key pair: the answer carries its public key (the private one never leaves)."""
    _writable()
    cur = _get_core(c, core_id)
    new, ib, pub = _inbound_candidate(cur, op, tag, spec, text, _all_tags(c) if op == "add" else set(), False)
    note = {"add": f"before adding the inbound {_obj(ib).get('tag')}", "set": f"before changing the inbound {tag}", "delete": f"before deleting the inbound {tag}"}[op]
    out = save(c, core_id, config=new, confirm=confirm, restart_after=restart_after, base_rev=base_rev or rev_of(cur), note=note)
    out["inbound"] = mask_secrets(ib)[0] if ib is not None else None
    out["public_key"] = pub
    return out

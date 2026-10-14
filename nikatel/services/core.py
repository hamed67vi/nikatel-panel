"""Outbound manager: read / change the Xray core config of a Marzban or PasarGuard panel.

Every change is validated first (`validate`), the old config is written to <data>/core-backups/
and read back after the write; when the panel did not keep it, the old config is put back."""
from __future__ import annotations

import json
import os
import re
import time

from ..config import get_settings
from ..db import session
from ..log import get_logger
from .events import emit
from .gateway import outbound_from_uri
from .panels import PanelError, call

log = get_logger("core")
KEEP = 30
_TAG = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,40}$")


def _dir():
    d = get_settings().data_dir / "core-backups"
    d.mkdir(parents=True, exist_ok=True)
    return d


# ------------------------------------------------------------------ pure
def validate(cfg) -> None:
    """Raises ValueError with a plain sentence when the config would break the panel."""
    if not isinstance(cfg, dict):
        raise ValueError("the config is not an object")  # noqa: TRY004 - the caller shows ValueError text to the operator

    ib, ob = cfg.get("inbounds"), cfg.get("outbounds")

    if not isinstance(ib, list) or not ib:
        raise ValueError("the config has no inbounds: refusing to save it")

    if not isinstance(ob, list) or not ob:
        raise ValueError("the config has no outbounds")

    tags = []

    for o in ob:
        if not isinstance(o, dict) or not o.get("protocol"):
            raise ValueError("an outbound has no protocol")

        if not o.get("tag"):
            raise ValueError("every outbound needs a tag")

        tags.append(str(o["tag"]))

    if len(set(tags)) != len(tags):
        raise ValueError("two outbounds have the same tag")

    if not any(o.get("protocol") == "freedom" for o in ob):
        raise ValueError("keep at least one direct (freedom) outbound")

    rt = cfg.get("routing") or {}
    balancers = {str(b.get("tag")) for b in (rt.get("balancers") or []) if isinstance(b, dict)}

    for r in rt.get("rules") or []:
        if not isinstance(r, dict):
            raise ValueError("a routing rule is not an object")  # noqa: TRY004

        t = r.get("outboundTag")

        if t and str(t) not in tags:
            raise ValueError(f"a routing rule sends traffic to “{t}”, which is not an outbound")

        bt = r.get("balancerTag")

        if bt and str(bt) not in balancers:
            raise ValueError(f"a routing rule uses the balancer “{bt}”, which does not exist")

    for b in rt.get("balancers") or []:
        for sel in b.get("selector") or []:
            if not any(str(t).startswith(str(sel)) for t in tags):
                raise ValueError(f"the balancer “{b.get('tag')}” selects “{sel}”, which matches no outbound")


def summary(cfg: dict) -> dict:
    """What the UI shows: outbounds with where they go, the default outbound, rules in one line each."""
    out = []

    for o in cfg.get("outbounds") or []:
        s = o.get("settings") or {}
        srv = (s.get("vnext") or s.get("servers") or [{}])[0] if isinstance(s, dict) else {}
        target = f"{srv.get('address', '')}:{srv.get('port', '')}" if isinstance(srv, dict) and srv.get("address") else ""
        out.append({"tag": o.get("tag"), "protocol": o.get("protocol"), "target": target,
                    "network": ((o.get("streamSettings") or {}).get("network") or "tcp") if o.get("protocol") not in ("freedom", "blackhole", "dns") else ""})

    rt = cfg.get("routing") or {}
    rules = []

    for r in rt.get("rules") or []:
        where = []

        for k in ("domain", "ip", "inboundTag", "protocol", "port", "network", "user", "source"):
            v = r.get(k)

            if v:
                where.append(f"{k}: " + (", ".join(map(str, v))[:60] if isinstance(v, list) else str(v)[:60]))

        rules.append({"to": r.get("outboundTag") or ("balancer " + str(r.get("balancerTag", ""))), "when": "; ".join(where) or "everything else"})

    default = (cfg.get("outbounds") or [{}])[0].get("tag", "") if cfg.get("outbounds") else ""
    return {"outbounds": out, "default": default, "rules": rules, "balancers": [b.get("tag") for b in rt.get("balancers") or []]}


def with_outbound(cfg: dict, ob: dict, make_default: bool = False) -> dict:
    """A copy of cfg with the outbound added (or replaced by tag); Xray's default is the first outbound."""
    new = json.loads(json.dumps(cfg))
    lst = [o for o in new.get("outbounds") or [] if o.get("tag") != ob["tag"]]

    if make_default:
        lst.insert(0, ob)
    else:
        lst.append(ob)

    new["outbounds"] = lst
    return new


def without_outbound(cfg: dict, tag: str) -> dict:
    new = json.loads(json.dumps(cfg))
    new["outbounds"] = [o for o in new.get("outbounds") or [] if o.get("tag") != tag]
    rt = new.get("routing") or {}
    rt["rules"] = [r for r in rt.get("rules") or [] if r.get("outboundTag") != tag]

    for b in rt.get("balancers") or []:
        b["selector"] = [s for s in b.get("selector") or [] if not str(tag).startswith(str(s))]

    new["routing"] = rt
    return new


def as_default(cfg: dict, tag: str) -> dict:
    new = json.loads(json.dumps(cfg))
    lst = new.get("outbounds") or []
    pick = [o for o in lst if o.get("tag") == tag]

    if not pick:
        raise ValueError("no outbound has that tag")

    new["outbounds"] = pick + [o for o in lst if o.get("tag") != tag]
    return new


# ------------------------------------------------------------------ panel I/O
def _kind(c: dict):
    """Marzban keeps one core config at /api/core/config; PasarGuard keeps a list at /api/cores."""
    try:
        r = call(c, "GET", "/api/cores")

        if isinstance(r, dict) and isinstance(r.get("cores"), list) and r["cores"]:
            return "pasarguard", r["cores"]
    except PanelError:
        pass

    return "marzban", None


def get(c: dict) -> dict:
    kind, cores = _kind(c)

    if kind == "pasarguard":
        core = cores[0]
        d = call(c, "GET", f"/api/core/{int(core['id'])}") or {}
        return {"kind": kind, "core_id": int(core["id"]), "name": d.get("name"), "config": d.get("config") or {},
                "extra": {k: d.get(k) for k in ("name", "type", "exclude_inbound_tags", "fallbacks_inbound_tags")}}

    return {"kind": kind, "core_id": None, "name": "core", "config": call(c, "GET", "/api/core/config") or {}, "extra": {}}


def _put(c: dict, cur: dict, cfg: dict):
    if cur["kind"] == "pasarguard":
        body = {"name": cur["extra"].get("name") or "Default Core Config", "config": cfg,
                "exclude_inbound_tags": cur["extra"].get("exclude_inbound_tags") or [], "fallbacks_inbound_tags": cur["extra"].get("fallbacks_inbound_tags") or []}
        # PasarGuard 5.x REQUIRES `restart_nodes` (the nodes use a saved config only after a restart, as Marzban's PUT does by itself): without it the panel answers 422.
        # `modify_users` is the older name of a parameter; a newer panel ignores it.
        return call(c, "PUT", f"/api/core/{cur['core_id']}?restart_nodes=true&modify_users=false", body=body, timeout=120)

    return call(c, "PUT", "/api/core/config", body=cfg, timeout=120)


def backup_save(panel_id: int, cfg: dict, note: str = "") -> str:
    d = _dir()
    stamp = int(time.time() * 1000)
    name = f"{int(panel_id)}-{stamp}.json"

    while (d / name).exists():          # two changes in the same millisecond: never overwrite a backup
        stamp += 1
        name = f"{int(panel_id)}-{stamp}.json"

    path = d / name
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)

    with os.fdopen(fd, "w") as f:
        json.dump({"panel": int(panel_id), "at": int(time.time()), "note": str(note)[:120], "config": cfg}, f)

    old = sorted(x.name for x in d.glob(f"{int(panel_id)}-*.json"))

    for x in old[:-KEEP]:
        (d / x).unlink(missing_ok=True)

    return name


def backups(panel_id: int) -> list[dict]:
    out = []

    for x in sorted(_dir().glob(f"{int(panel_id)}-*.json"), reverse=True):
        try:
            d = json.loads(x.read_text())
            out.append({"file": x.name, "at": d.get("at"), "note": d.get("note", ""), "outbounds": [o.get("tag") for o in (d.get("config") or {}).get("outbounds", [])]})
        except (OSError, ValueError):
            continue

    return out[:KEEP]


def save(c: dict, cfg: dict, note: str = "") -> dict:
    """Validate, back the old config up, write the new one, read it back; put the old one back when the read-back differs."""
    if get_settings().read_only:
        raise ValueError("the panel is read-only; set NIKATEL_READ_ONLY=false to change a core config")

    validate(cfg)
    cur = get(c)
    name = backup_save(c["id"], cur["config"], note or "before a change from the panel")
    want = [o.get("tag") for o in cfg.get("outbounds", [])]

    try:
        _put(c, cur, cfg)
        back = get(c)["config"]

        if [o.get("tag") for o in back.get("outbounds", [])] != want:
            raise PanelError("the panel did not keep the new outbounds")
    except (PanelError, ValueError) as e:
        try:
            _put(c, cur, cur["config"])
        except (PanelError, ValueError) as e2:
            raise RuntimeError(f"not applied ({str(e)[:100]}) and the old config could not be put back ({str(e2)[:80]}): restore backup {name}")

        raise RuntimeError(f"not applied ({str(e)[:140]}); the old config was put back")

    with session() as db:
        emit(db, "info", "panels", f"Core config of panel #{c['id']} changed: outbounds {', '.join(map(str, want))[:120]} (backup {name}).", str(c["id"]))

    return {"backup": name, "outbounds": want}


def restore(c: dict, name: str) -> dict:
    if not re.fullmatch(rf"{int(c['id'])}-\d+\.json", str(name)):
        raise ValueError("unknown backup")

    path = _dir() / name

    if not path.exists():
        raise ValueError("unknown backup")

    d = json.loads(path.read_text())
    return save(c, d["config"], "before restoring " + name)


def add_from_link(c: dict, uri: str, tag: str, make_default: bool = False) -> dict:
    if not _TAG.match(str(tag or "")):
        raise ValueError("the tag may use letters, digits, . _ - (max 40)")

    ob = outbound_from_uri(uri, tag)
    cur = get(c)
    return save(c, with_outbound(cur["config"], ob, make_default), f"before adding outbound {tag}")

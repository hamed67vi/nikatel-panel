"""PasarGuard: the SYSTEM view of a PasarGuard 5.4 panel in one answer - the machine it runs on (CPU, memory, disk, uptime, version), the users by state, the traffic, the two internal
workers (scheduler and node) with their answer time, the WireGuard address pools and the inbounds the panel knows (the owner asked for every section of the panel, 2026-10-14).
Read only: nothing here writes to the panel. Each read is separate and a read the panel refuses (a role without `system.read`, a panel without WireGuard) is named in `warnings`
instead of failing the page. Routes read: GET /api/system, /api/workers/health, /api/wireguard/subnets, /api/inbounds/details (and /api/inbounds for older panels)."""
from __future__ import annotations

from .panels import PanelError
from .pg_admin import _call, _dict, _list


def _int(v):
    return v if isinstance(v, int) and not isinstance(v, bool) else None


def _pct(used, total):
    return round(used * 100.0 / total, 1) if isinstance(used, (int, float)) and isinstance(total, (int, float)) and total > 0 else None


def machine(s) -> dict:
    """The machine part of /api/system: version, uptime, CPU, memory and disk with their percentages (None when the panel does not say)."""
    s = _dict(s)
    cpu = s.get("cpu_usage")
    return {"version": str(s.get("version") or ""), "uptime_seconds": _int(s.get("uptime_seconds")), "cpu_cores": _int(s.get("cpu_cores")), "cpu_percent": round(float(cpu), 1) if isinstance(cpu, (int, float)) and not isinstance(cpu, bool) else None,
            "mem_total": _int(s.get("mem_total")), "mem_used": _int(s.get("mem_used")), "mem_percent": _pct(s.get("mem_used"), s.get("mem_total")),
            "disk_total": _int(s.get("disk_total")), "disk_used": _int(s.get("disk_used")), "disk_percent": _pct(s.get("disk_used"), s.get("disk_total"))}


def users_of(s) -> dict:
    s = _dict(s)
    keys = ("total_user", "online_users", "active_users", "on_hold_users", "disabled_users", "expired_users", "limited_users", "incoming_bandwidth", "outgoing_bandwidth")
    out = {k: _int(s.get(k)) for k in keys}
    return {"total": out["total_user"], "online": out["online_users"], "active": out["active_users"], "on_hold": out["on_hold_users"], "disabled": out["disabled_users"], "expired": out["expired_users"],
            "limited": out["limited_users"], "bytes_in": out["incoming_bandwidth"], "bytes_out": out["outgoing_bandwidth"]}


def workers_of(w) -> list[dict]:
    """The two workers of the panel as rows: {name, status, ms, error}."""
    w = _dict(w)
    return [{"name": k, "status": str(_dict(w.get(k)).get("status") or "unknown"), "ms": _int(_dict(w.get(k)).get("response_time_ms")), "error": str(_dict(w.get(k)).get("error") or "")} for k in ("scheduler", "node") if k in w]


def pools_of(w) -> list[dict]:
    """The WireGuard address pools: {subnet, tags, capacity, used, free, percent, free_ips (the first ones)}."""
    out = []

    for p in _list(w):
        p = _dict(p)

        if not p.get("subnet"):
            continue

        cap, used = _int(p.get("capacity")), _int(p.get("used"))
        out.append({"subnet": str(p["subnet"]), "tags": [str(t) for t in _list(p.get("interface_tags"))], "capacity": cap, "used": used, "free": _int(p.get("free")), "percent": _pct(used, cap),
                    "free_ips": [str(i) for i in _list(p.get("free_ips"))[:5]]})

    return out


def inbounds_of(raw) -> list[dict]:
    """The inbounds the panel knows: {tag, protocol, network} from /api/inbounds/details; a panel that only lists the tags gives rows with the tag alone."""
    out = []

    for i in _list(raw):
        if isinstance(i, dict) and i.get("tag"):
            out.append({"tag": str(i["tag"]), "protocol": str(i.get("protocol") or ""), "network": str(i.get("network") or "")})
        elif isinstance(i, str) and i:
            out.append({"tag": i, "protocol": "", "network": ""})

    return sorted(out, key=lambda r: r["tag"].lower())


def system(c: dict) -> dict:
    """Everything above in one answer; `warnings` names each read the panel did not answer."""
    out: dict = {"machine": None, "users": None, "workers": [], "wireguard": [], "inbounds": [], "warnings": []}

    def get(path: str, what: str):
        try:
            return _call(c, "GET", path)
        except PanelError as e:
            out["warnings"].append(f"{what}: {str(e)[:140]}")
            return None

    s = get("/api/system", "the system numbers")

    if s is not None:
        out["machine"], out["users"] = machine(s), users_of(s)

    w = get("/api/workers/health", "the workers")

    if w is not None:
        out["workers"] = workers_of(w)

    wg = get("/api/wireguard/subnets", "the WireGuard pools")

    if wg is not None:
        out["wireguard"] = pools_of(wg)

    ib = get("/api/inbounds/details", "the inbounds")

    if ib is None:
        ib = get("/api/inbounds", "the inbound tags")

    if ib is not None:
        out["inbounds"] = inbounds_of(ib)

    return out

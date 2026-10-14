"""PasarGuard: what the Nodes tab still lacked of the node API of a 5.4.1 panel (the owner asked for every section of the panel, 2026-10-14): actions on SEVERAL nodes at once,
the resource curves of a node (CPU, memory, speed), the latency of the outbounds a node tests, the count of online / expired / limited users over time, a restart of every node, and the
maintenance that empties the usage tables. Built on pg_nodes (the node as a row, the backups of a node, the sentence in the events).

What the panel does, and so what is guarded here:
  * `POST /api/nodes/bulk/{enable|disable|delete|reconnect|reset|update}` takes `{ids}`; delete drops the traffic history of every node and cuts whoever is on it, so every node is saved to
    a file first (`backup_node`, the Nodes tab's Backups can make it again), and everything except enable needs `confirm`;
  * `POST /api/nodes/reconnect` restarts the core on EVERY node (`core_id` limits it to the nodes of one core): customers drop for a moment on all of them - `confirm` is needed;
  * `DELETE /api/nodes/clear_usage_data/{table}` (node_user_usages | node_usages) removes the traffic history for good, for a time range or entirely: the caller must type the table
    name back (`typed`), as a bare `confirm` is too easy to send by mistake;
  * the outbound latency rows carry the outbound's LINK, which holds its credentials: only scheme, host and port leave this module."""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from urllib.parse import urlsplit

import httpx

from . import panels
from .pg_nodes import (
    PERIODS,
    _int,
    _node_raw,
    _note,
    _npath,
    _num,
    _pct,
    _text,
    backup_node,
)

BULK = ("enable", "disable", "delete", "reconnect", "reset", "update")
NEEDS_OK = frozenset(BULK) - {"enable"}                  # what cuts users, drops counters or cannot be undone on the panel
USAGE_TABLES = ("node_user_usages", "node_usages")
METRICS = ("online", "expired", "limited")
MAX_BULK = 200


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _range(hours: int, period: str) -> tuple[str, str]:
    """(start, end) for the last `hours` hours: at most 14 days of hours, a year of days or months, like the usage window."""
    cap = 24 * 14 if period == "hour" else 24 * 366
    end = datetime.now(timezone.utc).replace(microsecond=0)
    return _iso(end - timedelta(hours=min(cap, max(1, int(hours))))), _iso(end)


def check_ids(ids) -> list[int]:
    """The node ids of a bulk call: a list of 1 to 200 whole numbers, no doubles (the order is kept)."""
    if not isinstance(ids, list) or not ids:
        raise ValueError("choose at least one node")

    out: list[int] = []

    for i in ids:
        if isinstance(i, bool) or not isinstance(i, int) or i < 1:
            raise ValueError("a node id is a whole number from 1")

        if i not in out:
            out.append(i)

    if len(out) > MAX_BULK:
        raise ValueError(f"at most {MAX_BULK} nodes at a time")

    return out


def safe_link(link) -> str:
    """The outbound link of a latency row without its credentials: scheme://host:port, or "" when it is not a link."""
    try:
        u = urlsplit(str(link or ""))
        return f"{u.scheme}://{u.hostname}{':' + str(u.port) if u.port else ''}" if u.scheme and u.hostname else ""
    except ValueError:
        return ""


def stat_rows(answer) -> list[dict]:
    """Pure: the rows of /api/node/{id}/stats: [{t, cpu, mem, rx, tx}] (percent, percent, bytes per second)."""
    rows = answer.get("stats") if isinstance(answer, dict) else None
    return [{"t": _text(r.get("period_start"), 25), "cpu": _pct(r.get("cpu_usage_percentage")), "mem": _pct(r.get("mem_usage_percentage")), "rx": _num(r.get("incoming_bandwidth_speed"), None),
             "tx": _num(r.get("outgoing_bandwidth_speed"), None)} for r in rows if isinstance(r, dict)] if isinstance(rows, list) else []


def latency_rows(answer) -> list[dict]:
    """Pure: the rows of /api/node/{id}/outbounds_latency: [{name, alive, ms, target, seen, tried, source}] - no credentials."""
    rows = answer.get("latencies") if isinstance(answer, dict) else None
    return [{"name": _text(r.get("name"), 80), "alive": bool(r.get("alive")), "ms": _int(r.get("delay")), "target": safe_link(r.get("link")), "seen": _int(r.get("last_seen_time")),
             "tried": _int(r.get("last_try_time")), "source": _text(r.get("source"), 40)} for r in rows if isinstance(r, dict)] if isinstance(rows, list) else []


def count_rows(answer, metric: str) -> dict:
    """Pure: the answer of /api/node/user_counts/{metric}: {count (during the period), rows [{t, n}]} - one list of rows even when the panel groups them by node."""
    a = answer if isinstance(answer, dict) else {}
    stats = a.get("stats")
    rows: list = []

    if isinstance(stats, dict):
        for series in stats.values():
            rows += [x for x in series if isinstance(x, dict)] if isinstance(series, list) else []
    elif isinstance(stats, list):
        rows = [x for x in stats if isinstance(x, dict)]

    merged: dict[str, int] = {}

    for r in rows:
        t = _text(r.get("period_start"), 25)
        merged[t] = merged.get(t, 0) + (_int(r.get("count")) or 0)

    return {"metric": metric, "count": _int(a.get("count_during_period")) or 0, "rows": [{"t": t, "n": n} for t, n in sorted(merged.items())]}


# ------------------------------------------------------------------ reads
def node_stats(c: dict, node_id: int, period: str = "hour", hours: int = 24) -> dict:
    """The resource curves of one node: CPU and memory percent and the speed in and out, per hour or day, with the highest and the average of each."""
    if period not in PERIODS:
        raise ValueError(f"period must be one of: {', '.join(PERIODS)}")

    start, end = _range(hours, period)
    rows = stat_rows(panels.call(c, "GET", f"{_npath(node_id)}/stats?{httpx.QueryParams({'period': period, 'start': start, 'end': end})}"))

    def top(k):
        v = [r[k] for r in rows if r[k] is not None]
        return {"max": max(v) if v else None, "avg": round(sum(v) / len(v), 1) if v else None}

    return {"node_id": int(node_id), "period": period, "hours": int(hours), "rows": rows, "summary": {k: top(k) for k in ("cpu", "mem", "rx", "tx")}}


def node_latency(c: dict, node_id: int, name: str = "", timeout: int | None = None) -> dict:
    """The delay of the outbounds a node tests (the panel asks the node; `name` limits it to one outbound). Reads only; the answer may take a few seconds."""
    q = {"name": str(name or "")[:80]}

    if timeout is not None:
        q["timeout"] = max(1, min(30, int(timeout)))

    rows = latency_rows(panels.call(c, "GET", f"{_npath(node_id)}/outbounds_latency?{httpx.QueryParams(q)}", timeout=60))
    return {"node_id": int(node_id), "rows": rows, "alive": sum(1 for r in rows if r["alive"]), "total": len(rows)}


def user_counts(c: dict, metric: str = "online", period: str = "hour", hours: int = 24, node_id: int | None = None) -> dict:
    """How many users were online / expired / limited, per hour or day, for the whole panel or one node."""
    if metric not in METRICS:
        raise ValueError(f"the metric is one of: {', '.join(METRICS)}")

    if period not in PERIODS:
        raise ValueError(f"period must be one of: {', '.join(PERIODS)}")

    start, end = _range(hours, period)
    q = {"period": period, "start": start, "end": end}

    if node_id:
        q["node_id"] = int(node_id)

    return count_rows(panels.call(c, "GET", f"/api/node/user_counts/{metric}?{httpx.QueryParams(q)}"), metric)


# ------------------------------------------------------------------ writes
def bulk(c: dict, action: str, ids, confirm: bool = False) -> dict:
    """One of the panel's bulk routes for the chosen nodes. Everything except `enable` needs `confirm`; a delete saves every node to a file first (key and certificate included, in a folder
    only this server can read). A node that does not exist makes the whole call fail before anything is done."""
    if action not in BULK:
        raise ValueError(f"the action is one of: {', '.join(BULK)}")

    ids = check_ids(ids)

    if action in NEEDS_OK and not confirm:
        raise ValueError({"delete": "deleting nodes cannot be undone on the panel (their traffic history goes): send confirm: true", "disable": "disabling nodes cuts whoever is connected through them: send confirm: true",
                          "reconnect": "restarting nodes drops their users for a moment: send confirm: true", "update": "updating nodes restarts them: send confirm: true",
                          "reset": "resetting the counters writes a log row and sets them to zero: send confirm: true"}[action])

    nodes = [_node_raw(c, i) for i in ids]
    saved = [backup_node(c, raw, "delete") for raw in nodes] if action == "delete" else []
    r = panels.call(c, "POST", f"/api/nodes/bulk/{action}", body={"ids": ids}, timeout=120)
    names = [_text(n.get("name"), 80) for n in nodes]
    _note(c, f"{action.capitalize()} done for {len(ids)} node{'s' if len(ids) != 1 else ''} ({', '.join(names[:6])}{', …' if len(names) > 6 else ''}).", "warn" if action in ("delete", "disable") else "info")
    return {"action": action, "count": _int((r or {}).get("count")) if isinstance(r, dict) else len(ids), "nodes": names, "backups": saved}


def reconnect_all(c: dict, confirm: bool = False, core_id: int | None = None) -> dict:
    """Restart the core on every node of the panel (or of one core) and connect again: customers drop for a moment on all of them."""
    if not confirm:
        raise ValueError("restarting every node drops all their users for a moment: send confirm: true")

    path = "/api/nodes/reconnect" + (f"?core_id={int(core_id)}" if core_id else "")
    panels.call(c, "POST", path, body={}, timeout=180)
    _note(c, f"Every node{' of core #' + str(int(core_id)) if core_id else ''} restarted from the panel.", "warn")
    return {"restarted": True, "core_id": int(core_id) if core_id else None}


def clear_usage(c: dict, table: str, typed: str, start: str | None = None, end: str | None = None) -> dict:
    """Empty a usage table of the panel (the traffic history per node or per user and node) for a time range or entirely - for good. `typed` must be the name of the table."""
    if table not in USAGE_TABLES:
        raise ValueError(f"the table is one of: {', '.join(USAGE_TABLES)}")

    if str(typed or "").strip() != table:
        raise ValueError(f"type the name of the table ({table}) to confirm: this removes the traffic history for good")

    q: dict = {}

    for k, v in (("start", start), ("end", end)):
        if v:
            if not re.fullmatch(r"\d{4}-\d{2}-\d{2}(T\d{2}:\d{2}:\d{2}(Z|[+-]\d{2}:\d{2})?)?", str(v)):
                raise ValueError(f"{k} must look like 2026-10-14 or 2026-10-14T00:00:00Z")

            q[k] = v if "T" in str(v) else f"{v}T00:00:00Z"

    panels.call(c, "DELETE", f"/api/nodes/clear_usage_data/{table}" + (f"?{httpx.QueryParams(q)}" if q else ""), timeout=300)
    _note(c, f"The usage table {table} was emptied{' from ' + q['start'] if 'start' in q else ''}{' to ' + q['end'] if 'end' in q else ''}.", "warn")
    return {"cleared": table, "start": q.get("start"), "end": q.get("end")}

"""Data centres: the hosting providers our servers sit in, grouped, with how our servers and tunnels there are doing
and whether the provider's own website answers - foreign providers checked from the panel server (HTTPS), Iranian
providers from one of our Iran servers (TCP 443, because many Iranian sites do not answer visitors from abroad).
Cached, refreshed in the background.

The provider of a server comes from the geo lookup (`nodes.geo_refresh`: isp / org, website domain, ASN) and is kept
in `Server.extra["geo"]`. `group()` and `parse_reach()` are pure and tested."""
from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor

import httpx
from sqlalchemy import select

from ..db import session
from ..log import get_logger
from ..models import Server, Tunnel
from ..ssh import Node, SSHError, run_script

log = get_logger("dcs")
TTL = 600
_sites: dict[str, dict] = {}
_sites_at = 0
_refreshing = False
_lock = threading.Lock()


def _key(s: dict) -> str:
    """One provider = one autonomous system when known ("Hetzner Online AG" and "... GmbH" are the same network), else its domain, else its name."""
    if s.get("asn"):
        return "as" + str(s["asn"])

    return (s.get("domain") or s.get("provider") or "unknown").lower()


def group(servers: list[dict], tunnels: list[dict] | None = None) -> list[dict]:
    """Pure: servers [{name, role, online, country, provider, domain, asn, city}] -> one row per provider.
    tunnels [{name, state, iran, foreign}] add how many tunnels through that provider's servers are not up."""
    rows: dict[str, dict] = {}

    for s in servers:
        k = _key(s)
        r = rows.setdefault(k, {"key": k, "name": s.get("provider") or s.get("domain") or "unknown provider", "domain": s.get("domain") or "", "asn": str(s.get("asn") or ""), "country": "", "city": "", "roles": set(), "servers": [], "online": 0, "total": 0, "tunnels_total": 0, "tunnels_bad": 0})

        if not r["domain"] and s.get("domain"):
            r["domain"] = s["domain"]

        if not r["country"] and s.get("country"):
            r["country"], r["city"] = s["country"], s.get("city") or ""

        r["roles"].add(s.get("role") or "")
        r["servers"].append({"name": s["name"], "online": s.get("online"), "role": s.get("role") or ""})
        r["total"] += 1
        r["online"] += 1 if s.get("online") else 0

    by_server = {s["name"]: _key(s) for s in servers}

    for t in tunnels or []:
        for side in ("iran", "foreign"):
            k = by_server.get(t.get(side) or "")

            if k in rows:
                rows[k]["tunnels_total"] += 1
                rows[k]["tunnels_bad"] += 0 if t.get("state") == "up" else 1

    out = []

    for r in rows.values():
        roles = r.pop("roles")
        r["role"] = "iran" if roles == {"iran"} else "foreign" if roles == {"foreign"} else "mixed"
        r["servers"].sort(key=lambda x: x["name"])
        out.append(r)

    out.sort(key=lambda r: (0 if r["role"] == "iran" else 1 if r["role"] == "mixed" else 2, -r["total"], r["name"].lower()))
    return out


def check_site(domain: str) -> dict:
    """Does the provider's website answer from here? Any HTTP answer below 500 counts (a login page or a 403 is still a live site)."""
    t0 = time.time()

    for url in (f"https://{domain}", f"https://www.{domain}", f"http://{domain}"):
        try:
            r = httpx.get(url, timeout=7, follow_redirects=True, headers={"User-Agent": "nikatel-panel (site check)"})
            return {"ok": r.status_code < 500, "code": r.status_code, "ms": int((time.time() - t0) * 1000), "at": int(time.time()), "via": ""}
        except httpx.HTTPError:
            continue

    return {"ok": False, "code": 0, "ms": int((time.time() - t0) * 1000), "at": int(time.time()), "via": ""}


def parse_reach(out: str) -> dict[str, dict]:
    """Pure: the lines of the tcp_reach script ("REACH host:port 1 ms" / "REACH host:port 0 0") -> {host: {ok, ms}}."""
    res = {}

    for ln in out.splitlines():
        p = ln.split()

        if len(p) == 4 and p[0] == "REACH" and ":" in p[1] and p[3].isdigit():
            res[p[1].rsplit(":", 1)[0]] = {"ok": p[2] == "1", "ms": int(p[3])}

    return res


def check_from_iran(domains: list[str]) -> dict[str, dict]:
    """TCP 443 to each site from one of our Iran servers; the panel server's own HTTPS check when none is online."""
    now = int(time.time())

    with session() as db:
        srv = db.scalars(select(Server).where(Server.role == "iran", Server.enabled.is_(True), Server.online.is_(True)).order_by(Server.name)).first()
        who = (srv.name, Node(srv.host, srv.ssh_user, srv.ssh_port)) if srv else None

    if not who:
        return {d: check_site(d) for d in domains}

    try:
        out = run_script(who[1], "tcp_reach", {"TARGETS": " ".join(f"{d}:443" for d in domains[:40])}, timeout=30)
    except (SSHError, ValueError) as e:
        log.warning("site check from %s failed: %s", who[0], e)
        return {d: check_site(d) for d in domains}

    got = parse_reach(out)
    return {d: {"ok": got[d]["ok"], "ms": got[d]["ms"], "code": 0, "at": now, "via": who[0]} if d in got else check_site(d) for d in domains}


def _refresh_sites(items: list[tuple[str, str]]) -> None:
    global _sites_at, _refreshing

    try:
        abroad = sorted({d for d, role in items if d and role != "iran"})
        iran = sorted({d for d, role in items if d and role == "iran"})

        with ThreadPoolExecutor(max_workers=min(8, max(1, len(abroad)))) as ex:
            for dom, res in zip(abroad, ex.map(check_site, abroad)):
                _sites[dom] = res

        if iran:
            _sites.update(check_from_iran(iran))
    except Exception:
        log.warning("site check failed", exc_info=True)
    finally:
        _sites_at = int(time.time())
        _refreshing = False


def sites(items: list[tuple[str, str]]) -> dict[str, dict]:
    """Cached site states for (domain, role) pairs; the first call waits, later calls answer at once and refresh in the background after TTL."""
    global _refreshing
    items = [(d, r) for d, r in items if d]
    missing = [d for d, _ in items if d not in _sites]
    stale = time.time() - _sites_at > TTL

    with _lock:
        if missing and not _sites:
            _refreshing = True
            _refresh_sites(items)
        elif (missing or stale) and not _refreshing:
            _refreshing = True
            threading.Thread(target=_refresh_sites, args=(items,), daemon=True, name="dc-sites").start()

    return {d: _sites.get(d, {"ok": None}) for d, _ in items}


def fetch() -> dict:
    with session() as db:
        servers = [{"name": s.name, "role": s.role, "online": s.online, "country": s.country or ("IR" if s.role == "iran" else ""), "city": ((s.extra or {}).get("geo") or {}).get("city", ""),
                    "provider": ((s.extra or {}).get("geo") or {}).get("provider", ""), "domain": ((s.extra or {}).get("geo") or {}).get("domain", ""), "asn": ((s.extra or {}).get("geo") or {}).get("asn", "")}
                   for s in db.scalars(select(Server).where(Server.enabled.is_(True)))]
        tunnels = [{"name": t.name, "state": t.state, "iran": t.iran.name, "foreign": t.foreign.name} for t in db.scalars(select(Tunnel).where(Tunnel.enabled.is_(True)))]

    rows = group(servers, tunnels)
    st = sites([(r["domain"], r["role"]) for r in rows])

    for r in rows:
        r["site"] = st.get(r["domain"]) if r["domain"] else None

    unknown = sum(1 for s in servers if not s["provider"] and not s["domain"])
    return {"rows": rows, "unknown": unknown, "at": _sites_at, "stale": time.time() - _sites_at > TTL * 2}

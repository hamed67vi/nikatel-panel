"""ArvanCloud CDN: the DNS records that point at our Iran servers.

Used by the failover (`failover.py`): when an Iran server dies and its tunnels move to another one, the A records
that pointed at the dead server's address are pointed at the new one. Only the address inside a record is
changed - every other field of the record (name, ttl, cloud, ports, weights …) is sent back exactly as it was read.

API: https://napi.arvancloud.ir/cdn/4.0  ·  header `Authorization: Apikey <key>`.
The key lives in the panel's settings (never in the repository, never shown again); give it access to the CDN
service only. `swap_ip` is pure and tested; everything else goes through `_req`."""
from __future__ import annotations

import copy
import ipaddress

import httpx

from ..log import get_logger
from . import settings as st

log = get_logger("arvan")
BASE = "https://napi.arvancloud.ir/cdn/4.0"
# the fields a record update takes back (the read side has more: id, created_at, …)
KEEP = ("type", "name", "value", "ttl", "cloud", "upstream_https", "ip_filter_mode", "cname", "text", "mx", "ns", "txt")


def key() -> str:
    k = str(st.get("arvan").get("api_key") or "").strip()
    return k.removeprefix("Apikey ").removeprefix("apikey ").strip()


def _req(method: str, path: str, body: dict | None = None, params: dict | None = None) -> dict:
    k = key()

    if not k:
        raise RuntimeError("no ArvanCloud API key saved (Settings → Repairs & AI → ArvanCloud)")

    try:
        r = httpx.request(method, BASE + path, params=params, json=body, timeout=20, headers={"Authorization": "Apikey " + k, "Accept": "application/json"})
    except httpx.HTTPError as e:
        raise RuntimeError("ArvanCloud does not answer: " + str(e)[:120])

    try:
        j = r.json()
    except ValueError:
        j = {}

    if r.status_code in (401, 403):
        raise RuntimeError("ArvanCloud refused the key (" + str(r.status_code) + "): check that the key is valid and has access to the CDN service")

    if r.status_code >= 400:
        msg = (j.get("message") if isinstance(j, dict) else "") or r.text[:120]
        raise RuntimeError(f"ArvanCloud answered {r.status_code}: {msg}")

    return j if isinstance(j, dict) else {}


def _pages(path: str) -> list[dict]:
    out = []

    for page in range(1, 30):
        j = _req("GET", path, params={"page": page, "per_page": 100})
        rows = j.get("data") or []
        out.extend(x for x in rows if isinstance(x, dict))
        last = ((j.get("meta") or {}).get("last_page")) or 1

        if page >= int(last) or not rows:
            break

    return out


def domains() -> list[str]:
    return sorted({str(d.get("domain") or d.get("name") or "") for d in _pages("/domains")} - {""})


def records(domain: str) -> list[dict]:
    return _pages(f"/domains/{domain}/dns-records")


def _ips(rec: dict) -> list[str]:
    v = rec.get("value")
    items = v if isinstance(v, list) else ([v] if v else [])
    return [str(i.get("ip") or "") for i in items if isinstance(i, dict) and i.get("ip")]


def swap_ip(rec: dict, old: str, new: str) -> dict | None:
    """Pure: the body of the update that points `rec` at `new` where it pointed at `old`; None when it does not point at `old`
    (or is not an A record). Everything but the address is kept as it was read."""
    if str(rec.get("type", "")).lower() != "a" or old not in _ips(rec):
        return None

    ipaddress.ip_address(new)   # ValueError for something that is not an address
    body = {k: copy.deepcopy(v) for k, v in rec.items() if k in KEEP and v is not None}

    for item in body.get("value", []):
        if isinstance(item, dict) and item.get("ip") == old:
            item["ip"] = new

    return body


def find_by_ip(ip: str, only: list[str] | None = None) -> list[dict]:
    """Every A record (in all our domains, or only those named) that points at `ip`."""
    out = []

    for d in (only or domains()):
        for r in records(d):
            if str(r.get("type", "")).lower() == "a" and ip in _ips(r):
                name = str(r.get("name") or "")
                out.append({"domain": d, "id": r.get("id"), "name": name, "fqdn": d if name in ("@", "") else f"{name}.{d}", "ip": ip, "ttl": r.get("ttl"), "cloud": bool(r.get("cloud"))})

    return out


def apply(domain: str, record_id: str, old: str, new: str) -> dict:
    """Point one record at `new`: read it again, change only the address, write it back, read it back to be sure."""
    cur = _req("GET", f"/domains/{domain}/dns-records/{record_id}").get("data") or {}
    body = swap_ip(cur, old, new)

    if body is None:
        raise RuntimeError(f"{cur.get('name', record_id)}.{domain} does not point at {old} any more - left alone")

    _req("PUT", f"/domains/{domain}/dns-records/{record_id}", body=body)
    after = _req("GET", f"/domains/{domain}/dns-records/{record_id}").get("data") or {}

    if new not in _ips(after):
        raise RuntimeError(f"ArvanCloud did not keep the new address for {cur.get('name', record_id)}.{domain}")

    log.info("dns %s.%s %s -> %s", cur.get("name"), domain, old, new)
    return {"domain": domain, "id": record_id, "name": cur.get("name"), "old": old, "new": new}


def test() -> dict:
    """Does the key work, and how much does it see. Read-only."""
    ds = domains()
    n = sum(len(records(d)) for d in ds[:20])
    res = {"ok": True, "domains": ds, "records": n}
    st.put("arvan", {"status": {"ok": True, "domains": len(ds), "records": n}})
    return res

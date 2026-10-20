"""ArvanCloud CDN: the DNS records that point at our Iran servers.

Used by the failover (`failover.py`): when an Iran server dies and its tunnels move to another one, the A records
that pointed at the dead server's address are pointed at the new one. Only the address inside a record is
changed - every other field of the record (name, ttl, cloud, ports, weights …) is sent back exactly as it was read.

API: https://napi.arvancloud.ir/cdn/4.0  ·  header `Authorization: Apikey <key>`.
The key lives in the panel's settings (never in the repository, never shown again); give it access to the CDN
service only. `swap_ip` is pure and tested; everything else goes through `_req`."""
from __future__ import annotations

import contextlib
import contextvars
import copy
import ipaddress
import re

import httpx

from ..log import get_logger
from . import settings as st

log = get_logger("arvan")
BASE = "https://napi.arvancloud.ir/cdn/4.0"
# the fields a record update takes back (the read side has more: id, created_at, …)
KEEP = ("type", "name", "value", "ttl", "cloud", "upstream_https", "ip_filter_mode", "cname", "text", "mx", "ns", "txt")


_KEY: contextvars.ContextVar[str | None] = contextvars.ContextVar("arvan_key", default=None)


@contextlib.contextmanager
def using(api_key: str):
    """Inside the block every call of this module uses THIS key (another ArvanCloud account), not the one in the settings."""
    tok = _KEY.set(str(api_key or ""))

    try:
        yield
    finally:
        _KEY.reset(tok)


def key() -> str:
    k = str(_KEY.get() or st.get("arvan").get("api_key") or "").strip()
    return k.removeprefix("Apikey ").removeprefix("apikey ").strip()


def _req(method: str, path: str, body: dict | None = None, params: dict | None = None) -> dict:
    k = key()

    if not k:
        raise RuntimeError("no ArvanCloud API key saved (Settings → Repairs & AI → ArvanCloud)")

    if not k.isascii() or not k.isprintable():      # a key goes into an HTTP header: a hidden or Persian character from the copy would raise UnicodeEncodeError (a 500 on Save)
        raise RuntimeError("the ArvanCloud key has a character that is not a letter, a digit or a sign (a hidden or non-English character from the copy?): copy it again from ArvanCloud")

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


TTLS = (120, 180, 300, 600, 900, 1800, 3600, 7200, 18000, 43200, 86400, 172800, 432000)      # the values ArvanCloud accepts
NAME_RE = re.compile(r"^(@|\*|[a-z0-9_*]([a-z0-9_.*-]{0,120}[a-z0-9_*])?)$")


UPSTREAM = ("default", "auto", "http", "https")      # how ArvanCloud talks to the origin server when the cloud is on


def a_body(name: str, ips: list[str], ttl: int = 120, cloud: bool = False, base: dict | None = None, port: int | None = None, upstream: str | None = None) -> dict:
    """Pure: the body of an A record (create, or update of `base` = the record as it was read, so every other field stays as it was). ValueError for a wrong name, address or ttl."""
    n = str(name or "").strip().lower()

    if not NAME_RE.match(n):
        raise ValueError("the name may only have letters, digits, dots, dashes and underscores (or @ for the domain itself, * for any)")

    clean = []

    for ip in ips:
        try:
            clean.append(str(ipaddress.IPv4Address(str(ip).strip())))
        except ValueError:
            raise ValueError(f"{str(ip).strip()[:40]!r} is not an IPv4 address")

    if not clean or len(clean) > 10 or len(set(clean)) != len(clean):
        raise ValueError("give one to ten different IPv4 addresses")

    if int(ttl) not in TTLS:
        raise ValueError("the TTL must be one of " + ", ".join(str(t) for t in TTLS))

    body = {k: copy.deepcopy(v) for k, v in (base or {}).items() if k in KEEP and v is not None}
    old_items = [i for i in (body.get("value") or []) if isinstance(i, dict)]
    body.update({"type": "a", "name": n, "ttl": int(ttl), "cloud": bool(cloud)})
    empty = {"port": None, "weight": None, "country": ""}

    def item_for(i: int, ip: str) -> dict:
        """The record keeps its port, weight and country when only the ADDRESS changes: the same address keeps its item, a new address takes the item of the same place (or the first one)."""
        same = next((o for o in old_items if o.get("ip") == ip), None)

        if same is not None:
            return same

        if len(old_items) == len(clean):
            return old_items[i]

        return old_items[0] if old_items else empty

    body["value"] = [{**item_for(i, ip), "ip": ip} for i, ip in enumerate(clean)]

    if port is not None:      # None = keep what the record has; 0 = no port; 1..65535 = this port on every address
        if not 0 <= int(port) <= 65535:
            raise ValueError("the port must be between 1 and 65535 (empty = none)")

        for item in body["value"]:
            item["port"] = int(port) or None

    if upstream is not None:
        if upstream not in UPSTREAM:
            raise ValueError("the protocol to the origin must be one of " + ", ".join(UPSTREAM))

        body["upstream_https"] = upstream

    body.setdefault("upstream_https", "default")
    body.setdefault("ip_filter_mode", {"count": "single" if len(clean) == 1 else "multi", "order": "none", "geo_filter": "none"})
    return body


HOST_RE = re.compile(r"^(?=.{1,253}$)([A-Za-z0-9_]([A-Za-z0-9_-]{0,61}[A-Za-z0-9_])?\.)+[A-Za-z]{2,63}\.?$")
EXTRA_TYPES = ("aaaa", "cname", "txt")      # the other kinds of record the panel can make and delete (A is the one it also edits)


def other_body(rtype: str, name: str, ips: list[str], value: str, ttl: int = 120, cloud: bool = False) -> dict:
    """Pure: the body of an AAAA, CNAME or TXT record. ValueError for a wrong name, address, host name, text or ttl."""
    t = str(rtype or "").lower()
    n = str(name or "").strip().lower()

    if t not in EXTRA_TYPES:
        raise ValueError("the record type must be A, AAAA, CNAME or TXT")

    if not NAME_RE.match(n):
        raise ValueError("the name may only have letters, digits, dots, dashes and underscores (or @ for the domain itself, * for any)")

    if int(ttl) not in TTLS:
        raise ValueError("the TTL must be one of " + ", ".join(str(x) for x in TTLS))

    body: dict = {"type": t, "name": n, "ttl": int(ttl)}

    if t == "aaaa":
        clean = []

        for ip in ips:
            try:
                clean.append(str(ipaddress.IPv6Address(str(ip).strip())))
            except ValueError:
                raise ValueError(f"{str(ip).strip()[:40]!r} is not an IPv6 address")

        if not clean or len(clean) > 10 or len(set(clean)) != len(clean):
            raise ValueError("give one to ten different IPv6 addresses")

        body.update({"cloud": bool(cloud), "value": [{"ip": ip, "port": None, "weight": None, "country": ""} for ip in clean], "upstream_https": "default",
                     "ip_filter_mode": {"count": "single" if len(clean) == 1 else "multi", "order": "none", "geo_filter": "none"}})
    elif t == "cname":
        host = str(value or "").strip()

        if not HOST_RE.match(host):
            raise ValueError("a CNAME points at a host name such as target.example.com")

        body.update({"cloud": bool(cloud), "value": {"host": host.rstrip("."), "host_header": "source"}, "upstream_https": "default"})
    else:
        text = str(value or "").strip()

        if not text or len(text) > 1000:
            raise ValueError("the text of a TXT record is empty or longer than 1000 characters")

        body.update({"value": {"text": text}})

    return body

def _check_domain(domain: str) -> str:
    d = str(domain or "").strip().lower()

    if d not in domains():
        raise ValueError("this domain is not in the ArvanCloud account")

    return d


def get_record(domain: str, record_id: str) -> dict:
    return _req("GET", f"/domains/{domain}/dns-records/{record_id}").get("data") or {}


def create_a(domain: str, name: str, ips: list[str], ttl: int = 120, cloud: bool = False, port: int | None = None, upstream: str | None = None) -> dict:
    """Make an A record; read it back to be sure. Returns {domain, id, record}."""
    d = _check_domain(domain)
    body = a_body(name, ips, ttl, cloud, port=port, upstream=upstream)
    made = _req("POST", f"/domains/{d}/dns-records", body=body).get("data") or {}
    rid = str(made.get("id") or "")

    if not rid:
        raise RuntimeError("ArvanCloud did not say which record it made")

    after = get_record(d, rid)

    if sorted(_ips(after)) != sorted(i["ip"] for i in body["value"]):
        raise RuntimeError(f"ArvanCloud did not keep the address of {name}.{d}")

    log.info("dns create %s.%s %s", body["name"], d, ",".join(_ips(after)))
    return {"domain": d, "id": rid, "record": after}


def update_a(domain: str, record_id: str, name: str, ips: list[str], ttl: int, cloud: bool, port: int | None = None, upstream: str | None = None) -> dict:
    """Change an A record (name, addresses, ttl, cloud); everything else stays as it was read; read back. Returns {domain, id, before, after}."""
    d = _check_domain(domain)
    before = get_record(d, record_id)

    if str(before.get("type", "")).lower() != "a":
        raise RuntimeError("only A records are edited here")

    _req("PUT", f"/domains/{d}/dns-records/{record_id}", body=a_body(name, ips, ttl, cloud, base=before, port=port, upstream=upstream))
    after = get_record(d, record_id)

    if sorted(_ips(after)) != sorted(str(ipaddress.IPv4Address(str(i).strip())) for i in ips):
        raise RuntimeError(f"ArvanCloud did not keep the new address of {name}.{d}")

    log.info("dns edit %s.%s %s -> %s", before.get("name"), d, ",".join(_ips(before)), ",".join(_ips(after)))
    return {"domain": d, "id": str(record_id), "before": before, "after": after}


def create_other(domain: str, rtype: str, name: str, ips: list[str], value: str, ttl: int = 120, cloud: bool = False) -> dict:
    """Make an AAAA, CNAME or TXT record; read it back to be sure. Returns {domain, id, record}."""
    d = _check_domain(domain)
    body = other_body(rtype, name, ips, value, ttl, cloud)
    made = _req("POST", f"/domains/{d}/dns-records", body=body).get("data") or {}
    rid = str(made.get("id") or "")

    if not rid:
        raise RuntimeError("ArvanCloud did not say which record it made")

    log.info("dns create %s %s.%s", body["type"], body["name"], d)
    return {"domain": d, "id": rid, "record": get_record(d, rid)}


def put_back(domain: str, record_id: str, body: dict) -> dict:
    """Restore: write a record as it was read before (an edit that is undone)."""
    d = _check_domain(domain)
    keep = {k: copy.deepcopy(v) for k, v in body.items() if k in KEEP and v is not None}
    _req("PUT", f"/domains/{d}/dns-records/{record_id}", body=keep)
    return get_record(d, record_id)


def create_from(domain: str, body: dict) -> dict:
    """Restore: make a record again from what was read before it was deleted."""
    d = _check_domain(domain)
    keep = {k: copy.deepcopy(v) for k, v in body.items() if k in KEEP and v is not None}
    made = _req("POST", f"/domains/{d}/dns-records", body=keep).get("data") or {}
    rid = str(made.get("id") or "")

    if not rid:
        raise RuntimeError("ArvanCloud did not say which record it made")

    return {"domain": d, "id": rid, "record": get_record(d, rid)}


def delete_record(domain: str, record_id: str) -> dict:
    """Delete an A record (what it was is returned, so it can be made again); the record must be gone afterwards."""
    d = _check_domain(domain)
    before = get_record(d, record_id)

    if str(before.get("type", "")).lower() not in ("a",) + EXTRA_TYPES:
        raise RuntimeError("only A, AAAA, CNAME and TXT records are deleted here")

    _req("DELETE", f"/domains/{d}/dns-records/{record_id}")

    try:
        get_record(d, record_id)
    except RuntimeError as e:
        if "404" in str(e):
            log.info("dns delete %s.%s", before.get("name"), d)
            return {"domain": d, "id": str(record_id), "before": before}

        raise

    raise RuntimeError(f"ArvanCloud still has {before.get(chr(110)+chr(97)+chr(109)+chr(101))}.{d}")


def test() -> dict:
    """Does the key work, and how much does it see. Read-only."""
    ds = domains()
    n = sum(len(records(d)) for d in ds[:20])
    res = {"ok": True, "domains": ds, "records": n}
    st.put("arvan", {"status": {"ok": True, "domains": len(ds), "records": n}})
    return res

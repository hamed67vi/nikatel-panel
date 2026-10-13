"""DNS accounts: ArvanCloud (several accounts) and Cloudflare under one list of records and one way to make, change and delete an A record.

  accounts()            the accounts: the main ArvanCloud key of the settings (id "arvan"; the failover uses it) and the ones added on the DNS page
  provider(account_id)  an object with the same methods for both kinds (below); a record is `{id, type, name, fqdn, domain, value, ips, port, ttl, cloud, editable}`
  add / update / remove an account; every key stays in the settings and never comes back through the API

The records of other types (MX, NS, TXT, CNAME ...) are listed (read only); A records can be made, changed (name, addresses, TTL, cloud) and deleted. What a
change replaces is returned as `before` (the provider's own body) so the DNS log can put it back."""
from __future__ import annotations

import copy
import ipaddress
import secrets
from dataclasses import dataclass

from ..log import get_logger
from . import arvan, cloudflare
from . import settings as st

log = get_logger("dns")
PROVIDERS = {"arvan": "ArvanCloud", "cloudflare": "Cloudflare"}
ARVAN_TTLS = ((120, "2 minutes"), (180, "3 minutes"), (300, "5 minutes"), (600, "10 minutes"), (900, "15 minutes"), (1800, "30 minutes"), (3600, "1 hour"), (7200, "2 hours"), (18000, "5 hours"), (43200, "12 hours"), (86400, "1 day"))
CF_TTLS = ((1, "Auto"), (60, "1 minute"), (120, "2 minutes"), (300, "5 minutes"), (600, "10 minutes"), (900, "15 minutes"), (1800, "30 minutes"), (3600, "1 hour"), (7200, "2 hours"), (18000, "5 hours"), (43200, "12 hours"), (86400, "1 day"))
NAME_RE = arvan.NAME_RE


@dataclass
class Account:
    id: str
    provider: str
    name: str
    key: str


def _stored() -> list[dict]:
    return [a for a in (st.get("dns").get("accounts") or []) if isinstance(a, dict) and a.get("id")]


def accounts() -> list[Account]:
    out = []
    main = str(st.get("arvan").get("api_key") or "").strip()

    if main:
        out.append(Account("arvan", "arvan", "ArvanCloud", main))

    for a in _stored():
        if a.get("provider") in PROVIDERS and a.get("key"):
            out.append(Account(str(a["id"]), str(a["provider"]), str(a.get("name") or PROVIDERS[a["provider"]]), str(a["key"])))

    return out


def get(account_id: str) -> Account:
    a = next((x for x in accounts() if x.id == account_id), None)

    if a is None:
        raise ValueError("this DNS account does not exist")

    return a


def public(a: Account) -> dict:
    status = (st.get("arvan").get("status") if a.id == "arvan" else next((x.get("status") for x in _stored() if x["id"] == a.id), None)) or {}
    return {"id": a.id, "provider": a.provider, "provider_name": PROVIDERS[a.provider], "name": a.name, "key_tail": a.key[-4:] if len(a.key) >= 12 else "", "status": status, "main": a.id == "arvan"}


def _set_status(account_id: str, status: dict) -> None:
    if account_id == "arvan":
        st.put("arvan", {"status": status})
        return

    lst = _stored()

    for a in lst:
        if a["id"] == account_id:
            a["status"] = status

    st.put("dns", {"accounts": lst})


def add(provider: str, name: str, key: str) -> Account:
    """Save a new account after the key has been tried (the domains are listed - read only)."""
    if provider not in PROVIDERS:
        raise ValueError("the provider must be arvan or cloudflare")

    k = str(key or "").strip()
    n = str(name or "").strip()[:40] or PROVIDERS[provider]

    if len(k) < 12:
        raise ValueError("paste the whole API key (token)")

    acc = Account(f"{provider[:2]}-{secrets.token_hex(3)}", provider, n, k)
    doms = provider_of(acc).domains()                       # RuntimeError here = the key does not work: nothing is saved
    st.put("dns", {"accounts": _stored() + [{"id": acc.id, "provider": provider, "name": n, "key": k, "status": {"ok": True, "domains": len(doms)}}]})
    return acc


def update(account_id: str, name: str | None = None, key: str | None = None) -> Account:
    a = get(account_id)

    if account_id == "arvan":
        if key:
            st.put("arvan", {"api_key": key.strip(), "status": {}})

        return get("arvan")

    lst = _stored()

    for x in lst:
        if x["id"] == account_id:
            if name is not None and name.strip():
                x["name"] = name.strip()[:40]

            if key and key.strip():
                probe = Account(a.id, a.provider, a.name, key.strip())
                provider_of(probe).domains()                # the new key is tried first
                x["key"], x["status"] = key.strip(), {}

    st.put("dns", {"accounts": lst})
    return get(account_id)


def remove(account_id: str) -> None:
    get(account_id)

    if account_id == "arvan":
        st.put("arvan", {"api_key": None, "status": {}, "mode": "off"})         # None, not "": an empty string keeps the old secret
        return

    st.put("dns", {"accounts": [a for a in _stored() if a["id"] != account_id]})


def ips_of(raw_value) -> list[str]:
    items = raw_value if isinstance(raw_value, list) else ([raw_value] if raw_value else [])
    return [str(i.get("ip")) for i in items if isinstance(i, dict) and i.get("ip")]


def _text(v) -> str:
    """A record value of another type as one readable string."""
    if isinstance(v, list):
        return " · ".join(_text(i) for i in v if i)

    if isinstance(v, dict):
        host = v.get("host") or v.get("text") or v.get("target") or v.get("domain") or ""
        pri = v.get("priority")
        return (f"{host}" + (f" (priority {pri})" if pri not in (None, "") else "")) if host else " ".join(f"{k}={x}" for k, x in v.items() if x not in (None, ""))[:160]

    return str(v or "")


def _check_ips(ips: list[str], one: bool) -> list[str]:
    clean = []

    for ip in ips:
        try:
            clean.append(str(ipaddress.IPv4Address(str(ip).strip())))
        except ValueError:
            raise ValueError(f"{str(ip).strip()[:40]!r} is not an IPv4 address")

    if not clean or len(clean) > 10 or len(set(clean)) != len(clean):
        raise ValueError("give one to ten different IPv4 addresses")

    if one and len(clean) != 1:
        raise ValueError("a Cloudflare record holds ONE address: make one record per address")

    return clean


def _check_name(name: str) -> str:
    n = str(name or "").strip().lower()

    if not NAME_RE.match(n):
        raise ValueError("the name may only have letters, digits, dots, dashes and underscores (or @ for the domain itself)")

    return n


class ArvanProvider:
    kind = "arvan"
    ttls = ARVAN_TTLS

    def __init__(self, acc: Account):
        self.acc = acc

    def domains(self) -> list[str]:
        with arvan.using(self.acc.key):
            return arvan.domains()

    def _row(self, domain: str, r: dict) -> dict:
        t = str(r.get("type") or "").upper()
        name = str(r.get("name") or "@")
        items = [i for i in (r.get("value") if isinstance(r.get("value"), list) else [r.get("value")]) if isinstance(i, dict)]
        ips = ips_of(r.get("value")) if t in ("A", "AAAA") else []
        return {"account": self.acc.id, "domain": domain, "id": str(r.get("id") or ""), "type": t, "name": name, "fqdn": domain if name in ("@", "") else f"{name}.{domain}", "ips": ips, "port": next((i.get("port") for i in items if i.get("port")), None), "upstream": r.get("upstream_https") if t == "A" else None,
                "value": ", ".join(ips) if ips else _text(r.get("value") or r.get("cname") or r.get("text") or r.get("mx") or r.get("ns") or ""), "ttl": r.get("ttl"), "cloud": bool(r.get("cloud")) if t in ("A", "AAAA", "CNAME") else None, "editable": t == "A", "deletable": t in ("A", "AAAA", "CNAME", "TXT")}

    def records(self, domain: str) -> list[dict]:
        with arvan.using(self.acc.key):
            return [self._row(domain, r) for r in arvan.records(domain)]

    def create(self, domain: str, name: str, ips: list[str], ttl: int, cloud: bool, port: int | None = None, upstream: str | None = None, rtype: str = "A", value: str = "") -> dict:
        with arvan.using(self.acc.key):
            if str(rtype).upper() != "A":
                made = arvan.create_other(domain, rtype, name, [str(i).strip() for i in ips], value, ttl, cloud)
            else:
                made = arvan.create_a(domain, name, _check_ips(ips, False), ttl, cloud, port, upstream)

        return self._row(made["domain"], made["record"])

    def update(self, domain: str, rid: str, name: str, ips: list[str], ttl: int, cloud: bool, port: int | None = None, upstream: str | None = None) -> dict:
        with arvan.using(self.acc.key):
            r = arvan.update_a(domain, rid, name, _check_ips(ips, False), ttl, cloud, port, upstream)

        return {"before": r["before"], "after": self._row(domain, r["after"]), "before_row": self._row(domain, r["before"])}

    def get(self, domain: str, rid: str) -> dict:      # one record read fresh (the list of a domain may leave out fields such as the origin protocol)
        with arvan.using(self.acc.key):
            return self._row(domain, arvan.get_record(domain, rid))

    def delete(self, domain: str, rid: str) -> dict:
        with arvan.using(self.acc.key):
            r = arvan.delete_record(domain, rid)

        return {"before": r["before"], "before_row": self._row(domain, r["before"])}

    def put_back(self, domain: str, rid: str, before: dict) -> None:
        with arvan.using(self.acc.key):
            arvan.put_back(domain, rid, before)

    def create_from(self, domain: str, before: dict) -> str:
        with arvan.using(self.acc.key):
            return arvan.create_from(domain, before)["id"]


class CloudflareProvider:
    kind = "cloudflare"
    ttls = CF_TTLS

    def __init__(self, acc: Account):
        self.acc = acc
        self._z: dict[str, str] | None = None

    def _zones(self) -> dict[str, str]:
        if self._z is None:
            self._z = {z["name"]: z["id"] for z in cloudflare.zones(self.acc.key)}

        return self._z

    def _zid(self, domain: str) -> str:
        d = str(domain or "").strip().lower()
        z = self._zones().get(d)

        if not z:
            raise ValueError("this domain is not in the Cloudflare account")

        return z

    def domains(self) -> list[str]:
        return sorted(self._zones())

    def _row(self, domain: str, r: dict) -> dict:
        t = str(r.get("type") or "").upper()
        fq = str(r.get("name") or "")
        name = "@" if fq == domain else (fq[: -len(domain) - 1] if fq.endswith("." + domain) else fq)
        ips = [str(r.get("content"))] if t == "A" and r.get("content") else []
        return {"account": self.acc.id, "domain": domain, "id": str(r.get("id") or ""), "type": t, "name": name, "fqdn": fq, "ips": ips, "port": None,
                "value": str(r.get("content") or ""), "ttl": r.get("ttl"), "cloud": bool(r.get("proxied")) if t in ("A", "AAAA", "CNAME") else None, "editable": t == "A", "deletable": t in ("A", "AAAA", "CNAME", "TXT")}

    def records(self, domain: str) -> list[dict]:
        return [self._row(domain, r) for r in cloudflare.records(self.acc.key, self._zid(domain))]

    @staticmethod
    def _body(domain: str, name: str, ips: list[str], ttl: int, cloud: bool) -> dict:
        n = _check_name(name)
        ip = _check_ips(ips, True)[0]

        if int(ttl) != 1 and not 60 <= int(ttl) <= 86400:
            raise ValueError("the TTL must be Auto or between 1 minute and 1 day")

        return {"type": "A", "name": domain if n == "@" else f"{n}.{domain}", "content": ip, "ttl": int(ttl), "proxied": bool(cloud)}

    @staticmethod
    def _other_body(domain: str, rtype: str, name: str, ips: list[str], value: str, ttl: int, cloud: bool) -> dict:
        """AAAA, CNAME and TXT: one value each (a Cloudflare AAAA record holds ONE address)."""
        t = str(rtype).upper()
        n = _check_name(name)

        if int(ttl) != 1 and not 60 <= int(ttl) <= 86400:
            raise ValueError("the TTL must be Auto or between 1 minute and 1 day")

        if t == "AAAA":
            if len(ips) != 1:
                raise ValueError("a Cloudflare record holds ONE address: make one record per address")

            try:
                content = str(ipaddress.IPv6Address(str(ips[0]).strip()))
            except ValueError:
                raise ValueError(f"{str(ips[0]).strip()[:40]!r} is not an IPv6 address")
        elif t == "CNAME":
            content = str(value or "").strip().rstrip(".")

            if not arvan.HOST_RE.match(content):
                raise ValueError("a CNAME points at a host name such as target.example.com")
        elif t == "TXT":
            content = str(value or "").strip()

            if not content or len(content) > 1000:
                raise ValueError("the text of a TXT record is empty or longer than 1000 characters")
        else:
            raise ValueError("the record type must be A, AAAA, CNAME or TXT")

        body = {"type": t, "name": domain if n == "@" else f"{n}.{domain}", "content": content, "ttl": int(ttl)}

        if t in ("AAAA", "CNAME"):
            body["proxied"] = bool(cloud)

        return body

    def create(self, domain: str, name: str, ips: list[str], ttl: int, cloud: bool, port: int | None = None, upstream: str | None = None, rtype: str = "A", value: str = "") -> dict:      # a Cloudflare record has no port or origin protocol here: both are ignored
        z = self._zid(domain)
        body = self._body(domain, name, ips, ttl, cloud) if str(rtype).upper() == "A" else self._other_body(domain, rtype, name, ips, value, ttl, cloud)
        made = (cloudflare.req(self.acc.key, "POST", f"/zones/{z}/dns_records", body=body).get("result")) or {}

        if not made.get("id"):
            raise RuntimeError("Cloudflare did not say which record it made")

        return self._row(domain, made)

    def _get(self, z: str, rid: str) -> dict:
        return cloudflare.req(self.acc.key, "GET", f"/zones/{z}/dns_records/{rid}").get("result") or {}

    def update(self, domain: str, rid: str, name: str, ips: list[str], ttl: int, cloud: bool, port: int | None = None, upstream: str | None = None) -> dict:
        z = self._zid(domain)
        before = self._get(z, rid)

        if str(before.get("type", "")).upper() != "A":
            raise RuntimeError("only A records are edited here")

        after = cloudflare.req(self.acc.key, "PUT", f"/zones/{z}/dns_records/{rid}", body=self._body(domain, name, ips, ttl, cloud)).get("result") or {}
        return {"before": before, "after": self._row(domain, after), "before_row": self._row(domain, before)}

    def delete(self, domain: str, rid: str) -> dict:
        z = self._zid(domain)
        before = self._get(z, rid)

        if str(before.get("type", "")).upper() not in ("A", "AAAA", "CNAME", "TXT"):
            raise RuntimeError("only A, AAAA, CNAME and TXT records are deleted here")

        cloudflare.req(self.acc.key, "DELETE", f"/zones/{z}/dns_records/{rid}")
        return {"before": before, "before_row": self._row(domain, before)}

    def put_back(self, domain: str, rid: str, before: dict) -> None:
        cloudflare.req(self.acc.key, "PUT", f"/zones/{self._zid(domain)}/dns_records/{rid}", body={k: copy.deepcopy(before.get(k)) for k in ("type", "name", "content", "ttl", "proxied") if before.get(k) is not None})

    def create_from(self, domain: str, before: dict) -> str:
        made = cloudflare.req(self.acc.key, "POST", f"/zones/{self._zid(domain)}/dns_records", body={k: copy.deepcopy(before.get(k)) for k in ("type", "name", "content", "ttl", "proxied") if before.get(k) is not None}).get("result") or {}
        return str(made.get("id") or "")


def provider_of(acc: Account):
    return ArvanProvider(acc) if acc.provider == "arvan" else CloudflareProvider(acc)


def provider(account_id: str):
    return provider_of(get(account_id))

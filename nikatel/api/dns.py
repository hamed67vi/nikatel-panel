import time

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from ..auth import current_user
from ..config import get_settings
from ..log import get_logger
from ..services import dnscache, dnsprov, failover, license

log = get_logger("dns")
router = APIRouter(tags=["dns"], dependencies=[Depends(current_user)])
_CACHE = dnscache.CACHE                 # the one copy of the records (services/dnscache.py): the same dict, so what reads it here and there sees the same
CACHE_SECONDS = dnscache.SECONDS


def _writable():
    if not license.has("failover"):
        raise HTTPException(402, "the DNS pages need a license (Settings → About → License)")

    if get_settings().read_only:
        raise HTTPException(403, "the panel is read-only")


class AccountIn(BaseModel):
    provider: str
    name: str = Field("", max_length=40)
    key: str = Field("", max_length=400)


class AccountEdit(BaseModel):
    name: str | None = None
    key: str | None = None


class RecordIn(BaseModel):
    account: str
    domain: str
    name: str = Field(min_length=1, max_length=120)
    ips: list[str] = Field(default_factory=list, max_length=10)      # the addresses of an A or AAAA record; a CNAME or TXT record has none
    ttl: int = 120
    cloud: bool = False
    type: str = Field("A", pattern="^(A|AAAA|CNAME|TXT)$")      # what kind of record to make (an existing record keeps its kind; only A records are edited)
    value: str | None = Field(None, max_length=1000)      # the host name of a CNAME, the text of a TXT
    port: int | None = Field(None, ge=0, le=65535)      # None = keep, 0 = none
    upstream: str | None = Field(None, pattern="^(default|auto|http|https)$")      # the protocol to the origin server (ArvanCloud)


@router.get("/dns/accounts")
def accounts_list():
    """The DNS accounts (ArvanCloud, Cloudflare) by name and provider; the keys never come back."""
    return {"ok": True, "accounts": [dnsprov.public(a) for a in dnsprov.accounts()], "providers": dnsprov.PROVIDERS}


@router.post("/dns/accounts", status_code=201)
def accounts_add(body: AccountIn):
    """Save a new DNS account after its key was tried (the domains are listed - read only)."""
    _writable()

    try:
        a = dnsprov.add(body.provider, body.name, body.key)
    except ValueError as e:
        raise HTTPException(400, str(e))
    except RuntimeError as e:
        raise HTTPException(502, str(e))

    dnscache.forget()
    return {"ok": True, "account": dnsprov.public(a)}


@router.put("/dns/accounts/{account_id}")
def accounts_edit(account_id: str, body: AccountEdit):
    """Rename an account or give it a new key (tried first)."""
    _writable()

    try:
        a = dnsprov.update(account_id, body.name, body.key)
    except ValueError as e:
        raise HTTPException(400, str(e))
    except RuntimeError as e:
        raise HTTPException(502, str(e))

    dnscache.forget()
    return {"ok": True, "account": dnsprov.public(a)}


@router.delete("/dns/accounts/{account_id}")
def accounts_remove(account_id: str):
    """Remove an account from the panel."""
    _writable()

    try:
        dnsprov.remove(account_id)
    except ValueError as e:
        raise HTTPException(400, str(e))

    dnscache.forget()
    return {"ok": True}


@router.post("/dns/accounts/{account_id}/test")
def accounts_test(account_id: str):
    """Read only: list the domains and count the records the key can see."""
    try:
        prov = dnsprov.provider(account_id)
        doms = prov.domains()
        n = sum(len(prov.records(d)) for d in doms[:30])
    except ValueError as e:
        raise HTTPException(400, str(e))
    except RuntimeError as e:
        dnsprov._set_status(account_id, {"ok": False, "error": str(e)})
        raise HTTPException(502, str(e))

    dnsprov._set_status(account_id, {"ok": True, "domains": len(doms), "records": n})
    return {"ok": True, "domains": doms, "records": n}


@router.get("/dns/records")
def records_list(fresh: bool = False):
    """The records of every account (all types; read only). The first read and `fresh` ask the accounts now (seconds); otherwise the page gets what was
    read last at once, and a read older than a minute is renewed in the background (`refreshing` says so; ask again in a few seconds)."""
    if fresh or not _CACHE["at"]:
        dnscache.read_all()
    elif time.time() - _CACHE["at"] > CACHE_SECONDS:
        dnscache.refresh_in_background()

    return {"ok": True, "records": _CACHE["rows"], "accounts": _CACHE["accounts"], "cached_at": int(_CACHE["at"]), "refreshing": bool(_CACHE["refreshing"])}


def _fq(domain: str, name: str) -> str:
    return domain if name in ("@", "") else f"{name}.{domain}"


@router.post("/dns/records", status_code=201)
def record_create(body: RecordIn):
    """Make an A record in one of the domains of an account (kept under Changes with a Restore)."""
    _writable()

    try:
        row = dnsprov.provider(body.account).create(body.domain, body.name, body.ips, body.ttl, body.cloud, body.port, body.upstream, body.type, body.value or "")
    except ValueError as e:
        raise HTTPException(400, str(e))
    except RuntimeError as e:
        raise HTTPException(502, str(e))

    dnscache.wrote(body.account, body.domain, row["id"], row)
    dnscache.stale()
    failover.log_manual("create", body.domain, row["fqdn"], row["id"], "", row["value"], None, body.account)
    return {"ok": True, "id": row["id"]}


@router.get("/dns/records/{account}/{domain}/{record_id}")
def record_one(account: str, domain: str, record_id: str):
    """One record read fresh from the provider (with the port and the protocol to the origin server), for the edit form."""
    try:
        prov = dnsprov.provider(account)
    except ValueError as e:                  # (an account that is not there: a sentence, not a 500)
        raise HTTPException(400, str(e))

    if not hasattr(prov, "get"):
        raise HTTPException(404, "this provider reads records only as a list")

    try:
        return {"ok": True, "record": prov.get(domain, record_id)}
    except ValueError as e:
        raise HTTPException(400, str(e))
    except RuntimeError as e:
        raise HTTPException(502, str(e))


@router.put("/dns/records/{account}/{domain}/{record_id}")
def record_edit(account: str, domain: str, record_id: str, body: RecordIn):
    """Change an A record by hand (name, addresses, TTL, cloud) - also what Point at a server does. Restore gives the old values back."""
    _writable()

    if body.type != "A":
        raise HTTPException(400, "only A records are edited here: delete the record and make it again with the new type")

    try:
        r = dnsprov.provider(account).update(domain, record_id, body.name, body.ips, body.ttl, body.cloud, body.port, body.upstream)
    except ValueError as e:
        raise HTTPException(400, str(e))
    except RuntimeError as e:
        raise HTTPException(502, str(e))

    dnscache.wrote(account, domain, record_id, r["after"])
    dnscache.stale()
    failover.log_manual("edit", domain, r["after"]["fqdn"], record_id, r["before_row"]["value"], r["after"]["value"], r["before"], account)
    return {"ok": True}


@router.delete("/dns/records/{account}/{domain}/{record_id}")
def record_delete(account: str, domain: str, record_id: str):
    """Delete a record (kept under Changes with a Restore that makes it again)."""
    _writable()

    try:
        r = dnsprov.provider(account).delete(domain, record_id)
    except ValueError as e:
        raise HTTPException(400, str(e))
    except RuntimeError as e:
        raise HTTPException(502, str(e))

    dnscache.wrote(account, domain, record_id, None)
    dnscache.stale()
    failover.log_manual("delete", domain, r["before_row"]["fqdn"], record_id, r["before_row"]["value"], "", r["before"], account)
    return {"ok": True}
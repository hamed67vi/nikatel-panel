import threading
import time

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from ..auth import current_user
from ..config import get_settings
from ..log import get_logger
from ..services import dnsprov, failover, license

log = get_logger("dns")
router = APIRouter(tags=["dns"], dependencies=[Depends(current_user)])
_CACHE: dict = {"at": 0.0, "rows": [], "accounts": [], "refreshing": False}
_REFRESH_LOCK = threading.Lock()
CACHE_SECONDS = 60


def _read_all() -> None:
    """Read the records of every account (one that fails does not hide the others) into the cache."""
    rows, accs = [], []

    for a in dnsprov.accounts():
        info = {"id": a.id, "name": a.name, "provider": a.provider, "ok": True, "error": "", "domains": [], "ttls": [[v, t] for v, t in dnsprov.provider_of(a).ttls]}
        n = 0

        try:
            prov = dnsprov.provider_of(a)

            for d in prov.domains():
                got = prov.records(d)
                n += len(got)
                info["domains"].append(d)
                rows.extend(got)
        except (RuntimeError, ValueError) as e:
            info["ok"], info["error"] = False, str(e)[:200]
        else:
            dnsprov._set_status(a.id, {"ok": True, "domains": len(info["domains"]), "records": n})

        accs.append(info)

    _CACHE.update(at=time.time(), rows=rows, accounts=accs)


def warm() -> None:
    """At start: read the accounts once in the background, so the first open of the DNS page is served from the cache too."""
    try:
        if dnsprov.accounts():
            _refresh_in_background()
    except Exception:
        log.exception("DNS warm-up skipped")


def _refresh_in_background() -> None:
    with _REFRESH_LOCK:
        if _CACHE["refreshing"]:
            return

        _CACHE["refreshing"] = True

    def work() -> None:
        try:
            _read_all()
        except Exception:
            log.exception("the DNS records could not be renewed in the background")
        finally:
            _CACHE["refreshing"] = False

    threading.Thread(target=work, name="dns-refresh", daemon=True).start()


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
    ips: list[str] = Field(min_length=1, max_length=10)
    ttl: int = 120
    cloud: bool = False


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

    _CACHE["at"] = 0.0
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

    _CACHE["at"] = 0.0
    return {"ok": True, "account": dnsprov.public(a)}


@router.delete("/dns/accounts/{account_id}")
def accounts_remove(account_id: str):
    """Remove an account from the panel."""
    _writable()

    try:
        dnsprov.remove(account_id)
    except ValueError as e:
        raise HTTPException(400, str(e))

    _CACHE["at"] = 0.0
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
        _read_all()
    elif time.time() - _CACHE["at"] > CACHE_SECONDS:
        _refresh_in_background()

    return {"ok": True, "records": _CACHE["rows"], "accounts": _CACHE["accounts"], "cached_at": int(_CACHE["at"]), "refreshing": bool(_CACHE["refreshing"])}


def _fq(domain: str, name: str) -> str:
    return domain if name in ("@", "") else f"{name}.{domain}"


@router.post("/dns/records", status_code=201)
def record_create(body: RecordIn):
    """Make an A record in one of the domains of an account (kept under Changes with a Restore)."""
    _writable()

    try:
        row = dnsprov.provider(body.account).create(body.domain, body.name, body.ips, body.ttl, body.cloud)
    except ValueError as e:
        raise HTTPException(400, str(e))
    except RuntimeError as e:
        raise HTTPException(502, str(e))

    _CACHE["at"] = 0.0
    failover.log_manual("create", body.domain, row["fqdn"], row["id"], "", row["value"], None, body.account)
    return {"ok": True, "id": row["id"]}


@router.put("/dns/records/{account}/{domain}/{record_id}")
def record_edit(account: str, domain: str, record_id: str, body: RecordIn):
    """Change an A record by hand (name, addresses, TTL, cloud) - also what Point at a server does. Restore gives the old values back."""
    _writable()

    try:
        r = dnsprov.provider(account).update(domain, record_id, body.name, body.ips, body.ttl, body.cloud)
    except ValueError as e:
        raise HTTPException(400, str(e))
    except RuntimeError as e:
        raise HTTPException(502, str(e))

    _CACHE["at"] = 0.0
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

    _CACHE["at"] = 0.0
    failover.log_manual("delete", domain, r["before_row"]["fqdn"], record_id, r["before_row"]["value"], "", r["before"], account)
    return {"ok": True}
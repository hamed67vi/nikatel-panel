"""The panel's copy of the DNS records of every account. Reading every domain of every provider takes many seconds, so the DNS page and the Outbound page share ONE copy: whoever
needs the records asks here, a write anywhere calls `forget()`. (It lived in api/dns.py until 1.46.0; a service must not import the web layer.)"""

import threading
import time

from ..log import get_logger
from . import dnsprov

log = get_logger("dnscache")
CACHE: dict = {"at": 0.0, "rows": [], "accounts": [], "refreshing": False}
SECONDS = 60                    # a copy older than this is renewed in the background when the page asks
PIN_SECONDS = 45                # what a write answered stays on top of the provider's list for this long (see `wrote`)
PINS: dict = {}                 # (account, domain, record id) -> {"row": the record as the provider answered the write, None = it was deleted, "until": time}
_LOCK = threading.Lock()


def _key(r: dict) -> tuple:
    return (r.get("account"), r.get("domain"), r.get("id"))


def _same(a: dict, b: dict) -> bool:
    return all(a.get(k) == b.get(k) for k in ("name", "fqdn", "type", "value", "ttl", "cloud", "ips"))


def apply_pins(rows: list[dict]) -> list[dict]:
    """The rows of a read of the providers with what the writes of the last PIN_SECONDS answered laid on top: a record that was changed shows as changed, one that was made is there, one that was deleted is gone.
    A pin goes when the provider's list agrees with it, or when its time is over."""
    now, out = time.time(), list(rows)

    with _LOCK:
        for key, p in list(PINS.items()):
            if now >= p["until"]:
                del PINS[key]
                continue

            i = next((n for n, r in enumerate(out) if _key(r) == key), None)

            if p["row"] is None:
                if i is None:
                    del PINS[key]                          # the list agrees: it is gone
                else:
                    del out[i]
            elif i is None:
                out.append(dict(p["row"]))
            elif _same(out[i], p["row"]):
                del PINS[key]                              # the list agrees: it is as written
            else:
                out[i] = dict(p["row"])

    return out


def wrote(account: str, domain: str, rid: str, row: dict | None) -> None:
    """A record was made or changed (`row`: the record as the provider answered the write) or deleted (None). The copy shows it at once, and for PIN_SECONDS no read of the provider undoes it: the list of a provider
    (Cloudflare's, seconds after a write) can still bring the OLD record, and the page - which reads right after the write - showed the old value until it was opened again (the owner, 2026-10-19: "I changed the
    DNS, it did not change, after a refresh it had")."""
    with _LOCK:
        PINS[(account, domain, rid)] = {"row": dict(row) if row else None, "until": time.time() + PIN_SECONDS}

    if CACHE["at"]:
        CACHE["rows"] = apply_pins(CACHE["rows"])           # (a copy that was never read is read, with the pins on it, at the next look)



def read_all() -> None:
    """Read the records of every account (one that fails does not hide the others) into the copy."""
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

    CACHE.update(at=time.time(), rows=apply_pins(rows), accounts=accs)


def refresh_in_background() -> None:
    """Renew the copy in a thread (one at a time): what is on the screen is the old copy, the next look has the new one."""
    with _LOCK:
        if CACHE["refreshing"]:
            return

        CACHE["refreshing"] = True

    def work() -> None:
        try:
            read_all()
        except Exception:
            log.exception("the DNS records could not be renewed in the background")
        finally:
            CACHE["refreshing"] = False

    threading.Thread(target=work, name="dns-refresh", daemon=True).start()


def warm() -> None:
    """At start: read the accounts once in the background, so the first open of the DNS page is served from the copy too."""
    try:
        if dnsprov.accounts():
            refresh_in_background()
    except Exception:
        log.exception("DNS warm-up skipped")


def stale() -> None:
    """A record was written (made, changed, deleted): the copy KEEPS its rows - `wrote` has laid the write on them - and is only marked old, so the next look gives the page those rows at once and the
    providers are read again in the background. `forget()` made the next look read EVERY domain of EVERY account before it answered (20-30 seconds with 26 domains): the page said "Record changed"
    and went on showing the old record until the answer came - or was refreshed by hand (the owner, 2026-10-20: "when I change a name or a DNS I must refresh the page")."""
    if CACHE["at"]:
        CACHE["at"] = 1.0


def forget() -> None:
    """A record was changed: the copy is out of date (the next read asks the providers)."""
    CACHE["at"] = 0.0

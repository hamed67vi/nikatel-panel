"""The panel's copy of the DNS records of every account. Reading every domain of every provider takes many seconds, so the DNS page and the Outbound page share ONE copy: whoever
needs the records asks here, a write anywhere calls `forget()`. (It lived in api/dns.py until 1.46.0; a service must not import the web layer.)"""

import threading
import time

from ..log import get_logger
from . import dnsprov

log = get_logger("dnscache")
CACHE: dict = {"at": 0.0, "rows": [], "accounts": [], "refreshing": False}
SECONDS = 60                    # a copy older than this is renewed in the background when the page asks
_LOCK = threading.Lock()


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

    CACHE.update(at=time.time(), rows=rows, accounts=accs)


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


def forget() -> None:
    """A record was changed: the copy is out of date (the next read asks the providers)."""
    CACHE["at"] = 0.0

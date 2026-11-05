"""A second vantage for the Configs monitoring (1.69.227). The probe tests every config from ONE Iran server; when that server's own network cannot carry a config
(2026-11-05: the servers of one provider stall every Cloudflare-fronted xHTTP config while the other Iran networks carry it at 3-4 MB/s) the config is shown DOWN for
everybody. A witness is a second probe on a server of ANOTHER network: it holds the same configs, tests them the same way, and a config the main probe calls down
but a witness gets through is shown UP, with the reason. A config both fail stays down. A witness never changes anything and is never a failover (an empty list
there would wipe the panel's own list): the panel only READS it, and keeps its config list equal to the main probe's (`mirror`)."""
from __future__ import annotations

import time

import httpx
from sqlalchemy import select

from ..db import session
from ..log import get_logger
from ..models import Config
from . import probe

log = get_logger("witness")
FRESH = 120           # seconds: a witness test older than this says nothing about now
READ_EVERY = 20       # seconds a read of the witnesses is kept (the quick job asks every 15 s)
BACKOFF = 60          # seconds without asking again after a witness did not answer (the monitor never waits long for a helper)
_STATE = {"read_at": 0.0, "fail_at": 0.0, "rows": {}}
_SYNCED: dict[tuple[str, str], tuple] = {}       # (witness, config name) -> the link key last put on that witness


def read(max_age: float = READ_EVERY) -> dict[str, dict]:
    """{config name: the best answer of the witnesses}. A row is a parse_health row plus "by", the witness that gave it; an "up" of any witness wins over a "down" of another.
    Never raises, never waits more than a few seconds; {} when there is no witness or none answers."""
    ws = probe.witnesses()

    if not ws:
        return {}

    now = time.time()

    if now - _STATE["read_at"] < max_age or now - _STATE["fail_at"] < BACKOFF:
        return _STATE["rows"]

    from . import configs

    rows: dict[str, dict] = {}
    answered = False

    for w in ws:
        try:
            d = probe._call(w, "GET", "/api/configs", timeout=12)
        except (httpx.HTTPError, RuntimeError, ValueError) as e:
            log.warning("witness %s does not answer: %s", w["name"], str(e)[:80])
            continue

        answered = True

        for r in configs.parse_health(d):
            old = rows.get(r["name"])

            if old is None or (r["status"] == "up" and old["status"] != "up"):
                rows[r["name"]] = {**r, "by": w["name"]}

    if answered:
        _STATE.update(read_at=now, rows=rows)
    else:
        _STATE.update(fail_at=now, rows={})

    return _STATE["rows"]


def rescue(row: dict, answer: dict | None, now: int) -> dict | None:
    """The main probe's row of a config it calls down + what the witnesses said -> the row to show instead (up, with the reason), or None when nobody gets through.
    Pure: see test_witness."""
    if not answer or row.get("status") != "down" or answer.get("status") != "up":
        return None

    if now - int(answer.get("checked_at") or 0) > FRESH:
        return None

    return {**row, "status": "up", "ping_ms": answer.get("ping_ms"), "error": f"works from {answer['by']}, another Iran server on another network; the main probe's own network does not get through"}


def mirror() -> dict:
    """Every 5 minutes: each witness holds exactly the configs of the main probe (add / change the link / delete, in ONE bulk call per witness).
    Returns {witness: {"add": n, "update": n, "delete": n}} - what was done."""
    ws = probe.witnesses()

    if not ws:
        return {}

    from . import configs

    with session() as db:
        names = [c.name for c in db.scalars(select(Config).where(Config.enabled.is_(True)))]

    ours: dict[str, str] = {}

    for n in names:
        try:
            ours[n] = configs.with_name(probe.config_uri(n), n)
        except (RuntimeError, ValueError):
            continue          # the main probe does not give this link now: leave the witness as it is for this one

    done = {}

    for w in ws:
        try:
            have = {str(c.get("name", "")) for c in probe._call(w, "GET", "/api/config/list", timeout=30).get("configs") or []}
            body = {"add": [], "update": [], "delete": sorted(n for n in have if n not in ours and n not in names)}

            for n, uri in ours.items():
                key = configs._link_key(uri)

                if n not in have:
                    body["add"].append({"uri": uri, "name": n})
                elif (w["name"], n) not in _SYNCED:
                    d = probe._call(w, "GET", "/api/config/uri?name=" + httpx.QueryParams({"n": n})["n"].replace("+", "%20"), timeout=20)

                    if d.get("ok") and configs._link_key(d.get("uri", "")) != key:
                        body["update"].append({"name": n, "uri": uri})
                    elif d.get("ok"):
                        _SYNCED[(w["name"], n)] = key
                elif _SYNCED[(w["name"], n)] != key:
                    body["update"].append({"name": n, "uri": uri})

            if not any(body.values()):
                continue

            d = probe._call(w, "POST", "/api/config/bulk", body, timeout=300)

            if not d.get("ok"):
                log.warning("witness %s refused the bulk change: %s", w["name"], str(d.get("error") or d.get("output"))[:120])
                continue

            res = d.get("results") or d

            for part in ("add", "update"):
                for it in res.get(part, []):
                    if it.get("ok"):
                        _SYNCED[(w["name"], str(it.get("name", "")))] = configs._link_key(ours.get(str(it.get("name", "")), ""))

            for n in body["delete"]:
                _SYNCED.pop((w["name"], n), None)

            done[w["name"]] = {k: len(v) for k, v in body.items()}
            log.info("witness %s mirrored: %s", w["name"], done[w["name"]])
        except (httpx.HTTPError, RuntimeError, ValueError) as e:
            log.warning("witness %s not mirrored: %s", w["name"], str(e)[:100])

    return done

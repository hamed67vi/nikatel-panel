"""The CDN side of the config statistics: when a host is a name behind the CDN of a DNS account (ArvanCloud cloud ON, Cloudflare proxied) every host of an inbound reaches the SAME origin port, so no counter on our server can
tell them apart. The CDN itself reports its traffic per domain; this module reads those reports (READ ONLY: GET, and one GraphQL query for Cloudflare) once an hour and keeps the bytes as hours like a port counter.

What is known and what is not (2026-10-21, no real ArvanCloud or Cloudflare key was available):
  * ArvanCloud CDN 4.0 has `GET /domains/{domain}/reports/traffics` with `period` (1h 3h 6h 12h 24h 7d 30d), `since`, `until` and a sub-domain filter (the official exporter of ArvanCloud reads it as
    `data.charts.traffics.series[]`, the series `reports.traffics.total` / `reports.traffics.saved`; ArvanCloud's changelog says the host filter is for the Growth plan and above). The name of the filter parameter is
    taken from the generated client (`filterSubdomain` <- `filter_subdomain`, the way `perPage` <- `per_page`), NOT seen on a real answer. So the first collection per account is a TEST (`probe`): the whole domain, a made-up
    host (a working filter answers 0) and the real host; a filter that does nothing (or is refused by the plan) marks the account "per-host numbers not available" and its hosts fall back to the shared origin port.
  * Cloudflare GraphQL `httpRequestsAdaptiveGroups` by `clientRequestHTTPHost` (`sum.edgeResponseBytes`) needs a token with "Analytics: Read" (the DNS token of the DNS page has only Zone: Read and DNS: Edit).
  * Whether a long WebSocket session is counted by the CDN report while it lasts or only when it ends is not known: the numbers are the CDN's own and APPROXIMATE for a VPN.
The parsers are tolerant (a shape they do not know gives None, never a made-up number) and the state of each account (`ok`, the error, `verified` after the first parse that found its series) is kept in the settings
and shown on the page."""
from __future__ import annotations

import math
import time

from ..log import get_logger
from . import arvan, cloudflare, dnsprov
from . import settings as st

log = get_logger("cfgcdn")
EVERY = 3300                            # seconds between two reads of one account (the report asked is the last hour)
PROBE_EVERY = 24 * 3600                 # a failed or unproven account is tried again after this long
NONSENSE = "zz-nikatel-probe-no-such-host"


# ------------------------------------------------------------------ pure: the answers
def _number(v):
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None

    return f if math.isfinite(f) else None


def parse_arvan(j) -> dict | None:
    """The traffic of an ArvanCloud `reports/traffics` answer: {"total": bytes served, "saved": bytes served from the cache or None, "points": how many numbers were added}. The series named `...total` is used (else the
    first series); the numbers of its `data` are added. None when the answer has no `charts.traffics.series`."""
    d = j.get("data") if isinstance(j, dict) else None
    ch = d.get("charts") if isinstance(d, dict) else None
    tr = ch.get("traffics") if isinstance(ch, dict) else None
    series = tr.get("series") if isinstance(tr, dict) else None

    if not isinstance(series, list) or not series:
        return None

    def vals(s):
        data = s.get("data") if isinstance(s, dict) else None
        return [x for x in (_number(i) for i in (data if isinstance(data, list) else [])) if x is not None]

    total = next((s for s in series if isinstance(s, dict) and str(s.get("name") or "").endswith("total")), series[0])
    saved = next((s for s in series if isinstance(s, dict) and str(s.get("name") or "").endswith("saved")), None)
    t = vals(total)
    return {"total": int(max(0.0, sum(t))), "saved": int(max(0.0, sum(vals(saved)))) if saved is not None else None, "points": len(t)}


def parse_cf(j) -> dict[str, int] | None:
    """{host: bytes} from a Cloudflare GraphQL answer (`viewer.zones[0].httpRequestsAdaptiveGroups`); None when the answer has errors or another shape."""
    if not isinstance(j, dict) or j.get("errors"):
        return None

    zones = ((j.get("data") or {}).get("viewer") or {}).get("zones") if isinstance(j.get("data"), dict) else None

    if not isinstance(zones, list) or not zones or not isinstance(zones[0], dict):
        return None

    groups = zones[0].get("httpRequestsAdaptiveGroups")

    if not isinstance(groups, list):
        return None

    out: dict[str, int] = {}

    for g in groups:
        host = str(((g or {}).get("dimensions") or {}).get("clientRequestHTTPHost") or "").strip().lower()
        n = _number(((g or {}).get("sum") or {}).get("edgeResponseBytes"))

        if host and n is not None:
            out[host] = out.get(host, 0) + int(max(0.0, n))

    return out


def error_text(j) -> str:
    errs = j.get("errors") if isinstance(j, dict) else None
    return "; ".join(str(e.get("message", e))[:100] for e in (errs or [])[:2] if e)[:200]


def verdict(total: int | None, fake: int | None, host: int | None) -> tuple[bool | None, str]:
    """(does the host filter work?, the sentence). A filter that works answers 0 for a host that does not exist while the whole domain answered something; the real host must not be more than the domain."""
    if total is None:
        return None, "the report of the domain could not be read"

    if fake is None or host is None:
        return None, "the report with a host filter could not be read (the plan may not include it)"

    if total > 0 and fake >= total:
        return False, "ArvanCloud ignored the host filter (a host that does not exist got the whole domain): per-host numbers are not available"

    if host > total:
        return False, "the host filter gave more than the whole domain: not trusted"

    return True, "the host filter works"


# ------------------------------------------------------------------ talking to the CDNs (read only)
def _label(fqdn: str, domain: str) -> str:
    return fqdn[: -len(domain) - 1] if domain and fqdn.endswith("." + domain) else fqdn


def arvan_report(key: str, domain: str, host: str | None, period: str = "1h") -> dict | None:
    """One `reports/traffics` read, parsed (None = an answer of a shape we do not know). RuntimeError from arvan._req when the key or the plan refuses."""
    params: dict = {"period": period}

    if host:
        params["filter_subdomain"] = host

    with arvan.using(key):
        return parse_arvan(arvan._req("GET", f"/domains/{domain}/reports/traffics", params=params))


def cf_report(token: str, zone_id: str, since: str, until: str) -> dict[str, int]:
    """{host: bytes sent by the edge} for a window, from the GraphQL analytics of Cloudflare. RuntimeError with the reason when the token lacks Analytics: Read or the shape is not the one we know."""
    q = ("query($z:String!,$s:Time!,$u:Time!){viewer{zones(filter:{zoneTag:$z}){httpRequestsAdaptiveGroups(limit:1000,filter:{datetime_geq:$s,datetime_lt:$u},orderBy:[sum_edgeResponseBytes_DESC])"
         "{dimensions{clientRequestHTTPHost} sum{edgeResponseBytes}}}}}")
    j = cloudflare.req(token, "POST", "/graphql", body={"query": q, "variables": {"z": zone_id, "s": since, "u": until}})
    out = parse_cf(j)

    if out is None:
        raise RuntimeError("Cloudflare did not answer with the analytics" + (": " + error_text(j) if error_text(j) else " (the token may lack Analytics: Read)"))

    return out


def probe_arvan(key: str, domain: str, fqdn: str) -> dict:
    """The three reads that say whether per-host numbers exist: the whole domain (24 h), a made-up host, the real host. Never raises: {"ok", "filter", "total", "fake", "host", "text", "error"}."""
    res: dict = {"ok": False, "filter": None, "total": None, "fake": None, "host": None, "style": "fqdn", "text": "", "error": ""}

    try:
        whole = arvan_report(key, domain, None, "24h")
    except RuntimeError as e:
        res["error"] = str(e)[:200]
        res["text"] = "ArvanCloud refused or did not answer: " + res["error"]
        return res

    if whole is None:
        res["text"] = "ArvanCloud answered, but not in the shape we know (data.charts.traffics.series): nothing is stored from it"
        return res

    res["total"], res["ok"] = whole["total"], True

    try:
        fake = arvan_report(key, domain, f"{NONSENSE}.{domain}", "24h")
        real = arvan_report(key, domain, fqdn, "24h")
        res["fake"], res["host"] = fake["total"] if fake else None, real["total"] if real else None

        if (res["host"] in (None, 0)) and res["total"]:
            alt = arvan_report(key, domain, _label(fqdn, domain), "24h")        # the filter may want the label (the part before the domain)
            alt_fake = arvan_report(key, domain, NONSENSE, "24h")

            if alt and alt["total"] and (alt_fake is None or alt_fake["total"] < res["total"]):
                res["host"], res["fake"], res["style"] = alt["total"], alt_fake["total"] if alt_fake else None, "label"
    except RuntimeError as e:
        res["error"] = str(e)[:200]

    res["filter"], res["text"] = verdict(res["total"], res["fake"], res["host"])
    return res


# ------------------------------------------------------------------ the state of each account
def state() -> dict:
    return dict(st.get("cfgstats").get("cdn_state") or {})


def _set_state(account: str, **kw) -> None:
    cur = state()
    cur[account] = {**cur.get(account, {}), **kw}
    st.put("cfgstats", {"cdn_state": cur})


def enabled() -> bool:
    return bool(st.get("cfgstats").get("cdn", True))


def set_enabled(on: bool) -> None:
    st.put("cfgstats", {"cdn": bool(on)})


def collect(groups: list[dict], add, now: int | None = None) -> dict:
    """Read the last hour of every CDN domain of the map (`groups`: cfgmap's `cdn` list) once an hour per account and put the bytes into the accumulator through `add(src, hour, rx, tx)`.
    Returns {account: "read" | "skipped" | "<error>"}. Never raises."""
    now = int(now or time.time())
    out: dict[str, str] = {}

    if not enabled() or not groups:
        return out

    try:
        accs = {a.id: a for a in dnsprov.accounts()}
    except Exception as e:                                   # noqa: BLE001 - the settings could not be read: the turn is skipped
        log.info("cdn: the accounts could not be read: %s", str(e)[:100])
        return out

    by_acc: dict[str, list[dict]] = {}

    for g in groups:
        by_acc.setdefault(g["account"], []).append(g)

    hour = (now - 1800) // 3600 * 3600
    stt = state()

    for aid, gs in by_acc.items():
        acc, s = accs.get(aid), stt.get(aid, {})

        if acc is None:
            out[aid] = "the DNS account is gone"
            continue

        if now - int(s.get("at") or 0) < EVERY:
            out[aid] = "skipped"
            continue

        if s.get("filter") is False and acc.provider == "arvan" and now - int(s.get("probed") or 0) < PROBE_EVERY:
            out[aid] = "skipped"
            continue

        try:
            if acc.provider == "arvan":
                out[aid] = _collect_arvan(acc, gs, s, add, hour, now)
            elif acc.provider == "cloudflare":
                out[aid] = _collect_cf(acc, gs, add, hour, now)
            else:
                out[aid] = "unknown provider"
        except Exception as e:                               # noqa: BLE001 - one account's failure must not stop the others
            out[aid] = str(e)[:200] or e.__class__.__name__
            _set_state(aid, ok=False, at=now, error=out[aid])

    return out


def _collect_arvan(acc, gs: list[dict], s: dict, add, hour: int, now: int) -> str:
    first = gs[0]

    if s.get("filter") is None or now - int(s.get("probed") or 0) > PROBE_EVERY and not s.get("verified"):
        p = probe_arvan(acc.key, first["domain"], first["fqdn"])
        _set_state(acc.id, ok=p["ok"], filter=p["filter"], style=p["style"], probed=now, at=now, error=p["error"], text=p["text"], verified=bool(p["ok"] and p["filter"]))

        if not p["ok"] or p["filter"] is not True:
            return p["text"] or "the report is not available"

        s = state().get(acc.id, {})

    style = s.get("style") or "fqdn"
    done = 0

    for g in gs:
        host = g["fqdn"] if style == "fqdn" else _label(g["fqdn"], g["domain"])
        rep = arvan_report(acc.key, g["domain"], host, "1h")

        if rep is not None:
            add(g["src"], hour, 0, rep["total"])
            done += 1

    _set_state(acc.id, ok=True, at=now, error="", read=done, verified=True)
    return "read"


def _collect_cf(acc, gs: list[dict], add, hour: int, now: int) -> str:
    prov = dnsprov.provider_of(acc)
    zones = prov._zones()
    since = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now - 3600))
    until = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now))
    done = 0

    for dom in sorted({g["domain"] for g in gs}):
        zid = zones.get(dom)

        if not zid:
            continue

        by_host = cf_report(acc.key, zid, since, until)

        for g in gs:
            if g["domain"] == dom:
                add(g["src"], hour, 0, by_host.get(g["fqdn"].lower(), 0))
                done += 1

    _set_state(acc.id, ok=True, at=now, error="", read=done, verified=True, filter=True)
    return "read"


def probe(account_id: str, fqdn: str, domain: str = "") -> dict:
    """The test the page offers: read the report of one domain of an account (Arvan: whole domain, made-up host, real host; Cloudflare: the last hour of the zone) and say what it understood. Stores the result as the state of the account."""
    acc = dnsprov.get(account_id)
    fq = str(fqdn or "").strip().lower()
    dom = str(domain or "").strip().lower() or next((d for d in sorted(prov_domains(acc), key=len, reverse=True) if fq == d or fq.endswith("." + d)), "")

    if not dom:
        raise ValueError("this name is not in a domain of that DNS account")

    now = int(time.time())

    if acc.provider == "arvan":
        p = probe_arvan(acc.key, dom, fq)
        _set_state(acc.id, ok=p["ok"], filter=p["filter"], style=p["style"], probed=now, at=0 if not p["ok"] else now, error=p["error"], text=p["text"], verified=bool(p["ok"] and p["filter"]))
        return {"provider": "arvan", "domain": dom, **p}

    try:
        zid = dnsprov.provider_of(acc)._zones().get(dom)
        got = cf_report(acc.key, zid, time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now - 3600)), time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now))) if zid else {}
        res = {"provider": "cloudflare", "domain": dom, "ok": True, "filter": True, "hosts": len(got), "host": got.get(fq), "text": f"Cloudflare analytics answered: {len(got)} host{'' if len(got) == 1 else 's'} in the last hour"}
        _set_state(acc.id, ok=True, filter=True, probed=now, error="", text=res["text"], verified=True)
    except RuntimeError as e:
        res = {"provider": "cloudflare", "domain": dom, "ok": False, "filter": None, "text": str(e)[:200], "error": str(e)[:200]}
        _set_state(acc.id, ok=False, probed=now, error=res["error"], text=res["text"])

    return res


def prov_domains(acc) -> list[str]:
    return dnsprov.provider_of(acc).domains()

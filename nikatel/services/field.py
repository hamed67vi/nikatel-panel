"""The FIELD TEST (1.69.231): what a real Iranian network sees, sent by the Iran test kit and read by the Doctor.

Everything the panel measures is measured from datacenters; the people who use the configs sit on a SIM's hotspot or a home line, and those networks behave differently (the same config passes from one Iran
server and stalls after 30 KB from another). The kit - a folder with a PowerShell script and an Xray client that a person in Iran runs on a laptop - measures, from THAT network: the reach of our addresses (DNS, TCP,
TLS, ping, path MTU) and what every config really carries: ten sites (Google, YouTube with its thumbnails and video servers, Instagram with a picture, Telegram web and api, WhatsApp, X), the delay of twelve small
requests (what a tap waits), a 3 MB download and, for the key configs, a long download second by second, a player's way of loading a video (would 720p, 1080p, 1440p, 4K have stopped to buffer), six pictures at once
(a feed), an upload - and for the DIRECT configs (TCP-3, TCP-4, TCP-5 and the LAB ones) whether the IP, a port or the SNI is filtered on that network (TCP to six ports, TLS with several names, ping).
When it ends it sends the result here, `POST /api/field/ingest`, with an API token of scope `field` (that scope may do this one thing; services/tokens.py).

The panel keeps a COMPACT copy (`compact`, pure; a table `field_runs`, 60 days) and the Doctor's rules read it (`judge`, pure): what is blocked, what connects and opens nothing, what stalls, what is slow, what is
unstable (from one run's TCP and from run to run), lag (the delay of a tap), video that does not play at 1080p, pictures that load slowly together, a name the network's DNS cannot or must not answer, a low path MTU, and for
the direct configs `direct_verdict`: ipblock (nothing answers), port / reset (a port is dropped or reset), refused (the server itself says no: not a filter), tls (handshakes dropped), sni (one name filtered) - and the comparison that matters most: when TCP-3 / TCP-4 (VLESS TLS on 8680 straight to a node)
fail from a network while the Reality or xHTTP-Reality way into the SAME node (the kit's LAB configs) passes, the way in is the problem and the other type is the answer; when every way into the node fails, the node's
address is throttled from there. Every finding becomes an event once in 12 hours (with what to do about it), a recovery too, and the Doctor's look at a suffering tunnel (userq.investigate) adds what the field says
about the configs that ride it (`story_for`). The panel changes nothing by itself from these findings: they say where the problem is and what would help."""
from __future__ import annotations

import ipaddress
import itertools
import math
import re
import time

from sqlalchemy import delete, func, select
from sqlalchemy.exc import SQLAlchemyError

from ..db import session
from ..log import get_logger
from ..models import FieldRun
from .events import emit

log = get_logger("field")
KEEP_DAYS = 60
KEEP_RUNS = 500
PER_HOUR = 20                    # runs one token may send in an hour
MAX_BYTES = 1_500_000            # the size of one result (a real one is about 100 KB)
TELL_EVERY = 12 * 3600           # the same finding is told again after 12 hours
HISTORY = 36 * 3600              # the runs of the same network that count for "up and down from run to run"
SITES = ("google", "youtube", "youtube-thumb", "youtube-video", "instagram", "instagram-img", "telegram-web", "telegram-api", "whatsapp-web", "x")      # what the kit opens through every config
FULL3 = 3_000_000                # the bytes of the quick download
SLOW_BULK = 1.5                  # Mb/s: a long download slower than this is slow
SLOW_QUICK = 1.0                 # Mb/s: the 3 MB download slower than this is slow
LAG_MED = 600.0                  # ms: the median of twelve small requests on one connection above this is lag (a tap waits that long)
LAG_P95 = 2000.0                 # ms: ... or the slow ones above this
VIDEO_MIN = 5.0                  # Mb/s a video must play at without stopping: 1080p
PICTURES_MIN = 2.0               # Mb/s six pictures at once must reach (a feed, stories)
TIERS = ((25.0, "4K"), (16.0, "4K (light)"), (8.0, "1440p"), (5.0, "1080p"), (2.5, "720p"))
BASES = {"TCP-3": "TR1", "TCP-4": "DE1"}           # a direct TLS 8680 config and the node it goes to; the kit's LAB configs of that node are LAB-<node>-reality1845 and -xhttp2082
CONNECTIONS = ("mobile", "home", "wifi", "other")


class TooMany(Exception):
    """One token sent more runs than PER_HOUR in an hour."""


# ------------------------------------------------------------------ the compact copy
def _num(x, lo: float = 0.0, hi: float = 1e12, default=None):
    try:
        v = float(x)
    except (TypeError, ValueError):
        return default

    if not math.isfinite(v):
        return default

    return min(max(v, lo), hi)


def _s(x, n: int) -> str:
    return re.sub(r"[\x00-\x1f]", " ", str(x if x is not None else ""))[:n]


def _word(x, n: int) -> str:
    return re.sub(r"[^A-Za-z0-9 _.-]", "", str(x if x is not None else "")).strip()[:n]


def compact(p) -> dict:
    """Pure. The run as the panel keeps it: bounded, text cleaned, only what `judge` and the story need. ValueError for something that is not the result of the kit."""
    if not isinstance(p, dict) or not str(p.get("kit") or "") or not (isinstance(p.get("configs"), dict) or isinstance(p.get("net"), dict)):
        raise ValueError("this is not the result of the Iran test kit")

    ident = p.get("identity") if isinstance(p.get("identity"), dict) else {}
    conn = _word(p.get("connection"), 16).lower()
    run = {"kit": _s(p.get("kit"), 8), "mode": _word(p.get("mode"), 8), "started": _s(p.get("started"), 24), "operator": _word(p.get("operator"), 32) or "unknown",
           "connection": conn if conn in CONNECTIONS else "other", "city": _word(p.get("city"), 32) or "unknown", "ip": _s(ident.get("ip"), 45), "asn": _s(ident.get("asn"), 80),
           "isp": _s(ident.get("isp"), 80), "country": _s(ident.get("country"), 32), "ipv6": bool((p.get("ipv6") or {}).get("ok")) if isinstance(p.get("ipv6"), dict) else None, "mtu": {}, "net": [], "cfg": []}

    for ip, m in list((p.get("mtu") or {}).items())[:8] if isinstance(p.get("mtu"), dict) else []:
        if isinstance(m, dict):
            run["mtu"][_s(ip, 45)] = int(_num(m.get("path_mtu"), 0, 9000, 0)) or ("blocked" if m.get("icmp") == "blocked" else None)

    for key, r in list(p["net"].items())[:80] if isinstance(p.get("net"), dict) else []:
        if not isinstance(r, dict):
            continue

        tcp = r.get("tcp") if isinstance(r.get("tcp"), dict) else {}
        ms = tcp.get("ms") if isinstance(tcp.get("ms"), dict) else {}
        tls = r.get("tls") if isinstance(r.get("tls"), dict) else None
        dns = r.get("dns") if isinstance(r.get("dns"), dict) else None
        ping = r.get("ping") if isinstance(r.get("ping"), dict) else {}
        prtt = ping.get("rtt") if isinstance(ping.get("rtt"), dict) else {}
        row = {"key": _s(key, 80), "labels": [_s(x, 32) for x in str(r.get("label") or "").split(",") if x.strip()][:8], "ip": _s(r.get("ip"), 45),
               "tcp_n": int(_num(tcp.get("tried"), 0, 1000, 0)), "tcp_ok": int(_num(tcp.get("ok"), 0, 1000, 0)), "tcp_med": _num(ms.get("med")), "tcp_max": _num(ms.get("max")), "tcp_sd": _num(ms.get("sd")),
               "tcp_err": {_s(k, 24): int(_num(v, 0, 1000, 0)) for k, v in list((tcp.get("errors") or {}).items())[:4]} if isinstance(tcp.get("errors"), dict) else {},
               "tls_ok": int(_num(tls.get("ok"), 0, 100, 0)) if tls else None, "tls_of": int(_num(tls.get("of"), 0, 100, 0)) if tls else None,
               "ping_n": int(_num(ping.get("sent"), 0, 1000, 0)), "ping_lost": int(_num(ping.get("lost"), 0, 1000, 0)), "ping_med": _num(prtt.get("med")), "dns": None}

        if dns:
            sysd = dns.get("system") if isinstance(dns.get("system"), dict) else {}
            cf = dns.get("cf") if isinstance(dns.get("cf"), dict) else {}
            ips = sysd.get("ips") if isinstance(sysd.get("ips"), list) else []
            row["dns"] = {"sys_ok": bool(sysd.get("ok")), "sys_ms": _num(sysd.get("ms")), "sys_ip": _s(ips[0], 45) if ips else "", "cf_ok": bool(cf.get("ok")), "cf_ms": _num(cf.get("ms"))}

        run["net"].append(row)

    for label, r in list(p["configs"].items())[:80] if isinstance(p.get("configs"), dict) else []:
        if not isinstance(r, dict):
            continue

        q = r.get("quick") if isinstance(r.get("quick"), dict) else {}
        sites = {_s(s, 24): bool(v.get("ok")) for s, v in list(q.items())[:16] if s not in ("down3MB", "download") and isinstance(v, dict)}
        sites.setdefault("google", False)
        d3 = q.get("download") if isinstance(q.get("download"), dict) else q.get("down3MB") if isinstance(q.get("down3MB"), dict) else {}          # (the quick download was 3 MB, `down3MB`; the kit now asks for 1 MB and says so in `want`)
        b = r.get("bulk") if isinstance(r.get("bulk"), dict) else None
        lat = r.get("latency") if isinstance(r.get("latency"), dict) else None
        par = r.get("parallel") if isinstance(r.get("parallel"), dict) else None
        vid = r.get("video") if isinstance(r.get("video"), dict) else None
        row = {"label": _s(r.get("label") or label, 32), "kind": _s(r.get("kind"), 8), "net": _s(r.get("net"), 8), "sec": _s(r.get("sec"), 8), "skipped": _s(r.get("skipped"), 40) or None,
               "sites": sites, "g": sites["google"], "ok_n": sum(1 for v in sites.values() if v), "sites_n": len(sites), "mbps3": _num(d3.get("mbps")), "bytes3": int(_num(d3.get("bytes"), 0, 10 ** 9, 0)), "want": int(_num(d3.get("want"), 1, 10 ** 9, FULL3)),
               "bulk": None, "up": _num((r.get("upload") or {}).get("mbps")) if isinstance(r.get("upload"), dict) else None, "exit": _s(r.get("exit_ip"), 45) or None,
               "lat": {"n": int(_num(lat.get("n"), 0, 1000, 0)), "lost": int(_num(lat.get("lost"), 0, 1000, 0)), "med": _num(lat.get("med")), "p95": _num(lat.get("p95")), "first": _num(lat.get("first_ms"))} if lat else None,
               "par": {"n": int(_num(par.get("n"), 0, 100, 0)), "fail": int(_num(par.get("fail"), 0, 100, 0)), "mbps": _num(par.get("mbps"))} if par else None,
               "video": {"best": _num(vid.get("best_mbps"), 0, 1000, 0.0), "startup": _num(vid.get("startup_s")), "segs": int(_num(vid.get("segments"), 0, 100, 0)), "src": _s(vid.get("source"), 40),
                         "tiers": {_s(k, 6): int(_num((v or {}).get("stalls"), 0, 1000, 0)) for k, v in list((vid.get("tiers") or {}).items())[:6] if isinstance(v, dict)} if isinstance(vid.get("tiers"), dict) else {}}
               if vid and int(_num(vid.get("segments"), 0, 100, 0)) >= 3 else None}

        if b:
            line = [x for x in (_num(v, 0, 1e5) for v in (b.get("per_second_mbps") if isinstance(b.get("per_second_mbps"), list) else [])[:40]) if x is not None]
            row["bulk"] = {"mb": round((_num(b.get("bytes"), 0, 1e10, 0) or 0) / 1e6, 1), "s": _num(b.get("seconds")), "avg": _num(b.get("avg_mbps")), "stalled": bool(b.get("stalled")),
                           "stall_at": int(_num(b.get("bytes_before_first_stall"), -1, 1e10, -1)), "line": line, "err": _s(b.get("error"), 40)}

        run["cfg"].append(row)

    run["direct"] = {}

    for ip, d in list(p["direct"].items())[:8] if isinstance(p.get("direct"), dict) else []:
        if not isinstance(d, dict):
            continue

        ports = {}

        for pt, v in list(d["ports"].items())[:10] if isinstance(d.get("ports"), dict) else []:
            if isinstance(v, dict):
                errs = v.get("err") if isinstance(v.get("err"), dict) else {}
                ports[_s(pt, 6)] = [int(_num(v.get("ok"), 0, 100, 0)), int(_num(v.get("n"), 0, 100, 0)), {_s(k, 24): int(_num(c, 0, 100, 0)) for k, c in list(errs.items())[:4]}]

        tls = []

        for key, v in list(d["tls"].items())[:30] if isinstance(d.get("tls"), dict) else []:
            if isinstance(v, dict):
                pt, _, sni = str(key).partition("|")
                tls.append({"port": _s(pt, 6), "sni": _s(sni, 64), "ok": bool(v.get("ok")), "err": _s(v.get("err"), 40), "cert": _s(v.get("cert"), 80)})

        ping = d.get("ping") if isinstance(d.get("ping"), dict) else {}
        run["direct"][_s(ip, 45)] = {"labels": [_s(x, 32) for x in (d.get("labels") if isinstance(d.get("labels"), list) else [])][:8], "cfgports": [int(_num(x, 0, 65535, 0)) for x in (d.get("cfgports") if isinstance(d.get("cfgports"), list) else [])][:8],
                                      "ports": ports, "tls": tls, "ping": [int(_num(ping.get("lost"), 0, 100, 0)), int(_num(ping.get("sent"), 0, 100, 0))]}

    run["panel"] = {"names": {}, "ips": {}, "matrix": {}}
    pn = p.get("panel") if isinstance(p.get("panel"), dict) else {}

    for name, r in list(pn["names"].items())[:16] if isinstance(pn.get("names"), dict) else []:
        if not isinstance(r, dict):
            continue

        dns = r.get("dns") if isinstance(r.get("dns"), dict) else {}
        sysd = dns.get("system") if isinstance(dns.get("system"), dict) else {}
        cf = dns.get("cf") if isinstance(dns.get("cf"), dict) else {}
        sips = sysd.get("ips") if isinstance(sysd.get("ips"), list) else []
        tcp = r.get("tcp") if isinstance(r.get("tcp"), dict) else None
        tls = r.get("tls") if isinstance(r.get("tls"), dict) else None
        http = r.get("http") if isinstance(r.get("http"), dict) else None
        run["panel"]["names"][_s(name, 64)] = {"dns": {"sys_ok": bool(sysd.get("ok")), "sys_ip": _s(sips[0], 45) if sips else "", "cf_ok": bool(cf.get("ok"))}, "tcp": _tcp3(tcp), "tls": {"ok": bool(tls.get("ok")), "err": _s(tls.get("err"), 80), "issuer": _s(tls.get("issuer"), 80)} if tls else None, "http": _get(http)}

    for lab, a in list(pn["ips"].items())[:8] if isinstance(pn.get("ips"), dict) else []:
        if isinstance(a, dict):
            ping = a.get("ping") if isinstance(a.get("ping"), dict) else {}
            run["panel"]["ips"][_s(lab, 40)] = {"ip": _s(a.get("ip"), 45), "ping": [int(_num(ping.get("lost"), 0, 100, 0)), int(_num(ping.get("sent"), 0, 100, 0))], "tcp443": _tcp3(a.get("tcp443")), "tcp80": _tcp3(a.get("tcp80"))}

    for key, g in list(pn["matrix"].items())[:60] if isinstance(pn.get("matrix"), dict) else []:
        if isinstance(g, dict):
            run["panel"]["matrix"][_s(key, 90)] = _get(g)

    if not run["cfg"] and not run["net"]:
        raise ValueError("the result has no tests in it")

    return run


def _tcp3(t) -> list | None:
    """[ok, tried, {error: count}] of a TCP probe as the kit writes it, or None."""
    if not isinstance(t, dict):
        return None

    errs = t.get("err") if isinstance(t.get("err"), dict) else {}
    return [int(_num(t.get("ok"), 0, 100, 0)), int(_num(t.get("n"), 0, 100, 0)), {_s(k, 24): int(_num(c, 0, 100, 0)) for k, c in list(errs.items())[:4]}]


def _get(g) -> dict | None:
    """One HTTPS fetch of the subscription as the kit writes it: {rc, code, ms, links}, or None."""
    if not isinstance(g, dict):
        return None

    return {"rc": int(_num(g.get("rc"), -1, 1000, -1)), "code": int(_num(g.get("code"), 0, 999, 0)), "ms": _num(g.get("ms")), "links": int(_num(g.get("links"), 0, 10 ** 5, 0))}


# ------------------------------------------------------------------ the Doctor's rules
def row_state(c: dict) -> str:
    """Pure. ok | partial (some of the four sites do not open) | slow | stall | closed (not even Google opens) | skipped (the address did not answer, or Xray did not start)."""
    if c.get("skipped"):
        return "skipped"

    if not c.get("g"):
        return "closed"

    b = c.get("bulk")

    if (b and b.get("stalled")) or int(c.get("bytes3") or 0) < c.get("want", FULL3) * 0.99:
        return "stall"

    if c.get("ok_n", 0) < c.get("sites_n", len(SITES)):
        return "partial"

    if (b and b.get("avg") is not None and b["avg"] < SLOW_BULK) or (c.get("mbps3") is not None and c["mbps3"] < SLOW_QUICK):
        return "slow"

    return "ok"


def _bogus(ip: str) -> bool:
    """An answer no real server has: private, loopback, unspecified, reserved - what a tampering DNS gives for a blocked name (Iran uses 10.10.34.x)."""
    try:
        a = ipaddress.ip_address(ip)
    except ValueError:
        return False

    return bool(a.is_private or a.is_loopback or a.is_unspecified or a.is_reserved or a.is_multicast)


def _kb(n) -> str:
    return f"{int(n) // 1000} KB" if int(n) < 1_000_000 else f"{int(n) / 1e6:.1f} MB"


def _f(kind: str, level: str, text: str, cfgs: list[str], hint: str = "") -> dict:
    labs = sorted(set(cfgs))
    return {"key": f"{kind}:{','.join(labs)}"[:160], "kind": kind, "level": level, "text": text, "cfgs": labs, "hint": hint}


def tier_name(mbps) -> str:
    """The video quality a Mb/s figure carries without stopping: 720p from 2.5, 1080p from 5, 1440p from 8, 4K from 16 (light) and 25."""
    return next((name for lo, name in TIERS if float(mbps or 0) >= lo), "below 720p")


DIRECT_KINDS = {"ipblock": ("ipblock", "bad", "Give the node another IP, or reach it through an entry inside Iran: nothing on this address can work from this network."),
                "allports": ("ipblock", "bad", "Every port is filtered while ping answers: the address is blocked for TCP; another IP, or an entry inside Iran, is the cure."),
                "port": ("portblock", "warn", "Serve the same inbound on a port that answers here (443 or 8443)."),
                "reset": ("portblock", "warn", "Something resets the connections to this port: move the inbound to another port, or use Reality / xHTTP-Reality to the node."),
                "refused": ("refused", "warn", "The server itself refuses the connection (nothing listens on that port): a server problem, not a filter - check the inbound on the node."),
                "tls": ("tlsblock", "warn", "Handshakes are dropped on the way: Reality or xHTTP-Reality to the same node may pass (see the LAB result)."),
                "sni": ("sniblock", "warn", "Change the SNI / domain of these configs; the node itself is reachable.")}


def direct_verdict(d: dict) -> tuple[str, str]:
    """Pure. One direct address of a node as the kit saw it (TCP to six ports, TLS with several SNIs, ping) -> (code, words): ok | ipblock | allports | port | reset | refused | tls | sni.
    A timeout is a drop (a filter); a refusal is the server saying no (not a filter); a reset on the config's port while others answer is an injected reset."""
    ports = d.get("ports") or {}
    open_p = [p for p, v in ports.items() if v[0] > 0]
    lost, sent = (d.get("ping") or [0, 0])[:2]

    if ports and not open_p:
        kinds = [k.lower() for v in ports.values() for k in v[2]]

        if kinds and all("refused" in k for k in kinds):                       # a refusal is an answer: the server is there and nothing listens (not a filter)
            return "refused", "every port refuses the connection: the server answers but nothing listens there"

        if sent and lost >= sent:
            return "ipblock", "nothing answers, no port and no ping: the IP looks blocked from this network"

        return "allports", "every port is filtered while ping still answers"

    bad = [str(p) for p in d.get("cfgports") or [] if str(p) in ports and ports[str(p)][0] == 0]

    if bad:
        errs = {k.lower(): c for p in bad for k, c in ports[p][2].items()}
        refused = sum(c for k, c in errs.items() if "refused" in k)
        reset = sum(c for k, c in errs.items() if "reset" in k)
        where = f"port {', '.join(bad)} does not answer while {', '.join(open_p)} does"

        if refused and not reset and refused >= sum(errs.values()):
            return "refused", f"{where} - it is refused (nothing listens there)"

        return ("reset", f"{where} - the connection is reset (injected)") if reset else ("port", f"{where} - it is dropped")

    tls = d.get("tls") or []

    if tls and not any(t["ok"] for t in tls):
        return "tls", "it connects but every TLS handshake fails (the handshake is dropped)"

    failed = sorted(s for s in {t["sni"] for t in tls} if all(not t["ok"] for t in tls if t["sni"] == s))          # a name counts as filtered only when it fails on EVERY port it was tried on (a lost packet fails one handshake)

    if failed:
        return "sni", f"the handshake fails for {', '.join(failed[:4])} and passes for other names (that name is filtered)"

    return "ok", "the IP, its ports and the handshakes all answer"


def panel_works(g: dict | None) -> bool:
    """Pure. A fetch of the subscription that really gave the links: the panel answered 200 and the body held them."""
    return bool(g) and g["rc"] == 0 and g["code"] == 200 and g["links"] >= 5


def _fetched(pn: dict, name: str) -> dict:
    """The fetches of the subscription through every address for one name: {'name|address': fetch}."""
    return {k: g for k, g in (pn.get("matrix") or {}).items() if k.split("|", 1)[0] == name}


def panel_view(run: dict) -> dict:
    """Pure. The panel as one network saw it: which subscription names open and which addresses answer on 443."""
    pn = run.get("panel") or {}
    return {"names": {n: "ok" if panel_works(r.get("http")) else "fail" for n, r in (pn.get("names") or {}).items() if _fetched(pn, n)},
            "addresses": {lab: "ok" if a["tcp443"] and a["tcp443"][0] > 0 else "dead" for lab, a in (pn.get("ips") or {}).items()}}


def _panel_findings(cur: dict, who: str) -> list[dict]:
    """Pure. The panel and its subscription names as the kit saw them: which address does not answer, which name opens only through another address, which name is filtered (or tampered with) here, which is simply not served."""
    pn = cur.get("panel") or {}
    names, addrs = pn.get("names") or {}, pn.get("ips") or {}
    out: list[dict] = []
    alive = [lab for lab, a in addrs.items() if a["tcp443"] and a["tcp443"][0] > 0]

    for lab, a in addrs.items():
        t = a["tcp443"]

        if t and t[1] and t[0] == 0:
            lost, sent = a["ping"]
            out.append(_f("panelip", "warn" if lab == "monitor-host" else "bad", f"{who}: the panel address {lab} does not answer on port 443 (ping lost {lost} of {sent})" + (f" while {', '.join(alive)} answer" if alive else "") + ".", [lab],
                          "Point the subscription names at an address that answers on this network, or tunnel them through an Iran server."))

    groups: dict[str, list[str]] = {}
    via_of: dict[str, list[str]] = {}

    for name, r in names.items():
        tried = _fetched(pn, name)

        if not tried:                                                       # a name the kit does not fetch the subscription from (the dashboard, the monitor)
            continue

        d = r.get("dns") or {}

        if (not d.get("sys_ok") and d.get("cf_ok")) or (d.get("sys_ok") and _bogus(d.get("sys_ip", ""))):
            groups.setdefault("paneldns", []).append(name + (f" (answers {d['sys_ip']})" if d.get("sys_ok") else " (no answer)"))

        if panel_works(r.get("http")):
            continue

        via = [k.split("|", 1)[1] for k, g in tried.items() if panel_works(g)]
        err = (r.get("tls") or {}).get("err") or ""

        if via:
            groups.setdefault("panelname", []).append(name)
            via_of[name] = via
        elif any(w in err for w in ("InternalError", "unexpected", "badly formatted")):
            groups.setdefault("panelnotserved", []).append(name)
        else:
            groups.setdefault("panelblock", []).append(name)

    if "paneldns" in groups:
        out.append(_f("paneldns", "warn", f"{who}: the DNS of this network cannot resolve, or answers a false address for, {', '.join(groups['paneldns'])}.", [n.split(" ")[0] for n in groups["paneldns"]], "Give the customers the name through another DNS, or an address."))

    if "panelname" in groups:
        via = sorted({v for n in groups["panelname"] for v in via_of[n]})
        out.append(_f("panelname", "bad", f"{who}: {', '.join(groups['panelname'])} do not open by their normal address on this network but open through {', '.join(via[:5])} - the address the name gives (or the route to it) is the problem.", groups["panelname"],
                      f"Point {'these names' if len(groups['panelname']) > 1 else 'this name'} at an address that works here ({', '.join(via[:3])}), or tunnel through an Iran server."))

    if "panelblock" in groups:
        out.append(_f("panelblock", "bad", f"{who}: {', '.join(groups['panelblock'])} do not open through any panel address - the name itself is filtered on this network, or every address is.", groups["panelblock"],
                      "Give the customers another name for the subscription (see which names open)."))

    if "panelnotserved" in groups:
        out.append(_f("panelnotserved", "warn", f"{who}: {', '.join(groups['panelnotserved'])}: the server answers but has no site for this name (a TLS internal error from every address) - this is not a filter.", groups["panelnotserved"],
                      "Add the name to the panel's web server, or do not hand it out."))

    return out


def judge(cur: dict, prev: list[dict] | tuple = ()) -> list[dict]:
    """Pure. cur: the compact run; prev: the compact runs of the SAME network before it (newest first). -> the findings: {key, kind, level, text, cfgs, hint}, most serious first."""
    who = f"{cur['operator']} ({cur['city']})"
    rows = {c["label"]: c for c in cur["cfg"]}
    states = {lab: row_state(c) for lab, c in rows.items()}
    bad = [lab for lab, s in states.items() if s in ("closed", "stall", "skipped")]
    level = "bad" if rows and len(bad) * 2 >= len(rows) else "warn"
    out: list[dict] = []
    covered: set[str] = set()           # the configs a direct-address finding already speaks for
    ipblocked: set[str] = set()

    for d in (cur.get("direct") or {}).values():                    # the direct configs: is the IP, a port or the name filtered on this network?
        code, words = direct_verdict(d)

        if code != "ok":
            kind, lvl, hint = DIRECT_KINDS[code]
            covered |= set(d["labels"])
            ipblocked |= set(d["labels"]) if kind == "ipblock" else set()
            out.append(_f(kind, lvl, f"{who}: {', '.join(d['labels'][:6])} - {words}.", d["labels"], hint))

    blocked = sorted({lab for n in cur["net"] if n["tcp_n"] and n["tcp_ok"] == 0 for lab in n["labels"]} - covered)

    if blocked:
        out.append(_f("blocked", level, f"{who}: {len(blocked)} address(es) do not answer at all ({', '.join(blocked[:8])}) - the network drops the connection or the address is cut.", blocked,
                      "Check these addresses from another Iran network; if only this one fails the address (or its CDN range) is blocked there."))

    closed = [lab for lab, s in states.items() if s == "closed"]

    if closed:
        out.append(_f("closed", level, f"{who}: {', '.join(closed[:8])} connect but open nothing, not even Google - the way in is cut after the connection.", closed,
                      "The protocol or SNI is filtered there; a different type to the same node may pass."))

    stall = [lab for lab, s in states.items() if s == "stall"]

    if stall:
        det = []

        for lab in stall[:3]:
            c = rows[lab]
            b = c.get("bulk")
            det.append(f"{lab} " + (f"stopped after {_kb(b['stall_at'])} of a long download" if b and b.get("stalled") and b.get("stall_at", -1) >= 0 else f"carried {_kb(c['bytes3'])} of a {_kb(c.get('want', FULL3))} download"))

        out.append(_f("stall", level, f"{who}: {len(stall)} config(s) stall ({'; '.join(det)}).", stall, "The flow is throttled after a few tens of KB: a different way into the same node, or a different address, is the cure."))

    partial = [lab for lab, s in states.items() if s == "partial"]

    if partial:
        miss = sorted({s for lab in partial for s, ok in rows[lab]["sites"].items() if not ok})
        out.append(_f("partial", "warn", f"{who}: some sites do not open through {', '.join(partial[:8])} ({', '.join(miss)}).", partial, "Those sites are cut on the way or on the exit; compare the exit locations of the configs."))

    slow = [lab for lab, s in states.items() if s == "slow"]

    if slow:
        out.append(_f("slow", "warn", f"{who}: {', '.join(slow[:8])} are slow (under {SLOW_BULK:g} Mb/s on the long download or {SLOW_QUICK:g} on the short one).", slow))

    lag = [lab for lab, c in rows.items() if not c.get("skipped") and c.get("lat") and ((c["lat"]["med"] or 0) > LAG_MED or (c["lat"]["p95"] or 0) > LAG_P95 or (c["lat"]["n"] and c["lat"]["lost"] >= max(2, 0.15 * c["lat"]["n"])))]

    if lag:
        det = [f"{lab} (median {rows[lab]['lat']['med'] or 0:.0f} ms, slowest {rows[lab]['lat']['p95'] or 0:.0f} ms, {rows[lab]['lat']['lost']} of {rows[lab]['lat']['n']} lost)" for lab in lag[:4]]
        out.append(_f("lag", "warn", f"{who}: every tap waits too long through {'; '.join(det)}" + (f" and {len(lag) - 4} more" if len(lag) > 4 else "") + " - Instagram and Telegram would feel laggy.", lag,
                      "Compare with the configs of this network that answer fast; the slow ones sit on a long or lossy path."))

    video = [(lab, c["video"]["best"]) for lab, c in rows.items() if c.get("video") and c["video"]["best"] < VIDEO_MIN]

    if video:
        out.append(_f("video", "warn", f"{who}: video does not play smoothly at 1080p through {', '.join(f'{lab} (only {tier_name(b)})' for lab, b in video[:6])}.", [lab for lab, _ in video],
                      "Offer the customers of this network a config that carries 1080p without stopping (see the matrix)."))

    pics = [lab for lab, c in rows.items() if c.get("par") and (c["par"]["fail"] > 0 or (c["par"]["mbps"] or 0) < PICTURES_MIN)]

    if pics:
        out.append(_f("images", "warn", f"{who}: six pictures at once load slowly or fail through {', '.join(pics[:8])} - a feed or stories would lag.", pics))

    for n in cur["net"]:
        d = n.get("dns")

        if d and ((not d["sys_ok"] and d["cf_ok"]) or (d["sys_ok"] and _bogus(d["sys_ip"]))):
            host = n["key"].rsplit(":", 1)[0]
            why = "cannot resolve it (1.1.1.1 can)" if not d["sys_ok"] else f"answers {d['sys_ip']}, an address no server has - the name is tampered with"
            out.append(_f("dns", "warn", f"{who}: the DNS of this network {why}: {host} ({', '.join(n['labels'][:4])}).", n["labels"], "Change the name of these configs, or point them at an address; clients on this network may also use their own DNS."))

        if n["tcp_n"] >= 6 and 0 < n["tcp_ok"] < n["tcp_n"] * 0.8:
            out.append(_f("unstable", "warn", f"{who}: the connection to {n['key']} loses {n['tcp_n'] - n['tcp_ok']} of {n['tcp_n']} tries ({', '.join(n['labels'][:4])}).", n["labels"]))
        elif n["tcp_ok"] >= 5 and n.get("tcp_sd") and n.get("tcp_med") and n["tcp_sd"] > max(80.0, 0.7 * n["tcp_med"]):
            out.append(_f("unstable", "warn", f"{who}: the connect time to {n['key']} jumps (median {n['tcp_med']:.0f} ms, spread {n['tcp_sd']:.0f} ms) ({', '.join(n['labels'][:4])}).", n["labels"]))

    mtus = [v for v in cur["mtu"].values() if isinstance(v, int) and v]

    if mtus and min(mtus) < 1400 and stall:
        out.append(_f("mtu", "warn", f"{who}: the path MTU is {min(mtus)} (a normal line is 1480-1500) and configs stall: big packets may be lost.", [], "Lower the MTU of the tunnels that carry GRE / VXLAN to about 1380 and test again."))

    for base, node in BASES.items():                       # the comparison: the same node by another way in
        if states.get(base) not in ("closed", "stall", "skipped"):
            continue

        labs = [lab for lab in rows if lab.startswith(f"LAB-{node}-")]
        good = [lab for lab in labs if states[lab] in ("ok", "partial", "slow")]

        if good:
            out.append(_f("protocol", level, f"{who}: {base} (VLESS TLS 8680 straight to {node}) fails but {', '.join(good)} on the same node works - the type of the way in is the problem, not the node.", [base] + good,
                          f"Give the customers a config of the working type to {node} (it is already on the node)."))
        elif labs and base not in ipblocked and all(states[lab] in ("closed", "stall", "skipped") for lab in labs):
            out.append(_f("path", level, f"{who}: every way into {node} fails ({base}, {', '.join(labs)}) - the node's address is throttled from this network.", [base] + labs,
                          f"Another address for {node} (a new IP) or an entry inside Iran is the cure; no type change helps."))

    flaky = []

    for lab in rows:
        seq = [states[lab] == "ok"] + [row_state(c) == "ok" for r in list(prev)[:3] for c in r.get("cfg", []) if c["label"] == lab]

        if sum(1 for a, b in itertools.pairwise(seq) if a != b) >= 2:                  # up, down, up (or down, up, down): one change is a new failure or a recovery, not a flutter
            flaky.append(lab)

    if flaky:
        out.append(_f("flaky", "warn", f"{who}: {', '.join(flaky[:8])} go up and down from run to run (the last {min(len(list(prev)), 3) + 1} runs of this network).", flaky, "Run the kit again at another hour; if it repeats the line is unstable there."))

    out.extend(_panel_findings(cur, who))
    order = {"bad": 0, "warn": 1}
    out.sort(key=lambda f: (order.get(f["level"], 2), f["kind"] != "protocol" and f["kind"] != "path"))
    return out


# ------------------------------------------------------------------ keeping, telling
def _slim(f: dict) -> dict:
    return {k: f[k] for k in ("kind", "level", "text", "cfgs", "hint")}


def ingest(payload, sender: str, now: int | None = None) -> dict:
    """The result of one run: keep the compact copy, judge it, tell what is new (an event once in 12 hours for each finding, one when a network is fine again). ValueError (not a kit result), TooMany."""
    run = compact(payload)
    now = int(now or time.time())

    with session() as db:
        if (db.scalar(select(func.count(FieldRun.id)).where(FieldRun.sender == sender, FieldRun.ts >= now - 3600)) or 0) >= PER_HOUR:
            raise TooMany(f"{PER_HOUR} runs in an hour is the most one token may send")

        same = [FieldRun.operator == run["operator"], FieldRun.connection == run["connection"], FieldRun.city == run["city"]]
        before = list(db.scalars(select(FieldRun).where(*same, FieldRun.ts >= now - HISTORY).order_by(FieldRun.ts.desc()).limit(3)))
        findings = judge(run, [r.data for r in before])
        last: dict[str, int] = {}

        for r in before:                                                    # newest first: the newest word on a finding wins (when it was last told)
            for f in r.findings or []:
                last.setdefault(f["key"], int(f.get("told") or r.ts))

        label = f"{run['operator']} {run['city']}"
        stored = []

        for f in findings:
            t0 = last.get(f["key"])
            fresh = t0 is None or now - t0 >= TELL_EVERY                    # told when it is new, and again every 12 hours while it goes on

            if fresh:
                emit(db, f["level"], "configs", "Field test - " + f["text"] + (" " + f["hint"] if f["hint"] else ""), label)

            stored.append(_slim(f) | {"key": f["key"], "told": now if fresh else t0})

        row = FieldRun(ts=now, sender=sender[:64], operator=run["operator"], connection=run["connection"], city=run["city"], ip=run["ip"], mode=run["mode"], data=run, findings=stored)
        db.add(row)
        db.flush()
        rid = row.id

        if not findings and before and before[0].findings:
            emit(db, "ok", "configs", f"Field test - {run['operator']} ({run['city']}): every config passes again.", label)

        old = db.scalars(select(FieldRun.id).order_by(FieldRun.ts.desc()).offset(KEEP_RUNS)).all()
        db.execute(delete(FieldRun).where((FieldRun.ts < now - KEEP_DAYS * 86400) | FieldRun.id.in_(old)))

    states = [row_state(c) for c in run["cfg"]]
    return {"id": rid, "configs": len(states), "configs_ok": states.count("ok"), "findings": [_slim(f) for f in findings]}          # (not `ok`: the route answers ok: true, and a number here would take its place)


def _latest(db, now: int, hours: int = 48) -> list[FieldRun]:
    """The newest run of every network (operator, connection, city) in the last hours."""
    seen, out = set(), []

    for r in db.scalars(select(FieldRun).where(FieldRun.ts >= now - hours * 3600).order_by(FieldRun.ts.desc()).limit(300)):
        k = (r.operator, r.connection, r.city)

        if k not in seen:
            seen.add(k)
            out.append(r)

    return out


def summary(now: int | None = None) -> dict:
    """The newest run of every network and the matrix config x network (ok / partial / slow / stall / closed / skipped)."""
    now = int(now or time.time())

    with session() as db:
        nets = []
        matrix: dict[str, dict] = {}

        for r in _latest(db, now, 24 * 14):
            states = {c["label"]: row_state(c) for c in (r.data or {}).get("cfg", [])}
            key = f"{r.operator}|{r.connection}|{r.city}"

            for lab, s in states.items():
                matrix.setdefault(lab, {})[key] = s

            direct = [{"ip": ip, "labels": d.get("labels", []), "code": v[0], "words": v[1]} for ip, d in ((r.data or {}).get("direct") or {}).items() for v in [direct_verdict(d)]]
            nets.append({"id": r.id, "ts": r.ts, "age_s": now - r.ts, "operator": r.operator, "connection": r.connection, "city": r.city, "ip": r.ip, "mode": r.mode, "key": key,
                         "configs": len(states), "ok": list(states.values()).count("ok"), "direct": direct, "panel": panel_view(r.data or {}), "findings": r.findings or []})

    return {"networks": nets, "matrix": matrix}


def runs(limit: int = 30) -> list[dict]:
    with session() as db:
        return [{"id": r.id, "ts": r.ts, "sender": r.sender, "operator": r.operator, "connection": r.connection, "city": r.city, "ip": r.ip, "mode": r.mode, "findings": len(r.findings or [])}
                for r in db.scalars(select(FieldRun).order_by(FieldRun.ts.desc()).limit(limit))]


def run_by_id(rid: int) -> dict | None:
    with session() as db:
        r = db.get(FieldRun, rid)
        return None if r is None else {"id": r.id, "ts": r.ts, "sender": r.sender, "operator": r.operator, "connection": r.connection, "city": r.city, "data": r.data, "findings": r.findings}


def story_for(labels: list[str], now: int | None = None) -> list[str]:
    """Lines for the Doctor's look at a suffering tunnel: what the newest field test of every network says about these configs (at most 4 lines, nothing when no kit has run lately). Never raises."""
    try:
        now = int(now or time.time())
        want = set(labels)
        lines: list[str] = []

        if not want:
            return lines

        with session() as db:
            for r in _latest(db, now):
                for f in r.findings or []:
                    if want & set(f.get("cfgs") or []):
                        lines.append(f"Field test {max(1, (now - r.ts) // 60)} min ago - {f['text']}" + (f" {f['hint']}" if f.get("hint") else ""))

        return lines[:4]
    except (SQLAlchemyError, KeyError, TypeError, ValueError, AttributeError) as e:          # (a look at the users must never fail because of this)
        log.warning("field story failed: %s", str(e)[:120])
        return []

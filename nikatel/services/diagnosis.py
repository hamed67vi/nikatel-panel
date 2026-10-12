"""Reading a problem: is it the panel, a service on one of your servers, or something outside (the network, a provider, a remote service)?

Every sentence the panel writes about a failure - a log line, the story of a repair, an event - is classified here, so the operator is told in plain words
WHERE the problem is and what to do, not only that there is one. Pure: no database, no network (tests/test_diagnosis.py).

Origins:
  outside  the panel works; something it reaches does not answer (a server that is off, a cut route, a provider, a remote API, the probe's test path)
  server   a service on one of your servers stopped or is blocked (tunnel units, the control port, GRE, a full disk) - the Tunnel Doctor repairs these
  panel    the panel itself (a bug, the database, its own limits, read-only mode, being busy) - the only kind that is ours to fix in the code
  unknown  no rule matched: the full text is shown, nothing is guessed
"""
from __future__ import annotations

import re

ORIGINS = ("outside", "server", "panel", "unknown")
LABELS = {"outside": "Outside the panel", "server": "A service on your server", "panel": "The panel itself", "unknown": "Not determined"}

# (origin, kind, pattern, what it means / what to do) - the first match wins, so the specific rules come before the general ones
RULES: list[tuple[str, str, str, str]] = [
    ("panel", "bug", r"internal error|traceback|keyerror|typeerror|attributeerror|nameerror|assertionerror", "The panel hit a bug. The request id on the answer finds the line in the log; this is the one kind that is a fault of the code."),
    ("panel", "database", r"database is locked|operationalerror|integrityerror|sqlite|no such table|no such column", "The panel's own database was busy or its schema is wrong. A restart clears a lock; a schema error is a bug."),
    ("panel", "read_only", r"panel is read-only|read-only mode|nikatel_read_only|needs read-only off", "Nothing is wrong: the panel is set to read-only and refuses to change servers. Turn it off in the service settings when repairs should run."),
    ("panel", "busy", r"another (tunnel test|smart repair|repair|build|update|check) (is )?(still )?(already )?running|already running|is already being|a smart repair of this tunnel", "Nothing is wrong with the server: the panel was already busy with another job of the same kind and did not start a second."),
    ("panel", "limit", r"already repaired \d+ times in the last hour|too many (wrong attempts|requests)|rate limit", "The panel's own safety limit stopped it on purpose (repeated repairs, or too many requests). It is protection, not a failure."),
    ("panel", "slow", r"timed out waiting|^timed out$|still running - see", "The panel stopped waiting for its own job. The job may still finish; look at its story."),
    ("outside", "storm", r"mass disruption|several servers.*(offline|down)|many tunnels down", "Several servers or many tunnels are down at once: that is the network or a provider, not one tunnel. The repair waits instead of deleting anything."),
    ("outside", "unreachable", r"ssh connection lost|connection timed out|connect to host .* port|no route to host|connection refused|connection reset|kex_exchange|network is unreachable|does not answer over ssh|server does not answer|timeout after \d+s|ssh does not answer|banner exchange", "The server or the network between the panel and it does not answer. The panel is fine; check that the server is on and its provider's network is up."),
    ("outside", "route", r"route between them is cut|cut or filtered|gre.* (is )?filtered|gre packets do not|blocked in front|loses packets|packets? (are )?(sent again|lost)|% loss|lossy|route is poor|provider's network|oversold|\bsteal\b|slow line|filtered", "The path between two servers is bad or filtered. Restarting does not help; another route, another tunnel type or another Iran server does."),
    ("outside", "probe_path", r"curl: \(\d+\)|ssl connection timeout|ssl_error_syscall|operation timed out after|the probe gets no answer|the probe does not answer|probe's (full )?proxy test", "The probe's test through this config failed on the way (from the data centre, or at the config's own server). The panel only reports what the probe saw."),
    ("outside", "remote_service", r"http (4|5)\d\d|bad gateway|gateway time-?out|cloudflare|arvancloud|\b52\d\b|upstream|refused the token|answered (4|5)\d\d", "A service outside the panel (a DNS provider, a Marzban / PasarGuard panel, GitHub) answered with an error or did not answer."),
    ("server", "service", r"units? .*(not running|failed|inactive|dead)|services? .*(are )?not (running|active)|not listening|port \d+ .*(closed|not open|cannot be reached)|control port|stopped answering|restart of .* failed", "A tunnel service on one of your servers stopped or its port is closed. The Tunnel Doctor restarts it; if it keeps stopping, read the unit's log on that server."),
    ("server", "interface", r"gre (interface|link) .*(down|missing|does not)|interface .* (down|missing)|carrier", "The GRE interface of a tunnel is down or missing on a server; a relink or rebuild recreates it."),
    ("server", "resources", r"out of memory|disk .*(full|\d{2,3} ?%)|no space left|cpu .*(9\d|100) ?%|swaps", "A server is short of memory, disk or CPU, so everything on it is slow."),
]
_COMPILED = [(o, k, re.compile(p, re.IGNORECASE), h) for o, k, p, h in RULES]
_HINT_UNKNOWN = "No rule recognises this text. Read the full story and the log line; if it keeps happening, report it with the request id."


def classify(text: str) -> dict:
    """{"origin", "kind", "label", "hint"} for one sentence or a few lines joined. Pure."""
    t = str(text or "")

    for origin, kind, rx, hint in _COMPILED:
        if rx.search(t):
            return {"origin": origin, "kind": kind, "label": LABELS[origin], "hint": hint}

    return {"origin": "unknown", "kind": "unknown", "label": LABELS["unknown"], "hint": _HINT_UNKNOWN}


_SKIP = ("starting", "looking at both servers", "queued")
_TRIED = re.compile(r"^(restart|rebuild|new link|best type|plan:|kernel tuning|rps turned|testing|checked again|restarted|iran:|foreign:|after:|ai:|a new gre link|removing|move|switch|the new link|before the link)", re.IGNORECASE)


def _clean(line: str, ref: str = "") -> str:
    t = re.sub(r"^(found:\s*)", "", str(line).strip(), flags=re.IGNORECASE)

    if ref and t.lower().startswith(ref.lower() + ":"):
        t = t[len(ref) + 1:].strip()

    return t


def summarize(story: list[str], ok: bool | None, action: str = "", ref: str = "") -> dict:
    """A repair story in four parts - the problem, what was tried, the result, and where the problem lies - so the History reads in one glance. Pure."""
    lines = [str(x).strip() for x in (story or []) if str(x).strip()]
    body = [x for x in lines if x.lower().rstrip("….") not in _SKIP]
    problem = re.split(r"\s+Where:\s", _clean(body[0], ref) if body else "")[0].strip()           # the first sentence: the rest ("Where: ...") is the detail of the full story
    tried = [re.sub(r"\s+", " ", x)[:150].rsplit(" ", 1)[0] + "…" if len(x) > 150 else re.sub(r"\s+", " ", x) for x in body[1:] if _TRIED.match(_clean(x, ref))][:5]
    result = body[-1] if body else ""
    done = ok is not None
    cls = classify(" ".join([problem] + body[-3:]) if body else "")

    if ok is True and cls["origin"] == "unknown":
        cls = {"origin": "unknown", "kind": "solved", "label": "", "hint": "Solved: nothing more to do."}

    return {"problem": problem[:240], "tried": tried, "result": result[:240], "done": done, "ok": ok, "origin": cls["origin"], "kind": cls["kind"], "label": cls["label"], "hint": cls["hint"]}


_NUM = re.compile(r"\d+(\.\d+)?")
_ADDR = re.compile(r"\b\d{1,3}(\.\d{1,3}){3}\b")


def shape(text: str) -> str:
    """The same sentence without the numbers and addresses that change from one occurrence to the next, so occurrences group. Pure."""
    return _NUM.sub("#", _ADDR.sub("<ip>", str(text or "").strip().lower()))[:140]


def group_findings(lines: list[dict]) -> dict:
    """lines: [{"level","domain","text","ts"}] -> {"by_origin": {...}, "groups": [{"shape","count","origin","kind","hint","sample","domain","last"}]} for WARNING and ERROR lines. Pure."""
    groups: dict[str, dict] = {}
    by_origin = {o: 0 for o in ORIGINS}

    for ln in lines:
        if ln.get("level") not in ("WARNING", "ERROR"):
            continue

        text = ln.get("text", "")
        c = classify(text)
        by_origin[c["origin"]] += 1
        g = groups.setdefault(shape(text), {"shape": shape(text), "count": 0, "origin": c["origin"], "kind": c["kind"], "hint": c["hint"], "sample": text[:240], "domain": ln.get("domain", ""), "last": ln.get("ts", ""), "level": ln.get("level")})
        g["count"] += 1
        g["last"] = max(g["last"], ln.get("ts", ""))

        if ln.get("level") == "ERROR":
            g["level"] = "ERROR"

    ordered = sorted(groups.values(), key=lambda g: ({"panel": 0, "server": 1, "outside": 2, "unknown": 3}[g["origin"]], -g["count"]))
    return {"by_origin": by_origin, "groups": ordered[:40], "total": sum(by_origin.values())}

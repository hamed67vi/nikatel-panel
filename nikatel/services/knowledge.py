"""What the Doctor KNOWS about every pair of servers: which tunnel types work between an Iran server and a foreign server, and how well - kept, so that when something breaks it already
knows what to build instead of testing a fixed list in the middle of the outage.
(The owner, 2026-10-16: "the Doctor must always be up to date: any tunnel added, on any disruption it knows by itself what tunnel to build in any situation between servers; it runs its tests
in all transports; the first priority is uptime and a high speed for every user".)

  what is kept         per pair (an Iran server and a foreign server), per tunnel kind (the 24 kinds of the tunnel test): the last test - did it work, how well, why not, when - and the last
                       FULL measurement (score, latency, jitter, loss, the download with eight connections and with one, the upload). Settings key `knowledge`.
  where it comes from  every pair test the panel runs (the Tunnel test page, the Doctor's own tests while it repairs, the Create flow), the quality test of every live tunnel with a test door,
                       and the NIGHT SURVEY: one pair at a time, every kind that is due, on the pairs that carry a tunnel.
  how it is used       on a disruption the Doctor tries the kinds known to work between THIS pair first, best first (what a customer downloads counts most), leaves out those that failed here
                       lately and stops at the first that works (`plan_for`); a pair nobody tested yet gets the standard order (`PRIOR`: what the real tests said).
  the night survey     `run` (job, every ten minutes): when it is on, the local time is inside the night window, no repair or test runs, no server is busy and the tunnels of the pair carry
                       little, it starts the survey of the pair with the least knowledge; it stops when the window closes or a repair needs the place for its own tests (`yield_slot`).

The pure helpers (`merge`, `verdict`, `ranked`, `blocked`, `poor`, `due_kinds`, `candidates`, `in_window`) carry the decisions and are tested."""
from __future__ import annotations

import threading
import time

from sqlalchemy import select

from .. import jobs
from ..config import get_settings
from ..db import session
from ..log import get_logger
from ..models import Server, ServerSample, Tunnel, is_temp
from . import linkmodels
from . import settings as st
from .events import emit
from .userscore import has_speed, user_score

log = get_logger("knowledge")
FULL_FRESH = 30 * 86400         # a full measurement older than this is only a hint (the numbers of the day, not of the month)
REFRESH = 7 * 86400             # a kind that worked is tested again after this
FAIL_FRESH = 14 * 86400         # a kind that did not work is left alone this long (and the Doctor does not try it in the middle of an outage)
TOOLS_RETRY = 86400             # a kind that was NOT TESTED because a server lacks wireguard-tools is asked again after this (at once when the owner installs the tools: `forget_tool_skips`)
QUIET_MBPS = 150.0              # the tunnels of the two servers carry less than this together (the night survey only asks a few pings of a temporary tunnel, no speed test: a survey hardly touches the users)
BUSY_CPU = 60.0                 # a server this busy is not surveyed
LIMIT = 5                       # the kinds the Doctor tries live at most
SURVEYS = ("survey", "survey-manual", "survey-cold")      # survey-cold: the first survey of a pair nobody has tested, by day too (see COLD_QUIET_MBPS)
COLD_QUIET_MBPS = 30.0          # a pair that has never been surveyed is surveyed outside the night window as well, when the tunnels of its servers carry less than this: without what the Doctor knows about a pair, a cut on it
                                # is repaired by trying the tunnel types one by one - the longest part of a repair (the owner, 2026-10-17: "bring the delay down to the minimum")
GRE_KINDS = ("tcpmux", "tcp", "wsmux", "wssmux")          # the Backhaul kinds: they need GRE (protocol 47) to pass between the two servers
# What the real tests said (docs/tunnels.md, 2026-10-16): the side that DIALS decides what a customer downloads, so the kinds where the Iran server dials come first; the kinds where the foreign
# server dials (reverse - they are the way when the Iran server cannot dial out) follow; the odd ones are last. It is the order for a pair nobody tested yet, and the tie-break.
PRIOR = ["tcpmux", "bpd:pck", "chisel:fwd", "paqet", "bpd:udp", "chisel:fwdtls", "bp:wssmux", "bp:tcpmux", "chisel:tls", "bp:stealth", "bp:wss", "bp:tcp",
         "wssmux", "tcp", "wsmux", "bp:pck", "chisel", "bp:ws", "bp:wsmux", "bp:kcp", "bpd:quic", "bp:quic", "bpd:xdi", "bp:xdi",
         "ipip:tcpmux", "ipipfou:tcpmux", "ipsec:tcpmux", "ipipgue:tcpmux", "ipsecnat:tcpmux", "wg:tcpmux", "wg:tcp", "fou:tcpmux", "gue:tcpmux"]      # (1.66.0 / 1.69.0: Backhaul over the other eight link models - last in the standard order, and in the order they measured on the test route, 2026-10-17: IPIP 1307 / 1290 Mb/s, IPIP in FOU 1228 / 736, IPsec ESP 1055 / 778, the rest 2 - 43 Mb/s towards the foreign server; what the survey finds to connect on a pair is tried first by `candidates`)
LINK_KINDS = ("ipip:tcpmux", "ipipfou:tcpmux", "ipsec:tcpmux", "ipipgue:tcpmux", "ipsecnat:tcpmux", "wg:tcpmux", "fou:tcpmux", "gue:tcpmux", "wg:tcp")      # Backhaul over a link that is not native GRE (services/linkmodels.py): they need no protocol 47 (the order is the measured one, see PRIOR: the link probe leaves out the ones that do not pass, the best of the rest goes first)
UDP_LINK_KINDS = tuple(k for k in LINK_KINDS if linkmodels.is_udp(linkmodels.split_kind(k)[0]))        # ... the ones that need a UDP port to pass between the two servers (the others need IP protocol 4 or 50)
# What the night survey tests on every pair that carries a tunnel (about twelve minutes a pair): the kinds the Doctor reaches for when a route is cut, the robust ones first. The other kinds are tested when
# the owner asks (Doctor page, Knowledge) or by a repair that needs them.
READY = ["bp:wssmux", "chisel:tls", "bp:tcpmux", "bpd:pck", "chisel:fwd", "paqet", "bp:wss", "chisel:fwdtls", "bp:tcp", "bpd:udp", "wg:tcpmux", "fou:tcpmux",
         "gue:tcpmux", "ipip:tcpmux", "ipipfou:tcpmux", "ipipgue:tcpmux", "ipsec:tcpmux", "ipsecnat:tcpmux"]      # (a link of the other models too: where protocol 47 is filtered they are the first thing to try, and which of them passes differs from pair to pair - a survey never installs a package, wireguard-tools included)
# The night survey goes in THREE tiers, one survey of a pair at a time (the owner, 2026-10-24: "why do you not test the other methods? I want you to work on every scenario - above all the heavier limits on the Iran servers"):
#   1. READY - what a repair reaches for (about twelve minutes a pair);
#   2. MORE  - the rest of the standard list (PRIOR), once the first tier of the pair is known;
#   3. the VARIANTS of a link that passed - Backhaul over IPIP / FOU / GUE / IPsec / WireGuard with the transports `tcp`, `wsmux`, `wssmux` too (a link that did not pass is not tried again with other transports: they would fail for the same reason).
MORE = [k for k in PRIOR if k not in READY]
VARIANT_TRANSPORTS = ("tcp", "wsmux", "wssmux")
# Backhaul over a WireGuard link CARRIED inside Chisel (one TCP port; services/linkmodels.py): not in PRIOR / READY / CUT_LINKS - the Doctor does not walk them in its standard order and the night survey does not build them. They are the
# LAST RESORT of a cut (`autopilot._carrier_last_resort`): after every kind failed, when the link probe SEES one pass. The owner can test them in Compare (the grid has them) and build them from Create tunnel.
CARRIER_KINDS = tuple(f"{k}:tcpmux" for k in linkmodels.CARRIER_LINKS)
NUMS = ("score", "latency_ms", "jitter_ms", "loss_pct", "down_mbps", "up_mbps", "down1_mbps", "up1_mbps")
HIST = 12                   # the tests of one kind that are kept (the newest): what a type did over time, not only what it did once
STEADY_MIN_TESTS = 3        # fewer tests than this: the stability of a type is NOT KNOWN (never "steady" on one good day)
STEADY_OK = 75              # a type whose stability is under this (percent) is unsteady: it is put behind the steady ones
STEADY_UNKNOWN = 0.85       # ... a type tested too few times counts this much of its user score; a steady one counts all of it, an unsteady one counts 0.7 x its stability
STEADY_CUTS = 3             # a running tunnel that was cut this many times in a day is unsteady, whatever its test said (see bandwidth._upgrade_check)
INCIDENTS = 200             # the cuts that are kept (the newest)
PLAYBOOK_DAYS = 90          # what healed a pair this long ago is still the first thing tried
_LOCK = threading.Lock()


LOST_MARKS = ("ssh connection lost", "is off or does not answer", "does not answer over SSH")


def server_lost(text) -> bool:
    """Pure: does this line (or the last lines of the story of a build) say that the panel lost its SSH connection to a server - it went off, its network dropped? Such a kind was NOT TESTED: the build died before the route
    was judged, so it says nothing about the route (it is remembered as "could not be tried", never as "blocked for two weeks" - the owner's panel, 2026-10-24: 26 types were "blocked" on a pair because its Iran server was off)."""
    lines = [text] if isinstance(text, str) else list(text or [])[-6:]
    return any(m in str(ln) for ln in lines for m in LOST_MARKS)


# ------------------------------------------------------------------ pure helpers (tested)
def pair_key(iran_id: int, foreign_id: int) -> str:
    return f"{int(iran_id)}:{int(foreign_id)}"


def merge(entry: dict | None, r: dict, measure: str, now: int) -> dict:
    """The entry of one kind after one test. `r` is a row of a pair test (kind, ok, score, latency_ms ..., error / verdict); `measure`: "full" (speed included) or "latency"."""
    e = dict(entry or {})
    why = str(r.get("error") or r.get("verdict") or "")[:140]

    if r.get("skipped") or (not r.get("ok") and not r.get("works") and r.get("score") is None and server_lost(why)):          # not tested at all (a server lacks wireguard-tools, or it went off in the middle of the build): that is NOT a failure of the route - it is never "blocked", it is asked again
        e["last"] = {"ok": False, "measured": False, "skipped": str(r.get("skipped") or "server"), "at": now, "measure": measure, "score": None, "error": why}
        return e

    ok, measured = bool(r.get("ok")), r.get("score") is not None or bool(r.get("works"))          # "works": it was built and carried data (a real build checks that) though nobody scored it
    e["last"] = {"ok": ok, "measured": measured, "at": now, "measure": measure, "score": r.get("score"), "error": "" if ok or measured else str(r.get("error") or r.get("verdict") or "")[:140]}

    if measured:
        e["full" if measure == "full" else "lat"] = {**{k: r.get(k) for k in NUMS}, "at": now}

    item = {"at": now, "ok": ok, "m": measure}               # (one line per test: a type that passed once and failed the next time must not look the same as one that passed every time)

    for key, name in (("down", "down_mbps"), ("down1", "down1_mbps"), ("lat", "latency_ms"), ("loss", "loss_pct")):
        if measured and r.get(name) is not None:
            item[key] = r.get(name)

    e["hist"] = (list(e.get("hist") or []) + [item])[-HIST:]
    return e


def stability(e: dict | None, now: int) -> dict | None:
    """Pure: how steady one kind has been, from its kept tests of the last FULL_FRESH days: {"n": tests, "passed": how many passed, "swing": the lowest download / the highest (1 = always the same), "pct": 0-100}.
    None when fewer than STEADY_MIN_TESTS tests are kept: one good day proves nothing (the owner, 2026-10-21: "a type that works at first and later shows low bandwidth or problems must not be chosen").
    pct = share of the tests that passed x (0.4 + 0.6 x swing), x 0.8 when any test lost more than 2 % of the packets."""
    h = [x for x in ((e or {}).get("hist") or []) if now - int(x.get("at") or 0) <= FULL_FRESH]

    if len(h) < STEADY_MIN_TESTS:
        return None

    passed = sum(1 for x in h if x.get("ok"))
    downs = [float(x["down"]) for x in h if x.get("down") is not None]
    swing = max(0.0, min(1.0, min(downs) / max(downs) if max(downs) > 0 else 0.0)) if len(downs) >= 2 else 1.0          # (a reading below zero must not push the stability below 0)
    pct = 100.0 * (passed / len(h)) * (0.4 + 0.6 * swing)

    if any(float(x.get("loss") or 0) > 2.0 for x in h):
        pct *= 0.8

    return {"n": len(h), "passed": passed, "swing": round(swing, 2), "pct": round(pct)}


def steady_note(r: dict) -> str:
    """Pure: the stability of one ranked row in words (for the reason sentences)."""
    if r.get("steady") is None:
        return f"stability not proven yet ({r.get('tests') or 0} test{'' if r.get('tests') == 1 else 's'} kept, {STEADY_MIN_TESTS} needed)"

    word = "steady" if r["steady"] >= STEADY_OK else "UNSTEADY"
    return f"{word}: {r['passed']} of {r['tests']} tests passed, stability {r['steady']} %"


def verdict(e: dict | None) -> str:
    """"good" (the last test passed), "poor" (it carried data but scored under the line), "failed" (it did not work), "untested" (it could not be tried: a server lacks wireguard-tools), "" (never tested)."""
    last = (e or {}).get("last")

    if not last:
        return ""

    if last.get("skipped"):
        return "untested"

    if last.get("ok"):
        return "good"

    if not last.get("measured") and server_lost(last.get("error")):          # (what was kept before the Doctor knew this: the build died with the server - it was never a verdict on the route)
        return "untested"

    return "poor" if last.get("measured") else "failed"


def ranked(pair: dict | None, now: int, include_poor: bool = False) -> list[dict]:
    """The kinds that work on the pair, best first: those with a fresh FULL measurement before those known by latency only, each by what the user gets (speed, ping, jitter, loss) times how steady the type was in its
    kept tests (`stability`: a type that failed or fell in some tests goes behind one that passed all of them; one tested too few times counts a little less) - a tie by the standard order.
    `include_poor`: the kinds that carried data but scored under the line count too (for a tunnel that is CUT, any tunnel that connects is better than none)."""
    out = []

    for kind, e in ((pair or {}).get("kinds") or {}).items():
        if verdict(e) not in (("good", "poor") if include_poor else ("good",)):
            continue

        full = e.get("full") or {}
        fresh = full.get("score") is not None and now - int(full.get("at") or 0) < FULL_FRESH
        src = full if fresh else (e.get("lat") or full)
        row = {"kind": kind, "score": src.get("score"), "measure": "full" if fresh else "latency", "at": int(src.get("at") or 0), "down_mbps": src.get("down_mbps"), "down1_mbps": src.get("down1_mbps"),
               "latency_ms": src.get("latency_ms"), "jitter_ms": src.get("jitter_ms"), "loss_pct": src.get("loss_pct")}
        row["user"] = user_score(row) if has_speed(row) and row["latency_ms"] is not None else row["score"]          # (what the USER gets, counted again from the numbers: a score written down by an older rule does not decide)
        st_ = stability(e, now)
        row["steady"] = None if st_ is None else st_["pct"]
        row["tests"] = len([x for x in (e.get("hist") or []) if now - int(x.get("at") or 0) <= FULL_FRESH])
        row["passed"] = None if st_ is None else st_["passed"]
        factor = STEADY_UNKNOWN if st_ is None else (1.0 if st_["pct"] >= STEADY_OK else 0.7 * st_["pct"] / 100.0)
        row["rank"] = float(row["user"] or 0) * factor          # (what the user gets, times how far the type can be trusted to give it again)
        out.append(row)

    return sorted(out, key=lambda r: (r["measure"] != "full", -r["rank"], PRIOR.index(r["kind"]) if r["kind"] in PRIOR else 99))


AUTO_TRIES = 3          # the types "Best of the day" tries one after the other when a build ends badly (the owner, 2026-10-20: a tunnel that carried nothing was called ready)


def _why_good(r: dict, now: int) -> str:
    when = f", measured {max(0, (now - r['at']) // 3600)} h ago" if r.get("at") else ""
    shown = r["user"] if r.get("user") is not None else r["score"]
    users = f" ({float(r['down1_mbps'] if r.get('down1_mbps') is not None else r['down_mbps']):.0f} Mb/s for a user, {float(r['latency_ms']):.0f} ms)" if has_speed(r) and r.get("latency_ms") is not None else ""
    return f"{r['kind']}: the best that connects between these two servers - score {shown if shown is not None else '-'}{users}{when}; {steady_note(r)}"


def _why_weak(r: dict, now: int) -> str:
    when = f", measured {max(0, (now - r['at']) // 3600)} h ago" if r.get("at") else ""
    down = f"{float(r['down_mbps']):g} Mb/s" if r.get("down_mbps") is not None else f"score {r['score'] if r['score'] is not None else '-'}"
    return f"{r['kind']}: no type scored well between these two servers - this is the best of the weak ones ({down}{when})"


def best_kinds(iran_id: int, foreign_id: int, allowed=lambda k: True, now: int | None = None, limit: int = 1) -> tuple[list[tuple[str, str]], str]:
    """([(type, the sentence that says why)] best first, the sentence when the list is empty): the types a NEW tunnel between this pair gets in the mode "Best of the day" (the owner, 2026-10-19: "a tunnel mode that
    puts the best tunnel of the day on it"). First the types that connect on this pair - a fresh full measurement before a latency-only one, each by score - then the weak ones, by what they CARRIED (the owner,
    2026-10-20: the automatic type chose a Paqet that had carried 4.9 Mb/s over types that had carried 66 and 76), what the licence allows. Nothing measured on this pair yet: the Doctor's first choice for a new pair
    (never a WireGuard type: it needs a package that nobody has put on the servers). The build tries them one after the other (`create.start`, `fallback_kinds`)."""
    now = now or int(time.time())
    p = pair(iran_id, foreign_id)
    picks = [(r["kind"], _why_good(r, now)) for r in ranked(p, now) if allowed(r["kind"])][:limit]
    weak = sorted((r for r in ranked(p, now, include_poor=True) if allowed(r["kind"]) and r["kind"] not in {k for k, _ in picks}),
                  key=lambda r: (r.get("down_mbps") is not None, float(r.get("down_mbps") or 0), r.get("score") or 0), reverse=True)
    picks += [(r["kind"], _why_weak(r, now)) for r in weak][: max(0, limit - len(picks))]

    if picks:
        return picks, ""

    kinds, _ = plan_for(iran_id, foreign_id, "", False, False, now=now)
    pool = [k for k in kinds if allowed(k) and linkmodels.split_kind(k)[0] != "wg"]
    # ONE GRE kind at most among the tries: the standard order starts with the four Backhaul-over-GRE kinds, and when GRE is filtered on the pair (seen for real on 2026-10-23: a Trident between an Iran server
    # and the build server tried tcpmux, wssmux, tcp - all GRE - and gave up) every try fails the same way; the tries after the first are the kinds of other families, in their order
    pool = pool[:1] + [k for k in pool[1:] if k not in GRE_KINDS] + [k for k in pool[1:] if k in GRE_KINDS]
    pool = weather_last(pool, iran_id, foreign_id, now)
    first = [(k, f"{k}: nothing is measured between these two servers yet - the Doctor's first choice for a new pair") for k in pool][:limit]
    return first, ("" if first else "no tunnel type may be built here (the licence)")


def weather_last(kinds: list[str], iran_id: int, foreign_id: int, now: int) -> list[str]:
    """The kinds in the same order, but those whose family of carriers is BLOCKED on the route today (services/weather.py) at the end: a pair nobody measured still has today's weather. Without weather: unchanged."""
    from . import weather

    bad = weather.blocked_families(iran_id, foreign_id, now)

    if not bad:
        return list(kinds)

    return [k for k in kinds if weather.family_of_kind(k) not in bad] + [k for k in kinds if weather.family_of_kind(k) in bad]


def note_gre_filtered(iran_id: int, foreign_id: int, why: str, now: int | None = None) -> None:
    """A REAL build of a Backhaul-over-GRE tunnel ended because GRE (protocol 47) does not pass between the two servers: every GRE kind is kept as a failed test of the pair (`blocked` leaves them out for
    FAIL_FRESH), so that the next choice - a standby, a strand, Best of the day, a repair - does not try the other three the same way. Seen for real on 2026-10-23: a Trident's tunnel, then its standby,
    tried tcpmux, wssmux, tcpmux, wssmux on one filtered pair, a minute each, with growing waits between them."""
    now = now or int(time.time())

    for kind in GRE_KINDS:
        record(iran_id, foreign_id, {"kind": kind, "ok": False, "error": str(why)[:140]}, "latency", now=now)


def best_kind(iran_id: int, foreign_id: int, allowed=lambda k: True, now: int | None = None) -> tuple[str, str]:
    """(the type, the sentence that says why) of the first of `best_kinds`; ('', why) when no type may be built."""
    picks, why = best_kinds(iran_id, foreign_id, allowed, now, 1)
    return picks[0] if picks else ("", why)


FASTER_RATIO = 5.0              # a type counts as FASTER for a very slow tunnel when its download on this pair was this many times what the tunnel gives (its test, or what its users carry, whichever is more) ...
FASTER_MIN_MBPS = 20.0          # ... and at least this much
FASTER_FRESH = 14 * 86400       # ... measured within this long ago (a number of last month says little about today's route)


def _ping_ok(r: dict, base: dict | None) -> bool:
    """Pure: does the type `r` answer about as fast as the running one (`base`: its test)? True when one of them has no ping."""
    lat, cur = r.get("latency_ms"), (base or {}).get("latency_ms")
    return lat is None or cur is None or float(lat) <= float(cur) * PING_SLACK + PING_SLACK_MS


def faster_kinds(pair: dict | None, now: int, current: str, baseline: float, allowed=lambda k: True, ratio: float = FASTER_RATIO) -> list[dict]:
    """Pure: the types the Doctor's memory of the pair knows to have given clearly more than a very slow tunnel gives (the owner, 2026-10-19: a tunnel that tested 0.8 Mb/s on a pair where another type had measured 119):
    kinds that connected on the pair (never the running one, never one that failed or only scored poor), whose download is at least FASTER_RATIO times `baseline` (and FASTER_MIN_MBPS), measured less than FASTER_FRESH ago,
    that the licence allows - the fastest first (a tie by score). Empty when the memory knows none: nothing is guessed."""
    need = max(FASTER_MIN_MBPS, ratio * max(float(baseline or 0), 1.0))
    cur = (((pair or {}).get("kinds") or {}).get(current) or {}).get("full") or {}
    rows = [r for r in ranked(pair, now) if r["kind"] != current and r.get("down_mbps") is not None and float(r["down_mbps"]) >= need and now - int(r.get("at") or 0) <= FASTER_FRESH and allowed(r["kind"])
            and _ping_ok(r, cur)]
    return sorted(rows, key=lambda r: (-float(r["down_mbps"]), -(r.get("user") or 0)))


PING_SLACK = 1.3                # a better type may answer this much slower than the running one (times) ...
PING_SLACK_MS = 15.0            # ... plus this many ms: the ping is what the user feels first (the owner, 2026-10-21)
UPGRADE_RATIO = 3.0             # a type counts as a better one for a tunnel that WORKS when the pair test gave it this many times what the pair test gave the running type ...
UPGRADE_MIN_GAIN = 100.0        # ... and at least this many Mb/s more (a few Mb/s of difference is no reason to touch a running tunnel)
UPGRADE_MAX_LOSS = 1.0          # ... and it was stable in its test: at most this % of packets lost


def upgrade_kinds(pair: dict | None, now: int, current: str, allowed=lambda k: True) -> list[dict]:
    """Pure: the types the Doctor's memory of the pair knows to be clearly better than the RUNNING type, for a tunnel that is not slow at all (the owner, 2026-10-20: "it set these types - are they the best? after it
    sets them it must test whether a better and more stable one exists"). Both numbers are FULL pair tests of the same kind of measurement, fresh (FASTER_FRESH): the running type's own, and the other's; the other
    carried at least UPGRADE_RATIO times as much and UPGRADE_MIN_GAIN Mb/s more, lost at most UPGRADE_MAX_LOSS % and scored at least as well; the licence allows it. The fastest first. Empty when the running type
    has no fresh full test on this pair (nothing to compare with: nothing is guessed)."""
    full = (((pair or {}).get("kinds") or {}).get(current) or {}).get("full") or {}

    if full.get("down_mbps") is None or now - int(full.get("at") or 0) > FASTER_FRESH:
        return []

    base = float(full["down_mbps"])
    need = max(base * UPGRADE_RATIO, base + UPGRADE_MIN_GAIN)
    rows = [r for r in ranked(pair, now) if r["kind"] != current and r.get("measure") == "full" and r.get("down_mbps") is not None and float(r["down_mbps"]) >= need
            and now - int(r.get("at") or 0) <= FASTER_FRESH and float(r.get("loss_pct") or 0) <= UPGRADE_MAX_LOSS and allowed(r["kind"]) and _ping_ok(r, full)
            and (r.get("steady") is None or r["steady"] >= STEADY_OK)          # (a type that fell or failed in some of its tests is no upgrade, however fast its best test was)
            and (r.get("user") or 0) >= (user_score(full) if has_speed(full) else (full.get("score") or 0))]
    return sorted(rows, key=lambda r: (r.get("steady") is None, -(r.get("user") or 0), -float(r["down_mbps"])))          # (the ones that passed all their tests first)


STEADIER_KEEP = 0.8             # a steadier type must give the user at least this much of what the running type gave in its test (steadiness is not bought with a slow tunnel)


def steadier_kinds(pair: dict | None, now: int, current: str, allowed=lambda k: True) -> list[dict]:
    """Pure: the types the memory of the pair knows to be STEADY (stability STEADY_OK or more over at least STEADY_MIN_TESTS tests) for a tunnel that keeps being cut: not the running type, a fresh full test, the
    user score at least STEADIER_KEEP of the running type's, a ping about the same (`_ping_ok`), the licence allows it. The steadiest first (a tie: the one that gives the user more). Empty when none is known."""
    cur = (((pair or {}).get("kinds") or {}).get(current) or {}).get("full") or {}
    base = (user_score(cur) if has_speed(cur) else (cur.get("score") or 0)) or 0
    rows = [r for r in ranked(pair, now) if r["kind"] != current and r.get("measure") == "full" and r.get("steady") is not None and r["steady"] >= STEADY_OK and allowed(r["kind"])
            and now - int(r.get("at") or 0) <= FASTER_FRESH and (r.get("user") or 0) >= STEADIER_KEEP * base and _ping_ok(r, cur)]
    return sorted(rows, key=lambda r: (-r["steady"], -(r.get("user") or 0)))


def blocked(pair: dict | None, now: int) -> list[str]:
    """The kinds that did not work on the pair lately (a test that failed less than FAIL_FRESH ago)."""
    return sorted(k for k, e in ((pair or {}).get("kinds") or {}).items() if verdict(e) == "failed" and now - int(e["last"].get("at") or 0) < FAIL_FRESH)


def untested(pair: dict | None) -> list[dict]:
    """The kinds that were NOT tested on the pair because they could not be tried (a server lacks wireguard-tools) - [{"kind", "reason", "at"}]. They are neither good nor blocked: the page says "not tested", and they are
    asked again (a day later, or at once when the owner installs the tools)."""
    return [{"kind": k, "reason": "tools" if (e["last"].get("skipped") == "tools") else str(e["last"].get("skipped") or "server"), "error": e["last"].get("error") or "", "at": int(e["last"].get("at") or 0)}
            for k, e in sorted(((pair or {}).get("kinds") or {}).items()) if verdict(e) == "untested"]


def forget_tool_skips(server_id: int) -> None:
    """The owner installed wireguard-tools on this server: every "not tested: tools missing" of a pair it belongs to is forgotten (the kind and the link), so that the next survey tests them at once."""
    with _LOCK:
        d = _doc()
        changed = False

        for key, p in d["pairs"].items():
            if str(server_id) not in key.split(":"):
                continue

            kinds = {k: e for k, e in (p.get("kinds") or {}).items() if verdict(e) != "untested"}
            links = {k: e for k, e in (p.get("links") or {}).items() if not e.get("skipped")}

            if len(kinds) != len(p.get("kinds") or {}) or len(links) != len(p.get("links") or {}):
                d["pairs"][key] = {**p, "kinds": kinds, "links": links}
                changed = True

        if changed:
            st.put("knowledge", {"pairs": d["pairs"]})


def poor(pair: dict | None, now: int) -> list[str]:
    """The kinds that carried data on the pair but scored under the line, tested less than FULL_FRESH ago."""
    return sorted(k for k, e in ((pair or {}).get("kinds") or {}).items() if verdict(e) == "poor" and now - int(e["last"].get("at") or 0) < FULL_FRESH)


def link_variants(pair: dict | None) -> list[str]:
    """Pure: the third tier of the survey for a pair - the transports `tcp`, `wsmux` and `wssmux` of every non-GRE link whose `tcpmux` kind PASSED on this pair (a good or poor test), in the order of LINK_KINDS, never a kind of the standard list
    and only what the panel can build (`tunnel_test.KINDS`). A link that failed or was never tested gives none."""
    from . import tunnel_test

    valid = {x[0] for x in tunnel_test.KINDS}
    have = (pair or {}).get("kinds") or {}
    out: list[str] = []

    for k in LINK_KINDS:
        link = k.split(":", 1)[0]
        e = have.get(f"{link}:tcpmux")

        if not e or verdict(e) not in ("good", "poor"):
            continue

        for tr in VARIANT_TRANSPORTS:
            v = f"{link}:{tr}"

            if v in valid and v not in PRIOR and v not in out:
                out.append(v)

    return out


def due_kinds(pair: dict | None, kinds: list[str], now: int, refresh: int = REFRESH) -> list[str]:
    """The kinds (in the order given) whose knowledge is missing or old: never tested; tested more than `refresh` ago; one that failed, more than FAIL_FRESH ago."""
    have = (pair or {}).get("kinds") or {}
    out = []

    for k in kinds:
        last = (have.get(k) or {}).get("last")

        if not last or now - int(last.get("at") or 0) >= (FAIL_FRESH if verdict(have[k]) == "failed" else TOOLS_RETRY if verdict(have[k]) == "untested" else refresh):
            out.append(k)

    return out


REVERSE_KINDS = frozenset({"bp:tcpmux", "bp:tcp", "bp:stealth", "bp:wss", "bp:wssmux", "bp:ws", "bp:wsmux", "bp:kcp", "bp:quic", "bp:pck", "bp:xdi", "chisel", "chisel:tls"})      # the FOREIGN server dials the Iran server
FORWARD_KINDS = frozenset({"bpd:udp", "bpd:quic", "bpd:pck", "bpd:xdi", "paqet", "chisel:fwd", "chisel:fwdtls"})                                                                   # the IRAN server dials the foreign server
# For a tunnel that is CUT the first thing is to connect, so the order is by robustness against filtering, not by speed: TLS WebSocket kinds look like HTTPS, the foreign server dialling in works when the
# foreign address is filtered from Iran (only the connections that START in Iran are cut), plain kinds next, UDP / raw / ICMP kinds last.
CUT_REVERSE = ["bp:wssmux", "chisel:tls", "bp:tcpmux", "bp:wss", "bp:tcp", "chisel", "bp:stealth", "bp:wsmux", "bp:ws", "bp:kcp", "bp:pck", "bp:quic", "bp:xdi"]
CUT_FORWARD = ["bpd:pck", "chisel:fwd", "paqet", "chisel:fwdtls", "bpd:udp", "bpd:quic", "bpd:xdi"]
CUT_GRE = ["tcpmux", "wssmux", "tcp", "wsmux"]
CUT_LINKS = list(LINK_KINDS)       # Backhaul over a link that is not native GRE: last in the static order (a pair whose survey found one connecting has it first anyway, see `candidates`)


def cut_order(dial_i: bool | None, dial_f: bool | None, no_gre: bool) -> list[str]:
    """The order in which a CUT tunnel's replacements are tried (see CUT_REVERSE). `dial_i` / `dial_f`: can the Iran / the foreign server open a TCP connection to the other one (None = not known).
    When only the foreign server can dial (the foreign address is filtered from Iran) the reverse kinds come first, when the Iran server can dial the forward kinds (faster) come first."""
    gre = [] if no_gre else CUT_GRE

    if dial_i is False and dial_f is not False:
        return gre + CUT_REVERSE + CUT_FORWARD + CUT_LINKS

    if dial_i:
        return gre + CUT_FORWARD + CUT_REVERSE + CUT_LINKS

    return gre + CUT_REVERSE + CUT_FORWARD + CUT_LINKS


def unreachable(dial_i: bool | None, dial_f: bool | None) -> frozenset:
    """The kinds that cannot connect because of who cannot dial (known only when the test said False)."""
    return (FORWARD_KINDS if dial_i is False else frozenset()) | (REVERSE_KINDS if dial_f is False else frozenset())


def candidates(pair: dict | None, current: str, no_gre: bool, lossy: bool, now: int, limit: int = LIMIT, base: list[str] | None = None, unreachable_kinds: frozenset = frozenset(),
               prefer: list[str] | None = None) -> list[str]:
    """The kinds the Doctor tries live when it must switch a tunnel, in the order it tries them: what is known to connect on this pair (best first), then what nobody tried yet (the order of `base`);
    what the facts of the moment say cannot connect (a server that cannot dial) goes to the end; what failed here lately only when nothing else is left. For a tunnel that is CUT (`lossy` False) the
    kinds that carried data but scored poorly count as known to connect. The tunnel's own kind is left out unless it is only lossy (a fresh build of it may be better)."""
    allowed = [k for k in (base or PRIOR) if not (no_gre and k in GRE_KINDS) and (lossy or k != current)]
    good = [r["kind"] for r in ranked(pair, now, include_poor=not lossy) if r["kind"] in allowed]
    skip = set(blocked(pair, now)) | (set(poor(pair, now)) if lossy else set())
    unknown = [k for k in allowed if k not in good and k not in skip]
    unknown = [k for k in (prefer or []) if k in unknown] + [k for k in unknown if k not in (prefer or [])]            # (what the other pairs of the same server connect with comes before what nobody tried)
    ordered = good + unknown
    ordered = [k for k in ordered if k not in unreachable_kinds] + [k for k in ordered if k in unreachable_kinds]

    return ordered[:limit] or [k for k in allowed if k in skip][:limit]


def recent_failure(pair: dict | None, kinds: tuple | list, now: int, within: int = 1800) -> bool:
    """Did any of these kinds fail on the pair less than `within` seconds ago (a new GRE link was tested a minute ago and did not pass: it is not tested again at once)?"""
    have = (pair or {}).get("kinds") or {}
    return any(verdict(have.get(k)) == "failed" and now - int((have[k].get("last") or {}).get("at") or 0) < within for k in kinds)


def in_window(now: int, tz_minutes: int, from_h: int, to_h: int) -> bool:
    """Is the local hour inside the night window [from_h, to_h)? The window may wrap past midnight; equal hours mean any time."""
    if from_h == to_h:
        return True

    h = ((now + tz_minutes * 60) // 3600) % 24
    return from_h <= h < to_h if from_h < to_h else (h >= from_h or h < to_h)


# ------------------------------------------------------------------ what is kept
def _doc() -> dict:
    d = st.get("knowledge")
    d["pairs"] = dict(d.get("pairs") or {})
    return d


def pair(iran_id: int, foreign_id: int) -> dict:
    return dict(_doc()["pairs"].get(pair_key(iran_id, foreign_id)) or {})


def record(iran_id: int, foreign_id: int, r: dict, measure: str, now: int | None = None) -> None:
    """Keep one result of a pair test (a row of a compare job). Every kind the panel can build is kept (1.69.125: the variants of the third tier of the survey are not in the standard list; they were thrown away here)."""
    from . import tunnel_test

    if r.get("kind") not in PRIOR and r.get("kind") not in {x[0] for x in tunnel_test.KINDS}:
        return

    now = now or int(time.time())

    with _LOCK:
        d = _doc()
        k = pair_key(iran_id, foreign_id)
        p = dict(d["pairs"].get(k) or {})
        kinds = dict(p.get("kinds") or {})
        kinds[r["kind"]] = merge(kinds.get(r["kind"]), r, measure, now)
        d["pairs"][k] = {**p, "at": now, "kinds": kinds}
        st.put("knowledge", {"pairs": d["pairs"]})


def record_link(iran_id: int, foreign_id: int, link: str, r: dict, now: int | None = None) -> None:
    """Keep what the link probe found for one link of one pair (services/linkprobe.py): did it pass, its delay and loss, why not, when. These are answers about the CARRIER (a protocol or a UDP port between the two
    servers), not about an engine: they are cheap to ask again, so unlike a kind that failed they never lock anything out for weeks - the next cut simply asks again."""
    if link not in linkmodels.ALL_LINKS:
        return

    now = now or int(time.time())

    with _LOCK:
        d = _doc()
        k = pair_key(iran_id, foreign_id)
        p = dict(d["pairs"].get(k) or {})
        links = dict(p.get("links") or {})
        if r.get("ok") is None and r.get("skipped"):          # the probe could not try it (wireguard-tools missing): said so, and not "does not pass"
            links[link] = {"ok": None, "skipped": str(r["skipped"]), "at": now, "rtt_ms": None, "loss_pct": None, "error": str(r.get("error") or "")[:140]}
        else:
            links[link] = {"ok": bool(r.get("ok")), "at": now, "rtt_ms": r.get("rtt_ms"), "loss_pct": r.get("loss_pct"), "error": "" if r.get("ok") else str(r.get("error") or "")[:140]}
        d["pairs"][k] = {**p, "at": now, "links": links}
        st.put("knowledge", {"pairs": d["pairs"]})


def link_state(pair: dict | None, link: str, now: int, within: int = 6 * 3600) -> bool | None:
    """Did this link pass between the pair when the probe last asked (True / False), or has it not been asked within `within` seconds (None)?"""
    e = ((pair or {}).get("links") or {}).get(link)
    return bool(e.get("ok")) if e and e.get("ok") is not None and now - int(e.get("at") or 0) <= within else None


def note_quality(tunnel_id: int, q: dict) -> None:
    """The quality test of a live tunnel with a test door measured it with eight connections: that is a FULL measurement of its kind on its pair, for nothing."""
    from . import autopilot

    if q.get("score") is None or q.get("error"):
        return

    with session() as db:
        t = db.get(Tunnel, tunnel_id)

        if t is None or is_temp(t.name):
            return

        kind, iran_id, foreign_id = autopilot.kind_of(t.type, t.transport, t.record or {}), t.iran_server_id, t.foreign_server_id

    record(iran_id, foreign_id, {"kind": kind, "ok": int(q["score"]) >= 50, **{k: q.get(k) for k in NUMS}}, "full")


def forget_server(server_id: int) -> None:
    """A server that was removed takes its pairs with it."""
    with _LOCK:
        d = _doc()
        keep = {k: v for k, v in d["pairs"].items() if str(server_id) not in k.split(":")}

        if len(keep) != len(d["pairs"]):
            st.put("knowledge", {"pairs": keep})


# ------------------------------------------------------------------ what the Doctor remembers of every cut
def remember(iran_id: int, foreign_id: int, cause: str, tunnel: str, steps: list[dict], healed: bool, secs: int, now: int | None = None) -> None:
    """One cut, kept (the owner, 2026-10-16: "when a tunnel is cut it must find out why, and keep it in its memory so that the next time it knows what to do"): the cause the Doctor found, the steps it
    tried in order (each with the tunnel type it built), what healed it and how long that took."""
    now = now or int(time.time())
    done = [{"step": s.get("step"), "ok": bool(s.get("ok")), **({"kind": s["kind"]} if s.get("kind") else {})} for s in steps][:12]
    fix = next((s for s in reversed(done) if s["ok"]), None) if healed else None
    entry = {"at": now, "tunnel": tunnel, "pair": pair_key(iran_id, foreign_id), "cause": cause, "steps": done, "healed": bool(healed), "secs": int(secs), "fix": (fix or {}).get("step", ""), "kind": (fix or {}).get("kind", "")}

    with _LOCK:
        d = _doc()
        st.put("knowledge", {"incidents": (list(d.get("incidents") or []) + [entry])[-INCIDENTS:]})


def playbook(iran_id: int, foreign_id: int, cause: str, now: int | None = None) -> dict:
    """What healed this pair the last time this cause came up: {"kind", "step", "at", "n" (how many times that kind healed it), "secs"}; {} when nothing did (within PLAYBOOK_DAYS)."""
    now = now or int(time.time())
    key = pair_key(iran_id, foreign_id)
    rows = [e for e in reversed(list(_doc().get("incidents") or [])) if e.get("pair") == key and e.get("cause") == cause and e.get("healed") and e.get("kind") and now - int(e.get("at") or 0) <= PLAYBOOK_DAYS * 86400]

    if not rows:
        return {}

    return {"kind": rows[0]["kind"], "step": rows[0].get("fix", ""), "at": int(rows[0]["at"]), "n": sum(1 for e in rows if e["kind"] == rows[0]["kind"]), "secs": int(rows[0].get("secs") or 0)}


def cut_before(iran_id: int, foreign_id: int, causes: tuple | list, now: int | None = None, days: int = 14) -> bool:
    """Was this pair cut for one of these causes in the last `days` days? (A pair whose route was filtered a week ago needs no restart tried first: the Doctor goes straight to the type that connected.)"""
    now = now or int(time.time())
    key = pair_key(iran_id, foreign_id)
    return any(e.get("pair") == key and e.get("cause") in causes and now - int(e.get("at") or 0) <= days * 86400 for e in (_doc().get("incidents") or []))


def repeats(iran_id: int, foreign_id: int, now: int | None = None, days: int = 7) -> int:
    """How many times this pair was cut in the last `days` days (a tunnel that is cut again and again is the one that gets a standby)."""
    now = now or int(time.time())
    key = pair_key(iran_id, foreign_id)
    return sum(1 for e in (_doc().get("incidents") or []) if e.get("pair") == key and now - int(e.get("at") or 0) <= days * 86400)


def sibling_kinds(iran_id: int, foreign_id: int, now: int | None = None) -> list[str]:
    """The kinds that connect on the OTHER pairs of the same foreign server, most pairs first: an address that is filtered from Iran is filtered for every Iran server, so what works for its neighbours is
    the best guess for a pair nobody tested."""
    count: dict[str, int] = {}

    for key, p in _doc()["pairs"].items():
        i, f = (int(x) for x in key.split(":"))

        if f != foreign_id or i == iran_id:
            continue

        for kind, e in (p.get("kinds") or {}).items():
            if verdict(e) in ("good", "poor"):
                count[kind] = count.get(kind, 0) + 1

    return sorted(count, key=lambda k: (-count[k], PRIOR.index(k) if k in PRIOR else 99))


def configure(on: bool | None = None, from_hour: int | None = None, to_hour: int | None = None) -> dict:
    changes: dict = {}

    if on is not None:
        changes["on"] = bool(on)

    for key, v in (("from_hour", from_hour), ("to_hour", to_hour)):
        if v is not None:
            if not 0 <= int(v) <= 23:
                raise ValueError("an hour is from 0 to 23")

            changes[key] = int(v)

    if changes:
        st.put("knowledge", changes)

    d = st.get("knowledge")
    return {"on": bool(d["on"]), "from_hour": int(d["from_hour"]), "to_hour": int(d["to_hour"])}


# ------------------------------------------------------------------ how the Doctor uses it
def plan_for(iran_id: int, foreign_id: int, current: str, no_gre: bool, lossy: bool, now: int | None = None, dial_i: bool | None = None, dial_f: bool | None = None, cause: str = "") -> tuple[list[str], list[str]]:
    """(the kinds to try, the lines for the story of the repair): what healed this pair the last time this cause came up (the playbook), what is known about THIS pair, what connects on the other pairs of the
    same foreign server and - for a tunnel that is cut - who can still dial whom, decide the order. A tunnel that is cut tries EVERY kind in its order until one connects (the first that works stays);
    one that still carries data tries a few and is swapped only for something clearly better."""
    now = now or int(time.time())
    p = pair(iran_id, foreign_id)
    cur_link = linkmodels.split_kind(current)[0]
    cut_link = cur_link if no_gre and cur_link not in ("", "gre") else ""          # the link under this tunnel is not GRE and it is the link that is cut: its other transports would be cut the same way - the other links, native GRE too, may still pass
    drop_gre = no_gre and not cut_link or (no_gre and cause == "route_cut")        # (GRE is left out where GRE itself is filtered, or where the two servers cannot reach each other at all)
    base = None if lossy else cut_order(dial_i, dial_f, drop_gre)
    cannot = unreachable(dial_i, dial_f) if not lossy else frozenset()
    hints = [] if lossy else sibling_kinds(iran_id, foreign_id, now)
    kinds = candidates(p, current, drop_gre, lossy, now, limit=LIMIT if lossy else len(PRIOR), base=base, unreachable_kinds=cannot, prefer=hints)

    if cut_link:
        kinds = [k for k in kinds if linkmodels.split_kind(k)[0] != cut_link]

    from . import (
        reviewer,  # (the types the owner told the Doctor not to try between this pair - an approved lesson of the Reviewer; never all of them: a cut tunnel must always have something to try)
    )

    told = reviewer.avoided(iran_id, foreign_id, now)
    left = [k for k in kinds if k not in told]
    told = {k: v for k, v in told.items() if k in kinds} if left else {}
    kinds = left if told else kinds
    pb = {} if (lossy or not cause) else playbook(iran_id, foreign_id, cause, now)

    if pb and pb["kind"] in kinds and pb["kind"] not in cannot and pb["kind"] not in blocked(p, now):
        kinds = [pb["kind"]] + [k for k in kinds if k != pb["kind"]]
    else:
        pb = {}

    good = [r for r in ranked(p, now, include_poor=not lossy) if r["kind"] in kinds]
    bad = blocked(p, now)
    lines = []

    if pb:
        lines.append(f"The last time this happened on this pair ({cause.replace('_', ' ')}), {pb['kind']} healed it" + (f" ({pb['n']} times)" if pb["n"] > 1 else "") + " - it goes first.")

    if not lossy and dial_i is not None:
        lines.append("Who can still open connections: the Iran server " + ("CAN" if dial_i else "CANNOT") + " reach the foreign server" + ("" if dial_f is None else ", the foreign server " + ("CAN" if dial_f else "CANNOT") + " reach the Iran server")
                     + (" - the kinds where the foreign server dials in (reverse) come first." if dial_i is False and dial_f is not False else " - the fast kinds where the Iran server dials come first." if dial_i else "."))

    if good:
        lines.append("Known to connect between these two servers (what the Doctor tested before): " + ", ".join(f"{r['kind']} {r['score'] if r['score'] is not None else 'works'}" for r in good[:4]) + " - tried first, the best first.")
    else:
        lines.append("Nothing is known yet about the tunnel types between these two servers: " + ("the order of a cut tunnel is used (robust kinds first)." if not lossy else "the standard order is used.") + " The Doctor tests the pairs before the next failure (Doctor page, Knowledge).")

    if hints:
        lines.append("The other tunnels that end on this foreign server connect with: " + ", ".join(hints[:4]) + " - tried after what is known for this pair.")

    if bad:
        lines.append("Not tried, they failed on this pair lately: " + ", ".join(bad[:6]) + ".")

    if told:
        lines.append("Not tried, you told the Doctor not to (Reviewer lessons you approved): " + ", ".join(f"{k} (until {time.strftime('%Y-%m-%d', time.localtime(int(v['until'])))})" for k, v in sorted(told.items())) + ".")

    return kinds, lines


# ------------------------------------------------------------------ the survey
def _running() -> dict | None:
    from . import tunnel_test

    for job in list(tunnel_test._COMPARE.values()):
        if not job["done"] and job.get("source") in SURVEYS:
            return {"id": job["id"], "iran_id": job["iran_id"], "foreign_id": job["foreign_id"], "kind": job["current"], "done": len(job["results"]), "total": len(job["kinds"]), "source": job["source"]}

    return None


def survey_start(iran_id: int, foreign_id: int, source: str = "survey-manual", kinds: list[str] | None = None) -> str:
    """Test every tunnel kind (or only `kinds`) between an Iran server and a foreign server, one after the other, and keep what they gave. ValueError when the pair is not fit for it,
    RuntimeError when another tunnel test runs (only one at a time)."""
    from . import tunnel_test

    with session() as db:
        i, f = db.get(Server, iran_id), db.get(Server, foreign_id)

        if i is None or i.role != "iran" or f is None or f.role != "foreign":
            raise ValueError("pick an Iran server and a foreign server")

        if not (i.enabled and f.enabled) or i.online is False or f.online is False:
            raise ValueError("both servers must be on")

        names = (i.name, f.name)

    order = [k for k in PRIOR if kinds is None or k in kinds] + [k for k in (kinds or []) if k not in PRIOR]          # (the standard order first; the variants of the third tier after it, in the order given)
    measure = "full" if source == "survey-manual" else "latency"      # the night survey asks only whether a type connects and what its delay and loss are (a few pings through a temporary tunnel): no speed test on servers that carry users
    jid = tunnel_test.compare_start(iran_id, foreign_id, order, measure=measure, source=source, install_wg=source == "survey-manual")      # (only a survey the owner asked for may install wireguard-tools; the night one never installs a package)

    with session() as db:
        how = "by hand: with a speed test" if source == "survey-manual" else "the first survey of this pair, on quiet servers: delay and loss only" if source == "survey-cold" else "the night survey: delay and loss only"
        emit(db, "info", "tunnels", f"Survey of {names[0]} ⇄ {names[1]} started: {len(order)} tunnel types, one after the other ({how}).", names[0])

    return jid


def yield_slot() -> bool:
    """A repair needs the one place there is for tunnel tests: a survey that runs gives it up (the kind it is testing is cancelled and removed). True when a survey was running."""
    from . import tunnel_test

    r = _running()

    if r is None:
        return False

    tunnel_test.compare_stop(r["id"])
    return True


def stop() -> None:
    if not yield_slot():
        raise ValueError("no survey is running")


def _server_state(db, server_id: int, now: int) -> tuple[bool, float | None]:
    s = db.get(Server, server_id)
    smp = db.scalar(select(ServerSample).where(ServerSample.server_id == server_id, ServerSample.ts >= now - 360).order_by(ServerSample.ts.desc()).limit(1))
    return bool(s is not None and s.enabled and s.online is not False), (smp.cpu if smp is not None else None)


def _live_pairs(db) -> dict[tuple[int, int], int]:
    out: dict[tuple[int, int], int] = {}

    for t in db.scalars(select(Tunnel).where(Tunnel.enabled.is_(True))):
        if not is_temp(t.name):
            out[(t.iran_server_id, t.foreign_server_id)] = out.get((t.iran_server_id, t.foreign_server_id), 0) + 1

    return out


def pending_pairs(now: int, cold_only: bool = False) -> list[tuple[int, int, list[str]]]:
    """The pairs that carry a tunnel and have kinds due for a test: (Iran id, foreign id, the due kinds in the standard order), the pair with the least knowledge first.
    `cold_only`: only the pairs that have never been surveyed (nothing is known about them at all)."""
    from . import license

    allowed = [k for k in READY if license.tunnel_kind_allowed(k)]            # (the kinds a repair reaches for; the owner can survey all of them by hand)
    d = _doc()

    with session() as db:
        live = _live_pairs(db)
        out = []

        for i, f in live:
            if not (_server_state(db, i, now)[0] and _server_state(db, f, now)[0]):
                continue

            p = d["pairs"].get(pair_key(i, f)) or {}

            if cold_only and p.get("kinds"):
                continue

            due = due_kinds(p, allowed, now)

            if not due and not cold_only:                     # (the night survey goes on when a pair knows its first tier: the rest of the standard list, then the variants of the links that passed)
                due = due_kinds(p, [k for k in MORE if license.tunnel_kind_allowed(k)], now) or due_kinds(p, [k for k in link_variants(p) if license.tunnel_kind_allowed(k)], now)

            if due:
                out.append((len(p.get("kinds") or {}), int(p.get("at") or 0), i, f, due))

    return [(i, f, due) for _, _, i, f, due in sorted(out)]


def not_now(iran_id: int, foreign_id: int, now: int, quiet: float = QUIET_MBPS) -> str:
    """Why the pair must not be surveyed right now ('' when it may): a server is busy, or its tunnels carry users (`quiet`: Mb/s of users under which the survey goes on)."""
    with session() as db:
        for sid in (iran_id, foreign_id):
            _on, cpu = _server_state(db, sid, now)

            if cpu is not None and cpu >= BUSY_CPU:
                return f"a server is busy (CPU {cpu:.0f} %)"

        carried = sum((t.rx_mbps or 0) + (t.tx_mbps or 0) for t in db.scalars(select(Tunnel).where(Tunnel.enabled.is_(True), (Tunnel.iran_server_id == iran_id) | (Tunnel.foreign_server_id == foreign_id)))
                      if not is_temp(t.name))

    return f"its servers carry {carried:.0f} Mb/s for users (the survey waits under {quiet:.0f})" if carried >= quiet else ""


def _tell_finished() -> None:
    """One event for every survey that ended."""
    from . import tunnel_test

    for job in list(tunnel_test._COMPARE.values()):
        if not job["done"] or job.get("source") not in SURVEYS or job.get("told"):
            continue

        job["told"] = True

        with session() as db:
            i, f = db.get(Server, job["iran_id"]), db.get(Server, job["foreign_id"])
            names = (i.name if i else "?", f.name if f else "?")
            rows = [r for r in job["results"] if r.get("ok")]
            best = sorted(rows, key=lambda r: -(r.get("score") or 0))[:3]
            text = (f"Survey of {names[0]} ⇄ {names[1]} " + ("stopped" if job.get("stop") else "finished") + f": {len(job['results'])} of {len(job['kinds'])} tunnel types tested, {len(rows)} work"
                    + (" - best: " + ", ".join(f"{r['kind']} {r.get('score')}" for r in best) if best else "") + ".")
            emit(db, "ok" if rows else "warn", "tunnels", text, names[0])


def run() -> None:
    """Job (every ten minutes): tell about a survey that ended, stop one that must not go on, start the next one when everything allows it."""
    if jobs.stopping() or get_settings().read_only:
        return

    from . import autopilot, tunnel_test

    now = int(time.time())
    d = st.get("knowledge")
    tz = int(st.get("report").get("tz_offset_minutes", 210))
    window = in_window(now, tz, int(d["from_hour"]), int(d["to_hour"]))
    _tell_finished()
    running = _running()
    busy = bool(autopilot.repairing_names()) or autopilot.storm_now()

    if running:
        if busy or (running["source"] == "survey" and not (d["on"] and window)) or (running["source"] == "survey-cold" and not d["on"]):
            tunnel_test.compare_stop(running["id"])            # a repair needs the place, or the night is over

        return

    if not d["on"] or busy:
        return

    cold = not window                                          # outside the night window only the pairs nobody has tested yet are surveyed, and only on very quiet servers
    source = "survey-cold" if cold else "survey"

    for iran_id, foreign_id, kinds in pending_pairs(now, cold_only=cold):
        why = not_now(iran_id, foreign_id, now, COLD_QUIET_MBPS if cold else QUIET_MBPS)

        if why:
            log.info("survey of %s:%s waits: %s", iran_id, foreign_id, why)
            continue

        try:
            survey_start(iran_id, foreign_id, source, kinds)
        except (ValueError, RuntimeError) as e:
            log.info("survey of %s:%s not started: %s", iran_id, foreign_id, str(e)[:120])

        return


# ------------------------------------------------------------------ for the page
def state(now: int | None = None) -> dict:
    now = now or int(time.time())
    d = _doc()
    tz = int(st.get("report").get("tz_offset_minutes", 210))

    with session() as db:
        names = {s.id: s.name for s in db.scalars(select(Server))}
        live = _live_pairs(db)

    rows = []

    for k in sorted(set(d["pairs"]) | {pair_key(i, f) for i, f in live}):
        i, f = (int(x) for x in k.split(":"))

        if i not in names or f not in names:
            continue

        p = d["pairs"].get(k) or {}
        kinds = p.get("kinds") or {}
        rows.append({"iran_id": i, "foreign_id": f, "iran": names[i], "foreign": names[f], "tunnels": live.get((i, f), 0), "at": int(p.get("at") or 0), "tested": len(kinds),
                     "due": len(due_kinds(p, READY, now)), "due_more": len(due_kinds(p, MORE, now)) + len(due_kinds(p, link_variants(p), now)), "best": ranked(p, now), "poor": poor(p, now), "cuts": repeats(i, f, now), "links": dict(p.get("links") or {}),
                     "blocked": [{"kind": x, "error": kinds[x]["last"].get("error") or "", "at": int(kinds[x]["last"].get("at") or 0)} for x in blocked(p, now)],
                     "untested": untested(p)})

    rows.sort(key=lambda r: (r["iran"].lower(), r["foreign"].lower()))
    return {"on": bool(d["on"]), "from_hour": int(d["from_hour"]), "to_hour": int(d["to_hour"]), "tz_minutes": tz, "window_open": in_window(now, tz, int(d["from_hour"]), int(d["to_hour"])),
            "running": _running(), "kinds": len(PRIOR), "ready": list(READY), "pairs": rows,
            "incidents": [{**e, "pair_name": " ⇄ ".join(names.get(int(x), "?") for x in str(e.get("pair", "0:0")).split(":"))} for e in list(reversed(d.get("incidents") or []))[:30]]}

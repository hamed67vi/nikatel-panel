"""The link probe: which of the nine links does a pair of servers carry RIGHT NOW? (1.69.0)

Building a tunnel over a link that the route does not carry costs a minute or two (the build waits for the link to answer, then for the engine, then rolls back) - and a tunnel that is CUT tries one kind after the other.
The probe asks the carriers themselves, all at the same time and in seconds: for every link wanted it makes a temporary link of that model on BOTH servers (a small private network of 10.253.x.x, a tagged
firewall rule for exactly this peer), pings the inner addresses ten times each way and takes the link away again - always, also when something fails in between. Nothing stays on either server.

  * the links that need only a different IP protocol or UDP port run side by side; the three IPIP models share one pair of addresses (IPIP has no key: one tunnel per pair), so they run one after the other;
  * a link whose tool or kernel module is missing is not tried (`ok` None = unknown for a missing package such as wireguard-tools, which the probe never installs; `ok` False for a missing kernel module);
  * every answer is kept in the Doctor's memory of the pair (`knowledge.record_link`), so the next cut knows what passed an hour ago and what did not.

The Doctor uses it when a tunnel is cut (`autopilot._switch_cut`) to leave out, in seconds, the kinds whose link does not pass. (The night survey does not use it: it builds every kind for real, which is what it is there to learn.)"""
from __future__ import annotations

import random
import re
import secrets
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from sqlalchemy import select

from ..config import get_settings
from ..db import session
from ..log import get_logger
from ..models import Server, Tunnel
from ..ssh import Node, SSHError, run_action, run_script
from . import builder, knowledge, linkmodels

log = get_logger("linkprobe")
NAME_FMT = "lp-{}"                   # the temporary interface of a probe: "lp-fou", "lp-ipipgue" (at most 15 characters)
GOOD = 6                             # of 10 pings: a link that answers this many both ways passes (a lossy route still carries a tunnel)
_LOCKS: dict[str, threading.Lock] = {}
_LOCK = threading.Lock()


def lock_for(iran_id: int, foreign_id: int) -> threading.Lock:
    with _LOCK:
        return _LOCKS.setdefault(f"{iran_id}:{foreign_id}", threading.Lock())


def verdict(received_i: int, received_f: int) -> bool:
    """Pure: does a link pass - both ends got GOOD of their ten pings."""
    return received_i >= GOOD and received_f >= GOOD


def received(out: str) -> tuple[int, float | None]:
    """Pure: (pings that came back, the average ms) in the answer of the `link_probe_ping` script."""
    m = re.search(r"(\d+) received", str(out))
    r = re.search(r"= [\d.]+/([\d.]+)/", str(out))
    return (int(m.group(1)) if m else 0), (float(r.group(1)) if r else None)


def plan(want: list[str], facts_i: dict, facts_f: dict, taken_nets: list[str], hosts: tuple[str, str]) -> dict[str, dict]:
    """Pure (apart from the choice of random names and keys): for every link wanted, either what to run - {"net": (network, iran_ip, foreign_ip), "port": udp port or 0} - or why it is not tried -
    {"skip": {"ok": None | False, "error": sentence}}. Different links never share a network or a UDP port."""
    out: dict[str, dict] = {}
    nets, ports = list(taken_nets), set()

    for f in (facts_i, facts_f):
        ports |= f["udp"] | f["fou_ports"]

    for link in want:
        miss = [m for m in linkmodels.KERNEL[link] if m not in ("fou", "wireguard") and facts_i["mods"] and (m not in facts_i["mods"] or m not in facts_f["mods"])]

        if link in ("fou", "gue", "ipipfou", "ipipgue") and not (facts_i["fou_mod"] and facts_f["fou_mod"]):
            miss.append("fou")

        if link == "wg" and not (facts_i["wg_mod"] and facts_f["wg_mod"]):
            out[link] = {"skip": {"ok": False, "error": "a server cannot load the wireguard kernel module"}}
            continue

        if miss:
            out[link] = {"skip": {"ok": False, "error": "a server cannot load the " + ", ".join(sorted(set(miss))) + " kernel module"}}
            continue

        if link == "wg" and not (facts_i["wg_tool"] and facts_f["wg_tool"]):
            out[link] = {"skip": {"ok": None, "error": "wireguard-tools is not installed on a server (the probe never installs a package)"}}
            continue

        if link in linkmodels.ONE_PER_PAIR and any(p in facts_i["ipip_pairs"] or p in facts_f["ipip_pairs"] for p in ((hosts[0], hosts[1]), (hosts[1], hosts[0]))):
            out[link] = {"skip": {"ok": None, "error": "an IPIP link between these two servers exists already - a tunnel of this pair or the warm standby of one runs over it (IPIP has no key: one per pair)"}}
            continue

        port = builder.pick_link_port(link, ports) if link in linkmodels.PORTS else 0

        if link in linkmodels.PORTS and not port:
            out[link] = {"skip": {"ok": None, "error": "no free UDP port in the range of this link"}}
            continue

        ports.add(port)
        net = builder.allocate_network(nets)
        nets.append(net[0])
        out[link] = {"net": net, "port": port}

    return out


def _put(node: Node, path: str, text: str, mode: str) -> None:
    builder._put(node, path, text, mode)


def _probe_one(link: str, name: str, nodes: tuple[Node, Node], hosts: tuple[str, str], net: tuple[str, str, str], port: int) -> dict:
    """One link: files and up on both ends, ten pings each way, then always removed again."""
    iran, foreign = nodes
    ihost, fhost = hosts
    _, ip_i, ip_f = net
    key = random.SystemRandom().randint(1, 2**31 - 1)
    res: dict = {"ok": False, "rtt_ms": None, "loss_pct": None, "error": "", "secs": 0.0}
    t0 = time.time()
    pubs: dict[str, str] = {}
    ips: dict[str, dict] = {}

    try:
        if link == "wg":
            oi, of = (run_action(n, "wg_keygen", {"NAME": name}, timeout=40) for n in (iran, foreign))
            pubs = {"iran": builder.parse_pub(oi), "foreign": builder.parse_pub(of)}

            if not pubs["iran"] or not pubs["foreign"]:
                raise RuntimeError("a server did not make its WireGuard key")

        if link in ("ipsec", "ipsecnat"):
            a, b = secrets.token_hex(20), secrets.token_hex(20)
            sa, sb = random.SystemRandom().randint(256, 2**32 - 1), random.SystemRandom().randint(256, 2**32 - 1)
            ips = {"iran": {"spi_tx": sa, "spi_rx": sb, "key_tx": a, "key_rx": b}, "foreign": {"spi_tx": sb, "spi_rx": sa, "key_tx": b, "key_rx": a}}

        def end(node: Node, local: str, remote: str, addr: str, peer_pub: str, ipsec: dict | None) -> None:
            files = builder.link_scripts(link, name, local, remote, key, addr, 1360, port=port, peer_pub=peer_pub, ipsec=ipsec)

            for path, text in files.items():
                if "/sbin/nikatel-gre-up-" in path or "/sbin/nikatel-gre-down-" in path or path.endswith(".keys"):
                    _put(node, path, text, "0700" if "/sbin/" in path else "0600")

            out = run_action(node, "link_probe_run", {"NAME": name, "MODE": "up"}, timeout=40)

            if "rc=0" not in out:
                raise RuntimeError(f"the link did not come up on {local}: {out.strip()[-120:]}")

        builder._both(lambda: end(iran, ihost, fhost, f"{ip_i}/30", pubs.get("foreign", ""), ips.get("iran")), lambda: end(foreign, fhost, ihost, f"{ip_f}/30", pubs.get("iran", ""), ips.get("foreign")))
        time.sleep(1.0)                                                           # (WireGuard and the SAs of IPsec need a moment)
        (ri, rtt), (rf, _) = builder._both(lambda: received(run_script(iran, "link_probe_ping", {"IFACE": name, "PEER": ip_f}, timeout=60)), lambda: received(run_script(foreign, "link_probe_ping", {"IFACE": name, "PEER": ip_i}, timeout=60)))
        res.update(ok=verdict(ri, rf), rtt_ms=rtt, loss_pct=round(100.0 - min(ri, rf) * 10.0, 1))

        if not res["ok"]:
            res["error"] = f"{ri} of 10 pings came back from the foreign end, {rf} of 10 from the Iran end: this link does not pass between the two servers"
    except (SSHError, ValueError, RuntimeError) as e:
        res["ok"], res["error"] = None, str(e)[:200]           # the probe itself failed: nothing is known about the link
    finally:
        for node in nodes:
            try:
                run_action(node, "link_probe_clean", {"NAME": name}, timeout=40)
            except (SSHError, ValueError):
                log.warning("link probe: could not remove %s on %s", name, node.host)

        res["secs"] = round(time.time() - t0, 1)

    return res


def probe(iran_id: int, foreign_id: int, links: list[str] | None = None, record: bool = True) -> dict[str, dict]:
    """The answer of every link wanted: {link: {"ok": True | False | None, "rtt_ms", "loss_pct", "error", "secs"}} (None: unknown - the probe could not tell). Refused while the panel is read-only; one probe of a pair at
    a time (a second caller gets what the first one found when it is done)."""
    if get_settings().read_only:
        raise ValueError("the panel is read-only; set NIKATEL_READ_ONLY=false on the panel server")

    want = [k for k in (links or linkmodels.LINKS) if k in linkmodels.LINKS]

    with session() as db:
        i, f = db.get(Server, iran_id), db.get(Server, foreign_id)

        if i is None or f is None or i.role != "iran" or f.role != "foreign":
            raise ValueError("pick an Iran server and a foreign server")

        if not (i.enabled and f.enabled) or i.online is False or f.online is False:
            raise ValueError("both servers must be on")

        nodes = (Node(i.host, i.ssh_user, i.ssh_port), Node(f.host, f.ssh_user, f.ssh_port))
        hosts = (i.host, f.host)
        taken = [str((t.record or {}).get("network")) for t in db.scalars(select(Tunnel)) if (t.record or {}).get("network")]

    lock = lock_for(iran_id, foreign_id)
    waited = not lock.acquire(blocking=False)

    if waited:
        lock.acquire()                                                           # the other probe is done: this one runs its own (the pair may have changed)

    try:
        raw = builder._both(lambda: (run_script(nodes[0], "link_facts", timeout=40), run_script(nodes[0], "build_facts", timeout=40)), lambda: (run_script(nodes[1], "link_facts", timeout=40), run_script(nodes[1], "build_facts", timeout=40)))
        li, lf = builder.parse_link_facts(raw[0][0]), builder.parse_link_facts(raw[1][0])
        bi, bf = builder.parse_facts(raw[0][1]), builder.parse_facts(raw[1][1])
        steps = plan(want, li, lf, taken + bi["ips"] + bf["ips"], hosts)
        out: dict[str, dict] = {k: v["skip"] for k, v in steps.items() if "skip" in v}
        busy_names = set(bi["links"]) | set(bf["links"])
        jobs = {k: v for k, v in steps.items() if "skip" not in v}

        def run_link(link: str) -> tuple[str, dict]:
            name = NAME_FMT.format(link)

            if name in busy_names:
                return link, {"ok": None, "error": f"an interface called {name} exists already on a server", "rtt_ms": None, "loss_pct": None, "secs": 0.0}

            return link, _probe_one(link, name, nodes, hosts, jobs[link]["net"], jobs[link]["port"])

        ipip_group = [k for k in jobs if k in linkmodels.ONE_PER_PAIR]
        rest = [k for k in jobs if k not in linkmodels.ONE_PER_PAIR]

        def run_group(group: list[str]) -> list[tuple[str, dict]]:
            return [run_link(k) for k in group]

        with ThreadPoolExecutor(max(1, len(rest) + (1 if ipip_group else 0))) as ex:
            futures = [ex.submit(run_group, [k]) for k in rest] + ([ex.submit(run_group, ipip_group)] if ipip_group else [])

            for fu in futures:
                for link, r in fu.result():
                    out[link] = r
    finally:
        lock.release()

    if record:
        now = int(time.time())

        for link, r in out.items():
            if r.get("ok") is not None:
                knowledge.record_link(iran_id, foreign_id, link, r, now)

    return out


def summary(res: dict[str, dict]) -> str:
    """Pure: one sentence for the story of a repair - what passes (with its delay) and what does not."""
    ok = [f"{linkmodels.TITLE[k]} {round(r['rtt_ms'])} ms" if r.get("rtt_ms") is not None else linkmodels.TITLE[k] for k, r in res.items() if r.get("ok")]
    no = [linkmodels.TITLE[k] for k, r in res.items() if r.get("ok") is False]
    unknown = [linkmodels.TITLE[k] for k, r in res.items() if r.get("ok") is None]
    return "passes: " + (", ".join(ok) or "nothing") + "; does not pass: " + (", ".join(no) or "nothing") + ("; not known: " + ", ".join(unknown) if unknown else "")

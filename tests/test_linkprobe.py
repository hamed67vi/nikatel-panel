"""The link probe (1.69.0): which of the nine links does a pair carry right now - asked of the carriers themselves, all at once, in seconds, so that a cut tunnel does not build kinds that cannot work. Tested here: the pure
parts (what a link needs, what it takes, the verdict), the run with two fake servers (what is written, what is run, what is ALWAYS cleaned up), the memory of the pair, and what the Doctor does with the answer."""
import threading
import time

import pytest

from nikatel import ssh
from nikatel.config import get_settings
from nikatel.db import session
from nikatel.models import Server
from nikatel.services import autopilot as ap
from nikatel.services import builder, knowledge, linkmodels, linkprobe

FACTS = "WG_MOD=1\nWG_TOOL=1\nFOU_MOD=1\nUDP=53\nFOU_PORTS=\nAPT=1\nMODS=ip_gre ipip fou xfrm_interface esp4 wireguard\nIPIP_PAIRS=\n"
BUILD_FACTS = "IPS=203.0.113.1/24\nLINKS=eth0 lo\nLISTEN=22\nUNITS=\nBROKEN=\nGRE_MOD=1\nBACKHAUL=v0.6\nIPTABLES=1\n"


def facts(extra=""):
    return builder.parse_link_facts(FACTS + extra)


# ------------------------------------------------------------------ pure
def test_the_answers_of_the_ping_script_are_read_and_a_link_passes_when_both_ends_hear_it():
    assert linkprobe.received("10 packets transmitted, 10 received, 0% packet loss\nrtt min/avg/max/mdev = 70.1/75.5/80.2/3.0 ms") == (10, 75.5)
    assert linkprobe.received("10 packets transmitted, 0 received, 100% packet loss") == (0, None) and linkprobe.received("") == (0, None)
    assert linkprobe.verdict(10, 10) and linkprobe.verdict(6, 7) and not linkprobe.verdict(5, 10) and not linkprobe.verdict(10, 0)


def test_the_plan_gives_every_link_its_own_network_and_port_and_says_why_it_leaves_one_out():
    p = linkprobe.plan(list(linkmodels.LINKS), facts(), facts(), ["10.4.5.0/30"], ("203.0.113.1", "198.51.100.2"))
    assert set(p) == set(linkmodels.LINKS) and all("net" in v for v in p.values())
    assert len({v["net"][0] for v in p.values()}) == 9 and "10.4.5.0/30" not in {v["net"][0] for v in p.values()}          # nine networks, none of a tunnel
    ports = [v["port"] for v in p.values() if v["port"]]
    assert len(ports) == len(set(ports)) == 6 and p["gre"]["port"] == 0 and p["ipip"]["port"] == 0 and p["ipsec"]["port"] == 0
    assert 5500 <= p["fou"]["port"] <= 5599 and 5600 <= p["gue"]["port"] <= 5699 and 51820 <= p["wg"]["port"] <= 51919 and 4500 <= p["ipsecnat"]["port"] <= 4599

    q = linkprobe.plan(list(linkmodels.LINKS), facts(), facts("").copy() | {"mods": {"ip_gre", "fou"}, "wg_tool": False}, [], ("203.0.113.1", "198.51.100.2"))
    assert q["ipip"]["skip"]["ok"] is False and "ipip" in q["ipip"]["skip"]["error"] and q["ipsec"]["skip"]["ok"] is False and "xfrm_interface" in q["ipsec"]["skip"]["error"]
    assert q["wg"]["skip"]["ok"] is None and q["wg"]["skip"]["skipped"] == "tools" and q["wg"]["skip"]["tools"] == {"iran": True, "foreign": False}                    # a missing package: unknown, not failed
    assert "MISSING on the foreign server" in q["wg"]["skip"]["error"] and "never installs a package" in q["wg"]["skip"]["error"]
    assert "net" in q["gre"] and "net" in q["fou"]

    held = facts("IPIP_PAIRS=198.51.100.2>203.0.113.1\n")
    held["ipip_pairs"] = {("198.51.100.2", "203.0.113.1")}
    r = linkprobe.plan(["ipip", "ipipfou", "ipipgue", "gue"], held, facts(), [], ("203.0.113.1", "198.51.100.2"))
    assert all(r[k]["skip"]["ok"] is None and "exists already" in r[k]["skip"]["error"] for k in ("ipip", "ipipfou", "ipipgue")) and "net" in r["gue"]

    no_fou = facts()
    no_fou["fou_mod"] = False
    assert linkprobe.plan(["fou", "gre"], no_fou, facts(), [], ("203.0.113.1", "198.51.100.2"))["fou"]["skip"]["ok"] is False


def test_a_summary_for_the_story_of_a_repair():
    res = {"gre": {"ok": False}, "fou": {"ok": True, "rtt_ms": 75.6}, "wg": {"ok": None}, "ipsec": {"ok": True, "rtt_ms": None}}
    assert linkprobe.summary(res) == "passes: GRE over UDP (FOU) 76 ms, IPsec (ESP); does not pass: GRE; not known: WireGuard"
    assert linkprobe.summary({"gre": {"ok": False}}) == "passes: nothing; does not pass: GRE"


# ------------------------------------------------------------------ the two reviewed actions
def test_the_probe_runs_only_the_two_scripts_of_its_own_temporary_link():
    r = ssh.render_action("link_probe_run", {"NAME": "lp-fou", "MODE": "up"})
    assert "n='lp-fou'" in r and '/usr/local/sbin/nikatel-gre-up-$n' in r and 'LINK_PROBE up $n' in r and "rm " not in r

    for bad in ({"NAME": "test-1", "MODE": "up"}, {"NAME": "lp-x;id", "MODE": "up"}, {"NAME": "lp-fou", "MODE": "sh"}, {"NAME": "ca-ir1", "MODE": "down"}, {"NAME": "lp-", "MODE": "up"}):
        with pytest.raises(ValueError):
            ssh.render_action("link_probe_run", bad)

    c = ssh.render_action("link_probe_clean", {"NAME": "lp-ipsec"})
    assert 'rm -f "/usr/local/sbin/nikatel-gre-up-$n"' in c and '"/etc/nikatel/ipsec/$n.keys"' in c and "lp-ipsec" in c and "nikatel-ipsec-sock" in c

    with pytest.raises(ValueError):
        ssh.render_action("link_probe_clean", {"NAME": "tunnel-live1"})                                        # (a real tunnel is never touched)

    assert "lp-" in ssh.render_action("purge_test_leftovers", {"TESTPORT": "9999"})                            # a probe that was cut short is swept with the other test leftovers


# ------------------------------------------------------------------ a probe of one link with two fake servers
class World:
    def __init__(self, monkeypatch, pings=(10, 10), up_rc=0):
        self.calls, self.files = [], []
        self.pings, self.up_rc = pings, up_rc
        monkeypatch.setattr(linkprobe, "run_action", self.action)
        monkeypatch.setattr(linkprobe, "run_script", self.script)
        monkeypatch.setattr(linkprobe, "_put", lambda node, path, text, mode: self.files.append((node.host, path, mode, text)))
        monkeypatch.setattr(linkprobe.time, "sleep", lambda s: None)

    def action(self, node, name, params=None, timeout=60):
        self.calls.append((node.host, name, dict(params or {})))

        if name == "wg_keygen":
            return "PUB=" + ("A" if node.host.startswith("203") else "B") * 43 + "=\n"

        if name == "link_probe_run":
            return f"LINK_PROBE up {params['NAME']} rc={self.up_rc}\n"

        return "LINK_PROBE_CLEAN\n"

    def script(self, node, name, params=None, timeout=40):
        self.calls.append((node.host, name, dict(params or {})))
        n = self.pings[0] if node.host.startswith("203") else self.pings[1]
        return f"10 packets transmitted, {n} received, {100 - n * 10}% packet loss\nrtt min/avg/max/mdev = 70.0/72.5/75.0/1.0 ms\n"


def run_one(link, **kw):
    from nikatel.ssh import Node

    return linkprobe._probe_one(link, f"lp-{link}", (Node("203.0.113.1", "root", 22), Node("198.51.100.2", "root", 22)), ("203.0.113.1", "198.51.100.2"), ("10.253.9.0/30", "10.253.9.1", "10.253.9.2"), kw.get("port", 5555))


def test_a_link_that_answers_both_ways_passes_and_everything_is_removed(monkeypatch):
    w = World(monkeypatch)
    r = run_one("fou", port=5555)
    assert r["ok"] is True and r["rtt_ms"] == 72.5 and r["loss_pct"] == 0.0 and r["error"] == ""
    assert [c[1] for c in w.calls if c[0] == "203.0.113.1"] == ["link_probe_run", "link_probe_ping", "link_probe_clean"]                  # up, the pings, away - on both servers
    assert [c[1] for c in w.calls if c[0] == "198.51.100.2"] == ["link_probe_run", "link_probe_ping", "link_probe_clean"]
    assert {p for _, p, _, _ in w.files} == {"/usr/local/sbin/nikatel-gre-up-lp-fou", "/usr/local/sbin/nikatel-gre-down-lp-fou"} and all(m == "0700" for _, _, m, _ in w.files)


def test_a_link_that_one_end_does_not_hear_does_not_pass_and_is_still_removed(monkeypatch):
    w = World(monkeypatch, pings=(10, 2))
    r = run_one("gue", port=5655)
    assert r["ok"] is False and "this link does not pass" in r["error"] and r["loss_pct"] == 80.0
    assert [c[1] for c in w.calls].count("link_probe_clean") == 2


def test_a_link_that_does_not_come_up_is_unknown_not_failed_and_is_removed(monkeypatch):
    w = World(monkeypatch, up_rc=1)
    r = run_one("ipip", port=0)
    assert r["ok"] is None and "did not come up" in r["error"]                                                    # (the script failed: nothing is known about the carrier)
    assert [c[1] for c in w.calls].count("link_probe_clean") == 2 and "link_probe_ping" not in [c[1] for c in w.calls]


def test_wireguard_makes_its_keys_on_the_servers_and_ipsec_gets_its_keys_in_a_file_of_0600(monkeypatch):
    w = World(monkeypatch)
    assert run_one("wg", port=51825)["ok"] is True and [c[1] for c in w.calls].count("wg_keygen") == 2
    assert not any(p.endswith(".keys") for _, p, _, _ in w.files)
    w2 = World(monkeypatch)
    assert run_one("ipsec", port=0)["ok"] is True
    keys = [(h, p, m, t) for h, p, m, t in w2.files if p.endswith(".keys")]
    assert len(keys) == 2 and all(m == "0600" and t.startswith("KEY_TX=") for _, _, m, t in keys) and "wg_keygen" not in [c[1] for c in w2.calls]
    ti, tf = keys[0][3].splitlines(), keys[1][3].splitlines()
    assert ti[0].split("=")[1] == tf[1].split("=")[1] and ti[1].split("=")[1] == tf[0].split("=")[1]               # what one end sends with, the other receives with


# ------------------------------------------------------------------ probe(): all links at once
def two_servers(monkeypatch):
    monkeypatch.setattr(get_settings(), "read_only", False)

    with session() as db:
        i = Server(name="ir-pr", host="203.0.113.1", role="iran", online=True)
        f = Server(name="fr-pr", host="198.51.100.2", role="foreign", online=True)
        db.add_all([i, f])
        db.flush()
        return i.id, f.id


def test_every_link_asked_gets_an_answer_the_three_ipip_models_one_after_the_other_and_the_answers_are_kept(monkeypatch):
    ir, fr = two_servers(monkeypatch)
    monkeypatch.setattr(linkprobe, "run_script", lambda node, name, params=None, timeout=40: FACTS if name == "link_facts" else BUILD_FACTS)
    running, overlap, seen = set(), [], []
    lock = threading.Lock()

    def fake_one(link, name, nodes, hosts, net, port):
        with lock:
            if link in linkmodels.ONE_PER_PAIR and running & linkmodels.ONE_PER_PAIR:
                overlap.append(link)

            running.add(link)
            seen.append(link)

        time.sleep(0.05)

        with lock:
            running.discard(link)

        return {"ok": link not in ("gre", "ipsec"), "rtt_ms": 75.0, "loss_pct": 0.0, "error": "" if link not in ("gre", "ipsec") else "no answer", "secs": 0.1}

    monkeypatch.setattr(linkprobe, "_probe_one", fake_one)
    res = linkprobe.probe(ir, fr)
    assert set(res) == set(linkmodels.LINKS) and overlap == [] and sorted(seen) == sorted(linkmodels.LINKS)
    assert res["gre"]["ok"] is False and res["fou"]["ok"] is True and res["ipsec"]["ok"] is False
    pair = knowledge.pair(ir, fr)
    now = int(time.time())
    assert knowledge.link_state(pair, "fou", now) is True and knowledge.link_state(pair, "gre", now) is False and knowledge.link_state(pair, "gre", now + 7 * 3600) is None
    assert pair["links"]["gre"]["error"] == "no answer" and pair["links"]["fou"]["rtt_ms"] == 75.0
    knowledge.record(ir, fr, {"kind": "tcpmux", "ok": True, "score": 80}, "latency")
    assert "links" in knowledge.pair(ir, fr) and "tcpmux" in knowledge.pair(ir, fr)["kinds"]                        # (a test of a kind keeps what the probe found)


def test_a_probe_asks_only_the_links_wanted_and_never_while_the_panel_is_read_only(monkeypatch):
    ir, fr = two_servers(monkeypatch)
    monkeypatch.setattr(linkprobe, "run_script", lambda node, name, params=None, timeout=40: FACTS if name == "link_facts" else BUILD_FACTS)
    monkeypatch.setattr(linkprobe, "_probe_one", lambda link, *a: {"ok": True, "rtt_ms": 70.0, "loss_pct": 0.0, "error": "", "secs": 0.1})
    assert set(linkprobe.probe(ir, fr, ["fou", "wg", "nope"])) == {"fou", "wg"}
    monkeypatch.setattr(get_settings(), "read_only", True)

    with pytest.raises(ValueError, match="read-only"):
        linkprobe.probe(ir, fr)

    monkeypatch.setattr(get_settings(), "read_only", False)

    with session() as db:
        db.get(Server, ir).online = False

    with pytest.raises(ValueError, match="both servers must be on"):
        linkprobe.probe(ir, fr)

    with pytest.raises(ValueError, match="Iran server and a foreign server"):
        linkprobe.probe(fr, ir)


# ------------------------------------------------------------------ the Doctor
def test_a_cut_tunnel_leaves_out_the_kinds_whose_link_does_not_pass_before_it_builds_anything(monkeypatch):
    said = []
    monkeypatch.setattr(ap, "_say", lambda aid, line, done=None, ok=None: said.append(line))
    seen = []

    def fake_probe(iran_id, foreign_id, links=None, record=True):
        seen.append(list(links))
        return {"fou": {"ok": True, "rtt_ms": 76.0}, "gue": {"ok": False}, "wg": {"ok": None}, "ipsec": {"ok": False}}

    monkeypatch.setattr(ap.linkprobe, "probe", fake_probe)
    snap = {"iran_id": 1, "foreign_id": 2}
    kept = ap._probe_links(7, snap, ["bp:wssmux", "fou:tcpmux", "gue:tcpmux", "wg:tcpmux", "ipsec:tcpmux", "chisel:tls"])
    assert kept == ["bp:wssmux", "fou:tcpmux", "wg:tcpmux", "chisel:tls"] and seen == [["fou", "gue", "wg", "ipsec"]]        # an engine with no link stays; a link the probe could not judge stays
    assert any(s.startswith("Link probe: passes: GRE over UDP (FOU) 76 ms; does not pass: GRE over UDP (GUE), IPsec (ESP); not known: WireGuard") for s in said)
    assert any("2 kind(s) left out" in s and "gue:tcpmux, ipsec:tcpmux" in s for s in said)


def test_when_nothing_has_a_link_or_the_probe_cannot_run_the_list_is_untouched(monkeypatch):
    said = []
    monkeypatch.setattr(ap, "_say", lambda aid, line, done=None, ok=None: said.append(line))
    monkeypatch.setattr(ap.linkprobe, "probe", lambda *a, **k: (_ for _ in ()).throw(AssertionError("must not run")))
    assert ap._probe_links(1, {"iran_id": 1, "foreign_id": 2}, ["bp:wssmux", "chisel:tls", "paqet"]) == ["bp:wssmux", "chisel:tls", "paqet"] and said == []
    monkeypatch.setattr(ap.linkprobe, "probe", lambda *a, **k: (_ for _ in ()).throw(ValueError("the panel is read-only")))
    assert ap._probe_links(1, {"iran_id": 1, "foreign_id": 2}, ["fou:tcpmux", "bp:wss"]) == ["fou:tcpmux", "bp:wss"]
    assert any("could not run (the panel is read-only)" in s for s in said)


def test_native_gre_kinds_are_probed_too_and_when_every_link_fails_only_the_engines_remain(monkeypatch):
    monkeypatch.setattr(ap, "_say", lambda *a, **k: None)
    monkeypatch.setattr(ap.linkprobe, "probe", lambda i, f, links=None, record=True: {k: {"ok": False} for k in links})
    assert ap._probe_links(1, {"iran_id": 1, "foreign_id": 2}, ["tcpmux", "fou:tcpmux", "bp:wss"]) == ["bp:wss"]
    assert ap._probe_links(1, {"iran_id": 1, "foreign_id": 2}, ["tcpmux", "fou:tcpmux"]) == []

def test_the_api_asks_the_probe_and_the_knowledge_page_carries_the_answers(client, monkeypatch):
    ir, fr = two_servers(monkeypatch)
    monkeypatch.setattr(linkprobe, "run_script", lambda node, name, params=None, timeout=40: FACTS if name == "link_facts" else BUILD_FACTS)
    monkeypatch.setattr(linkprobe, "_probe_one", lambda link, *a: {"ok": link != "gre", "rtt_ms": 70.0, "loss_pct": 0.0, "error": "" if link != "gre" else "no answer", "secs": 0.1})
    r = client.post("/api/doctor/knowledge/links", json={"iran_id": ir, "foreign_id": fr})
    assert r.status_code == 200 and set(r.json()["links"]) == set(linkmodels.LINKS) and r.json()["links"]["gre"]["ok"] is False and r.json()["links"]["fou"]["ok"] is True
    row = next(p for p in client.get("/api/doctor/knowledge").json()["pairs"] if p["iran_id"] == ir)
    assert row["links"]["fou"]["ok"] is True and row["links"]["gre"]["error"] == "no answer" and set(row["links"]) == set(linkmodels.LINKS)            # the page has what the Doctor kept
    assert client.post("/api/doctor/knowledge/links", json={"iran_id": ir, "foreign_id": ir}).status_code == 400                                        # not an Iran server and a foreign one
    monkeypatch.setattr(get_settings(), "read_only", True)
    assert client.post("/api/doctor/knowledge/links", json={"iran_id": ir, "foreign_id": fr}).status_code == 403

def test_the_probe_warms_a_new_udp_flow_up_before_it_counts_the_pings():
    """Found on the test servers (2026-10-17): the first packets of a new UDP flow were lost for a few seconds on that route (10 - 45 % of the first pings), and WireGuard repeats a lost handshake only after 5 s - the
    probe, with a fixed 1.5 s and 10 pings, said WireGuard did not pass; the lab, which waited longer, got 40 % loss and then none. The script tries up to eight single pings first and then counts ten."""
    s = ssh.render_script("link_probe_ping", {"IFACE": "lp-wg", "PEER": "10.253.9.2"}) if hasattr(ssh, "render_script") else ssh._script_text("link_probe_ping", {"IFACE": "lp-wg", "PEER": "10.253.9.2"})
    first, second = s.strip().splitlines()
    assert first.startswith("for i in 1 2 3 4 5 6 7 8; do ping -c 1 -W 1 -I 'lp-wg' '10.253.9.2'") and first.endswith("&& break; done")
    assert second == "ping -c 10 -i 0.3 -W 1 -I 'lp-wg' '10.253.9.2' 2>&1 | tail -3"                               # (the same ten pings the verdict has always been made of: GOOD of 10)

    for bad in ({"IFACE": "lp-wg;id", "PEER": "10.253.9.2"}, {"IFACE": "lp-wg", "PEER": "10.253.9.2; id"}, {"IFACE": "lp-wg"}):
        with pytest.raises(ValueError):
            ssh._script_text("link_probe_ping", bad)
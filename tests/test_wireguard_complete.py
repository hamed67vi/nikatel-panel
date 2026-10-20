"""WireGuard made complete (1.69.44; the owner's request through the other session): the tools are installed only by the OWNER (a button in Settings -> About -> Tunnel cores, an item in the menu of a server, a build he starts),
every other part says "installed / missing" per server and keeps "not tested: tools missing" apart from "blocked", the link has a health check with the age of the last handshake, a restart on a stale handshake and an MTU that
counts the 80 bytes WireGuard adds, and every `wg:<transport>` builds.

What is tested here needs no server: the reading of a server's answer, the install (a faked `wg_install`: installed / already there / no apt / apt failed / no kernel module), the read-only guard, what the Doctor
remembers (untested is not failed, it is asked again, forgotten when the tools come), the probe's sentence per server, the health check script run for real against stub programs, the MTU, the handshake in the verdict
of the health check, the walk of the link models, and the whole build of the four transports against a faked pair of servers."""
import os
import shutil
import stat
import subprocess
from types import SimpleNamespace

import pytest

from nikatel.config import get_settings
from nikatel.db import session
from nikatel.models import ActionLog, Server, Tunnel
from nikatel.services import (
    autopilot,
    builder,
    engines,
    knowledge,
    linkmodels,
    linkprobe,
    onboard,
    tunnel_test,
    tunnels,
)

IRAN, FOREIGN = "203.0.113.1", "198.51.100.2"
PUB_I, PUB_F = "A" * 43 + "=", "B" * 43 + "="
NOW = 1_800_000_000


# ------------------------------------------------------------------ what a server says about WireGuard, and the install
def test_the_answer_of_a_server_is_read_as_tool_module_and_apt():
    assert engines.parse_wg("ENGINE backhaul v0.7\nWGTOOL 1\nWGMOD 1\nWGAPT 1\n") == {"tool": True, "module": True, "apt": True}
    assert engines.parse_wg("WGTOOL 0\nWGMOD 1\nWGAPT 0\nnoise\n") == {"tool": False, "module": True, "apt": False} and engines.parse_wg("") == {"tool": False, "module": False, "apt": False}


def test_one_word_says_what_each_server_needs():
    ok, none, old_kernel, no_apt = ({"tool": True, "module": True, "apt": True}, {"tool": False, "module": False, "apt": True}, {"tool": True, "module": False, "apt": True}, {"tool": False, "module": False, "apt": False})
    assert [engines.wg_state(x) for x in (ok, none, old_kernel, no_apt, None)] == ["installed", "missing", "kernel", "no apt", "unreachable"]
    s = engines.wg_summary([("ir1", "iran", ok), ("fr1", "foreign", none), ("fr2", "foreign", no_apt), ("fr3", "foreign", None)])
    assert (s["installed"], s["missing"], s["stuck"]) == (1, 1, 1) and [n["state"] for n in s["nodes"]] == ["installed", "missing", "no apt", "unreachable"]            # (a server that does not answer is counted nowhere)


def test_the_script_that_asks_the_servers_says_the_three_facts_and_the_install_is_apt_only_with_a_module_check():
    from nikatel import ssh

    ask = ssh.SCRIPTS["engine_versions"][0]
    assert 'echo "WGTOOL' in ask and 'echo "WGMOD' in ask and 'echo "WGAPT' in ask
    inst = ssh.ACTIONS["wg_install"][0]
    assert "apt-get install" in inst and "curl" not in inst and "wget" not in inst and "scp" not in inst              # (nothing is downloaded from anywhere else, nothing is copied from another machine)
    assert "WG_FAIL this server has no apt-get" in inst and "modprobe wireguard" in inst and "cannot load the wireguard module" in inst and "WG_OK installed (the kernel module is loaded)" in inst


def test_the_answer_of_the_install_action_is_a_sentence_for_the_owner():
    assert engines.wg_install_result("WG_OK present\n") == (True, "already there") and engines.wg_install_result("WG_OK installed (the kernel module is loaded)\n") == (True, "installed")
    ok, why = engines.wg_install_result("WG_FAIL this server has no apt-get: install wireguard-tools by hand\n")
    assert not ok and why == "this server has no apt-get: install wireguard-tools by hand"
    assert engines.wg_install_result("") == (False, "no answer from the server (is it on?)") and engines.wg_install_result("garbage")[0] is False


def make_servers():
    with session() as db:
        i, f = Server(name="ir-wg", host=IRAN, role="iran", online=True), Server(name="fr-wg", host=FOREIGN, role="foreign", online=True)
        db.add_all([i, f])
        db.flush()
        return i.id, f.id


def test_the_owner_installs_on_one_server_and_the_doctor_forgets_what_it_could_not_test_there(monkeypatch):
    monkeypatch.setattr(get_settings(), "read_only", False)
    iran_id, foreign_id = make_servers()
    ran = []
    answers = iter(["WG_OK installed (the kernel module is loaded)\n"])
    monkeypatch.setattr(engines, "run_action", lambda node, name, params=None, timeout=90: ran.append((node.host, name)) or next(answers))
    knowledge.record(iran_id, foreign_id, {"kind": "wg:tcpmux", "ok": False, "skipped": "tools", "error": "tools"}, "latency", NOW)
    knowledge.record_link(iran_id, foreign_id, "wg", {"ok": None, "skipped": "tools", "error": "tools"}, NOW)
    assert knowledge.verdict(knowledge.pair(iran_id, foreign_id)["kinds"]["wg:tcpmux"]) == "untested"
    assert engines.install_wireguard_on(iran_id) == "installed" and ran == [(IRAN, "wg_install")]
    p = knowledge.pair(iran_id, foreign_id)
    assert "wg:tcpmux" not in (p.get("kinds") or {}) and "wg" not in (p.get("links") or {})                          # (the next survey tests WireGuard at once)


def test_an_install_that_fails_says_why_in_the_words_of_the_server_and_changes_nothing_in_the_memory(monkeypatch):
    monkeypatch.setattr(get_settings(), "read_only", False)
    iran_id, foreign_id = make_servers()
    knowledge.record(iran_id, foreign_id, {"kind": "wg:tcpmux", "ok": False, "skipped": "tools", "error": "tools"}, "latency", NOW)
    monkeypatch.setattr(engines, "run_action", lambda node, name, params=None, timeout=90: "WG_FAIL this server has no apt-get: install wireguard-tools by hand\n")

    with pytest.raises(RuntimeError, match="ir-wg: this server has no apt-get"):
        engines.install_wireguard_on(iran_id)

    assert "wg:tcpmux" in knowledge.pair(iran_id, foreign_id)["kinds"]

    with pytest.raises(ValueError, match="server not found"):
        engines.install_wireguard_on(99999)


def test_nothing_is_installed_while_the_panel_is_read_only():
    iran_id, _ = make_servers()

    for call in (lambda: engines.install_wireguard_on(iran_id), lambda: engines.install_wireguard_start(None)):
        with pytest.raises(ValueError, match="read-only"):
            call()


def test_the_button_installs_on_every_enabled_server_and_one_failure_does_not_stop_the_others(monkeypatch):
    monkeypatch.setattr(get_settings(), "read_only", False)
    make_servers()

    def action(node, name, params=None, timeout=90):
        return "WG_OK present\n" if node.host == IRAN else "WG_FAIL apt could not install wireguard-tools: no route\n"

    monkeypatch.setattr(engines, "run_action", action)
    job = engines.install_wireguard()
    assert job["done"] and {i["name"]: i["state"] for i in job["items"]} == {"ir-wg": "ok", "fr-wg": "failed"}
    assert "no route" in next(i["detail"] for i in job["items"] if i["name"] == "fr-wg") and engines.last()["wg_job"] is job


def test_a_second_press_while_one_install_runs_is_refused(monkeypatch):
    monkeypatch.setattr(get_settings(), "read_only", False)
    monkeypatch.setitem(engines._STATE, "wg_job", {"done": False, "items": []})
    assert engines.install_wireguard_start(None) is False

    with pytest.raises(RuntimeError, match="already running"):
        engines.install_wireguard()


def test_the_api_has_one_button_and_the_cores_box_gets_the_card(client, monkeypatch):
    seen = []
    monkeypatch.setattr(engines, "install_wireguard_start", lambda ids: seen.append(ids) or True)
    assert client.post("/api/engines/wireguard/install").json()["started"] is True and client.post("/api/engines/wireguard/install?server_id=7").status_code == 202 and seen == [None, [7]]
    monkeypatch.setitem(engines._STATE, "wireguard", engines.wg_summary([("ir1", "iran", {"tool": True, "module": True, "apt": True})]))
    d = client.get("/api/engines").json()
    assert d["wireguard"]["installed"] == 1 and d["wireguard"]["nodes"][0]["state"] == "installed" and "wg_job" in d


def test_the_menu_of_a_server_installs_through_the_onboard_step_that_a_new_server_never_runs(client, monkeypatch):
    assert onboard.STEPS == ("read", "gre", "cores") and onboard.ALL_STEPS == ("read", "gre", "cores", "wg")            # (adding a server never installs a package)
    seen = []
    monkeypatch.setattr(onboard, "start", lambda sid, steps=onboard.STEPS: seen.append((sid, tuple(steps))) or True)
    sid, _ = make_servers()
    assert client.post(f"/api/servers/{sid}/onboard", json={"steps": ["wg"]}).json()["steps"] == ["wg"] and seen == [(sid, ("wg",))]
    assert client.post(f"/api/servers/{sid}/onboard", json={}).json()["steps"] == ["read", "gre", "cores"]
    assert client.post(f"/api/servers/{sid}/onboard", json={"steps": ["apt"]}).status_code == 400


def test_the_onboard_step_installs_and_says_how_it_ended(monkeypatch):
    sid, _ = make_servers()
    told = []
    monkeypatch.setattr(onboard, "_say", lambda server_id, what, detail="": told.append(what))
    monkeypatch.setattr(onboard.nodes, "_set_extra", lambda *a, **k: None)
    monkeypatch.setattr(engines, "install_wireguard_on", lambda server_id: "installed")
    assert onboard.run(sid, ("wg",)) == {"wg": "ok"} and told == ["Installing WireGuard tools"]

    def fail(server_id):
        raise RuntimeError("ir-wg: this server has no apt-get")

    monkeypatch.setattr(engines, "install_wireguard_on", fail)
    assert onboard.run(sid, ("wg",))["wg"].startswith("failed: ir-wg: this server has no apt-get")


# ------------------------------------------------------------------ "not tested: tools missing" is not "blocked"
def test_a_kind_that_could_not_be_tried_is_untested_not_failed_and_is_asked_again_after_a_day():
    e = knowledge.merge(None, {"kind": "wg:tcpmux", "ok": False, "skipped": "tools", "error": "wireguard-tools: MISSING on the Iran server"}, "latency", NOW)
    assert knowledge.verdict(e) == "untested" and e["last"]["skipped"] == "tools" and "full" not in e
    p = {"kinds": {"wg:tcpmux": e}}
    assert knowledge.blocked(p, NOW) == [] and knowledge.poor(p, NOW) == [] and knowledge.ranked(p, NOW) == [] and knowledge.ranked(p, NOW, include_poor=True) == []
    assert knowledge.due_kinds(p, ["wg:tcpmux"], NOW + 3600) == [] and knowledge.due_kinds(p, ["wg:tcpmux"], NOW + knowledge.TOOLS_RETRY) == ["wg:tcpmux"]            # a failure waits 14 days
    f = knowledge.merge(None, {"kind": "wg:tcpmux", "ok": False, "error": "no data"}, "latency", NOW)
    assert knowledge.blocked({"kinds": {"wg:tcpmux": f}}, NOW) == ["wg:tcpmux"] and knowledge.due_kinds({"kinds": {"wg:tcpmux": f}}, ["wg:tcpmux"], NOW + knowledge.TOOLS_RETRY) == []
    assert knowledge.untested(p) == [{"kind": "wg:tcpmux", "reason": "tools", "error": "wireguard-tools: MISSING on the Iran server", "at": NOW}] and knowledge.untested({"kinds": {"wg:tcpmux": f}}) == []


def test_a_later_good_test_replaces_the_untested_mark():
    e = knowledge.merge(None, {"kind": "wg:tcpmux", "ok": False, "skipped": "tools"}, "latency", NOW)
    e = knowledge.merge(e, {"kind": "wg:tcpmux", "ok": True, "score": 70, "latency_ms": 90.0}, "latency", NOW + 100)
    assert knowledge.verdict(e) == "good" and "skipped" not in e["last"]


def test_the_page_lists_the_untested_kinds_and_they_are_not_in_the_blocked_list():
    iran_id, foreign_id = make_servers()
    knowledge.record(iran_id, foreign_id, {"kind": "wg:tcpmux", "ok": False, "skipped": "tools", "error": "tools missing"}, "latency", NOW)
    knowledge.record(iran_id, foreign_id, {"kind": "bp:tcp", "ok": False, "error": "no data"}, "latency", NOW)
    row = knowledge.state(NOW + 60)["pairs"][0]
    assert [b["kind"] for b in row["blocked"]] == ["bp:tcp"] and [u["kind"] for u in row["untested"]] == ["wg:tcpmux"] and row["untested"][0]["reason"] == "tools"


def test_the_link_probe_remembers_that_it_could_not_try_a_link_and_that_is_not_a_no():
    iran_id, foreign_id = make_servers()
    knowledge.record_link(iran_id, foreign_id, "wg", {"ok": None, "skipped": "tools", "error": "tools"}, NOW)
    p = knowledge.pair(iran_id, foreign_id)
    assert p["links"]["wg"]["ok"] is None and p["links"]["wg"]["skipped"] == "tools" and knowledge.link_state(p, "wg", NOW + 60) is None          # (not False: it does not pass - it was not tried)
    knowledge.record_link(iran_id, foreign_id, "fou", {"ok": False, "error": "0 of 10"}, NOW)
    assert knowledge.link_state(knowledge.pair(iran_id, foreign_id), "fou", NOW + 60) is False


def test_a_build_that_stopped_for_missing_tools_is_recognised_in_its_story_and_a_real_failure_is_not():
    assert tunnel_test.tools_missing(["x", "Stopped: Iran server ir1: wireguard-tools is not installed (Servers -> its menu ...). Everything created was removed again."])
    assert not tunnel_test.tools_missing(["Stopped: the backhaul client did not connect"]) and not tunnel_test.tools_missing([]) and not tunnel_test.tools_missing(None)


def test_a_pair_test_of_wireguard_without_the_tools_is_recorded_as_untested(monkeypatch):
    iran_id, foreign_id = make_servers()

    with session() as db:
        e = ActionLog(ref="test-1", action="tunnel_test", source="manual", story=["Temporary wg:tcpmux tunnel test-1 is being built", "Build failed: Stopped: Foreign server fr-wg: wireguard-tools is not installed (Servers -> its menu -> Install -> WireGuard tools). Everything created was removed again."],
                      ok=False, finished_at=NOW)
        db.add(e)
        db.flush()
        aid = e.id

    monkeypatch.setattr(tunnel_test, "start", lambda *a, **k: aid)
    monkeypatch.setattr(tunnel_test.time, "sleep", lambda s: None)
    job = {"id": "cmpx", "iran_id": iran_id, "foreign_id": foreign_id, "measure": "latency", "source": "survey", "kinds": ["wg:tcpmux"], "results": [], "current": "", "current_aid": None, "done": False, "stop": False, "started": NOW, "install_wg": False}
    tunnel_test._compare_worker(job)
    r = job["results"][0]
    assert r["ok"] is False and r["skipped"] == "tools" and "not tested" in r["error"]
    assert knowledge.verdict(knowledge.pair(iran_id, foreign_id)["kinds"]["wg:tcpmux"]) == "untested" and knowledge.blocked(knowledge.pair(iran_id, foreign_id), int(NOW)) == []


# ------------------------------------------------------------------ the probe says installed / missing per server
def facts(**kw):
    base = {"wg_mod": True, "wg_tool": True, "fou_mod": True, "udp": set(), "fou_ports": set(), "apt": True, "mods": {"ip_gre", "wireguard"}, "ipip_pairs": set(), "tcp": {22}, "chisel": True}
    base.update(kw)
    return base


def test_the_probe_names_the_server_that_lacks_the_tools_and_marks_the_answer_as_not_tried():
    p = linkprobe.plan(["wg", "wgchisel"], facts(wg_tool=False), facts(), [], (IRAN, FOREIGN))
    skip = p["wg"]["skip"]
    assert skip["ok"] is None and skip["skipped"] == "tools" and skip["tools"] == {"iran": False, "foreign": True}
    assert "installed on the foreign server" in skip["error"] and "MISSING on the Iran server" in skip["error"] and "Install -> WireGuard tools" in skip["error"] and "never installs a package" in skip["error"]
    assert p["wgchisel"]["skip"]["skipped"] == "tools"
    both = linkprobe.plan(["wg"], facts(wg_tool=False), facts(wg_tool=False), [], (IRAN, FOREIGN))["wg"]["skip"]
    assert both["tools"] == {"iran": False, "foreign": False} and both["error"].count("MISSING") == 2
    assert "wg" in linkprobe.plan(["wg"], facts(), facts(), [], (IRAN, FOREIGN)) and "skip" not in linkprobe.plan(["wg"], facts(), facts(), [], (IRAN, FOREIGN))["wg"]


def test_the_summary_says_wireguard_was_not_known_because_of_the_tools():
    s = linkprobe.summary({"fou": {"ok": True, "rtt_ms": 40.0}, "wg": {"ok": None, "skipped": "tools"}, "ipip": {"ok": None}})
    assert s == "passes: GRE over UDP (FOU) 40 ms; does not pass: nothing; not known: WireGuard (wireguard-tools missing), IPIP"


def test_the_doctor_leaves_out_a_link_the_probe_could_not_try_because_of_the_tools(monkeypatch):
    said = []
    monkeypatch.setattr(autopilot, "_say", lambda aid, line, **k: said.append(line))
    monkeypatch.setattr(linkprobe, "probe", lambda iran_id, foreign_id, links, record=True: {"fou": {"ok": True, "rtt_ms": 40.0}, "wg": {"ok": None, "skipped": "tools", "error": "tools"}, "ipip": {"ok": False, "error": "0 of 10"}})
    keep = autopilot._probe_links(1, {"iran_id": 1, "foreign_id": 2}, ["fou:tcpmux", "wg:tcpmux", "ipip:tcpmux", "bp:tcp"])
    assert keep == ["fou:tcpmux", "bp:tcp"] and any("2 kind(s) left out" in s for s in said)


# ------------------------------------------------------------------ the link's own health
def test_the_check_script_of_a_wireguard_link_counts_the_age_of_the_handshake_and_is_valid_shell(tmp_path):
    for link in ("wg", "wgchisel"):
        f = builder.link_scripts(link, "ca-wg1", IRAN, FOREIGN, 1, "10.9.8.1/30", 1360 if link == "wg" else 8000, port=51830 if link == "wg" else 52005, peer_pub=PUB_F,
                                 carrier=None if link == "wg" else {"role": "iran", "token": "ab" * 24, "fp": ""})
        chk = f["/usr/local/sbin/nikatel-gre-check-ca-wg1"]
        assert chk.startswith("#!/bin/sh\n# nikatel-gre-check-script") and "wg show" in chk and "latest-handshakes" in chk and "PEER=10.9.8.2" in chk and "NEED=3" in chk and "NEED=2" in chk and "private" not in chk
        p = tmp_path / ("chk-" + link)
        p.write_text(chk)
        assert subprocess.run(["sh", "-n", str(p)], capture_output=True, text=True, check=False).returncode == 0

    plain = builder.link_scripts("fou", "ca-wg1", IRAN, FOREIGN, 1, "10.9.8.1/30", 1360, port=5510)["/usr/local/sbin/nikatel-gre-check-ca-wg1"]
    assert "latest-handshakes" not in plain                                                                              # (the check of the other links is what it was)


@pytest.mark.skipif(shutil.which("sh") is None, reason="no sh")
@pytest.mark.parametrize("handshake_age,fails_to_restart", [(10, 3), (1000, 2), (None, 2)])
def test_the_check_script_restarts_a_link_after_three_misses_or_after_two_when_the_handshake_is_old(tmp_path, handshake_age, fails_to_restart):
    """The real script, against stub programs: ping never answers, `wg` says how old the handshake is, `systemctl` notes a restart."""
    bin_, run = tmp_path / "bin", tmp_path / "run"
    bin_.mkdir()
    run.mkdir()
    now = 1_900_000_000

    def stub(name, body):
        p = bin_ / name
        p.write_text("#!/bin/sh\n" + body + "\n")
        p.chmod(p.stat().st_mode | stat.S_IEXEC)

    stub("ping", "exit 1")
    stub("date", f"echo {now}")
    stub("logger", "exit 0")
    stub("systemctl", f"echo \"$@\" >> {run}/restarts")
    stub("wg", f"echo \"wgpub {'0' if handshake_age is None else now - handshake_age}\"")
    stub("up", "exit 0")
    script = builder.link_scripts("wg", "ca-wg1", IRAN, FOREIGN, 1, "10.9.8.1/30", 1360, port=51830, peer_pub=PUB_F)["/usr/local/sbin/nikatel-gre-check-ca-wg1"]
    script = script.replace("/usr/local/sbin/nikatel-gre-up-$NAME", str(bin_ / "up")).replace("/run/", str(run) + "/")
    f = tmp_path / "check.sh"
    f.write_text(script)
    env = {**os.environ, "PATH": f"{bin_}:{os.environ['PATH']}"}

    for n in range(1, fails_to_restart + 1):
        subprocess.run(["sh", str(f)], env=env, capture_output=True, check=True)
        restarted = (run / "restarts").exists()
        assert restarted == (n == fails_to_restart), (handshake_age, n)

    assert (run / "restarts").read_text().strip() == "restart nikatel-gre-ca-wg1.service" and not (run / "nikatel-gre-fail-ca-wg1").exists()


def test_a_link_that_answers_the_ping_is_never_restarted(tmp_path):
    bin_, run = tmp_path / "bin", tmp_path / "run"
    bin_.mkdir()
    run.mkdir()

    for name, body in (("ping", "exit 0"), ("systemctl", f"echo restart >> {run}/restarts"), ("up", "exit 0")):
        (bin_ / name).write_text("#!/bin/sh\n" + body + "\n")
        (bin_ / name).chmod(0o755)

    script = builder.link_scripts("wg", "ca-wg1", IRAN, FOREIGN, 1, "10.9.8.1/30", 1360, port=51830, peer_pub=PUB_F)["/usr/local/sbin/nikatel-gre-check-ca-wg1"]
    (tmp_path / "c.sh").write_text(script.replace("/usr/local/sbin/nikatel-gre-up-$NAME", str(bin_ / "up")).replace("/run/", str(run) + "/"))
    (run / "nikatel-gre-fail-ca-wg1").write_text("2")
    subprocess.run(["sh", str(tmp_path / "c.sh")], env={**os.environ, "PATH": f"{bin_}:{os.environ['PATH']}"}, capture_output=True, check=True)
    assert not (run / "restarts").exists() and not (run / "nikatel-gre-fail-ca-wg1").exists()               # (the count of misses starts again)


def test_the_private_key_is_made_on_its_server_with_0600_and_never_travels():
    from nikatel import ssh

    keygen = ssh.ACTIONS["wg_keygen"][0]
    assert "umask 077" in keygen and "chmod 700" in keygen and "chmod 600" in keygen and "wg genkey >" in keygen and "wg pubkey <" in keygen and keygen.count("PUB=") == 1       # the public half is the only thing printed
    f = builder.link_scripts("wg", "ca-wg1", IRAN, FOREIGN, 1, "10.9.8.1/30", 1360, port=51830, peer_pub=PUB_F)
    assert all("PrivateKey" not in t and "wg genkey" not in t for t in f.values()) and "private-key \"$KEY\"" in f["/usr/local/sbin/nikatel-gre-up-ca-wg1"]       # the script names the FILE
    assert "/etc/nikatel/wg/$n.key" in ssh.ACTIONS["remove_tunnel_files"][0] and "/etc/nikatel/wg/lp-*.key" in ssh.ACTIONS["purge_test_leftovers"][0]


def test_the_mtu_counts_what_the_link_adds_to_every_packet():
    assert linkmodels.OVERHEAD["wg"] == 80
    assert linkmodels.inner_mtu("wg", 1472, 1472) == 1360 and linkmodels.inner_mtu("gre", 1472, 1472) == 1360          # a path of 1500 bytes: the cap decides, as it always did
    assert linkmodels.inner_mtu("wg", 1372, 1472) == 1320                                                              # a path of 1400: 1400 - 80 (it was 1360: the packets were cut in two)
    assert linkmodels.inner_mtu("gre", 1372, 1472) == 1360 and linkmodels.inner_mtu("ipsecnat", 1372, 1372) == 1316
    assert linkmodels.inner_mtu("wg", 0, 0) == 1348 and linkmodels.inner_mtu("wg", 900, 900) == 1200                    # unknown: a path of 1428; never under 1200
    assert all(linkmodels.inner_mtu(k, 1472, 1472) + linkmodels.OVERHEAD[k] <= 1500 for k in linkmodels.LINKS)


def test_the_health_check_reads_the_handshake_and_says_so_when_it_is_old():
    side = tunnels.parse_side("UNIT x active\nIFACE ca-wg1 UP 10 20\nWGHS 12\nPEER 10.9.8.2 1.5 0\n")
    assert side.wg_age == 12 and tunnels.wg_note(side) == ""
    assert tunnels.parse_side("WGHS never\n").wg_age == -1 and tunnels.parse_side("UNIT x active\n").wg_age is None
    old = tunnels.parse_side("WGHS 4000\n")
    assert "last WireGuard handshake was 4000 s ago" in tunnels.wg_note(old) and "has not shaken hands" in tunnels.wg_note(tunnels.parse_side("WGHS never\n")) and tunnels.wg_note(tunnels.Side()) == ""
    plan = tunnels.Plan(["a.service"], ["a.service"], iface="ca-wg1", iran_peer="10.9.8.2")
    ok = tunnels.Side(units={"a.service": "active"}, iface_state="UP", loss_pct=100.0, wg_age=4000)
    st, why = tunnels.judge(plan, ok, tunnels.Side(units={"a.service": "active"}, iface_state="UP"))
    assert st == "down" and why.startswith("no answer from the foreign end over the GRE link") and "4000 s ago" in why
    lossy = tunnels.Side(units={"a.service": "active"}, iface_state="UP", loss_pct=40.0, wg_age=-1, data_code="200")
    assert "40% loss on the GRE link; WireGuard has not shaken hands" in tunnels.judge(plan, lossy, tunnels.Side(units={"a.service": "active"}))[1]
    plain = tunnels.Side(units={"a.service": "active"}, iface_state="UP", loss_pct=100.0)
    assert tunnels.judge(plan, plain, tunnels.Side(units={"a.service": "active"}, iface_state="UP"))[1] == "no answer from the foreign end over the GRE link"          # (a link with no handshake in its answer is judged as before)


def test_the_side_script_asks_for_the_handshake_only_of_a_wireguard_card():
    from nikatel import ssh

    s = ssh.SCRIPTS["tunnel_side"][0]
    assert "WGHS" in s and "grep -q wireguard" in s and "latest-handshakes" in s and s.index("WGHS") < s.index('if [ "@QUICK@" = 1 ]')          # (also in the quick look of the sentinel)


# ------------------------------------------------------------------ the walk of the link models uses WireGuard where it is already there
@pytest.fixture
def worker(monkeypatch):
    def run(bad, ready, install_wg=False):
        b = builder.Build(9001, SimpleNamespace(id=1, name="s1", host=IRAN), SimpleNamespace(id=2, name="s2", host=FOREIGN), [], "tcpmux", opts={"install_wg": True} if install_wg else {}, name="t-fb", link_fallback=True)
        built = []

        def fake_build(bb):
            link = str(bb.opts.get("link") or "gre")
            built.append(link)

            if link in bad:
                raise builder.LinkFailed(f"{link} does not pass")

        monkeypatch.setattr(builder, "_build", fake_build)
        monkeypatch.setattr(builder, "_say", lambda *a, **k: None)
        monkeypatch.setattr(builder, "_rollback", lambda bb: None)
        monkeypatch.setattr(builder, "_wg_ready", lambda bb: ready)
        builder._BUILDING.add("pair-wg")
        builder._build_worker(b, "pair-wg")
        return built

    return run


def test_a_build_of_the_owner_falls_back_to_wireguard_when_the_tools_are_already_on_both_servers_without_asking_for_an_install(worker):
    plain = [k for k in linkmodels.LINKS if k != "wg"]
    assert worker(set(plain), ready=True)[-1] == "wg"                                                                  # (nothing is installed: the tools are there)
    assert worker(set(plain), ready=False)[-1] != "wg" and "wg" not in worker(set(plain), ready=False)                  # not there, and no install asked: it is not tried
    assert worker(set(plain), ready=False, install_wg=True)[-1] == "wg"


def test_the_tools_are_ready_only_when_both_servers_have_the_tool_and_the_module(monkeypatch):
    facts_by = {IRAN: "WG_MOD=1\nWG_TOOL=1\n", FOREIGN: "WG_MOD=1\nWG_TOOL=1\n"}
    monkeypatch.setattr(builder, "run_script", lambda node, name, params=None, timeout=40: facts_by[node.host])
    b = SimpleNamespace(iran=Server(id=1, name="i", host=IRAN, ssh_user="root", ssh_port=22), foreign=Server(id=2, name="f", host=FOREIGN, ssh_user="root", ssh_port=22))
    assert builder._wg_ready(b) is True
    facts_by[FOREIGN] = "WG_MOD=1\nWG_TOOL=0\n"
    assert builder._wg_ready(b) is False
    facts_by[FOREIGN] = "WG_MOD=0\nWG_TOOL=1\n"
    assert builder._wg_ready(b) is False

    def boom(node, name, params=None, timeout=40):
        raise builder.SSHError("no route")

    monkeypatch.setattr(builder, "run_script", boom)
    assert builder._wg_ready(b) is False


# ------------------------------------------------------------------ every wg:<transport> builds, and goes away
class Pair:
    def __init__(self, monkeypatch, path_payload=1472):
        from nikatel.services import engines as eng

        self.calls, self.puts, self.said, self.peer = [], {}, [], {}
        self.path_payload = path_payload
        monkeypatch.setattr(builder, "run_script", self.script)
        monkeypatch.setattr(builder, "run_action", self.action)
        monkeypatch.setattr(builder, "_put", lambda node, path, text, mode: self.puts.__setitem__((node.host, path), (text, mode)))
        monkeypatch.setattr(builder, "_unit", lambda node, unit: "active")
        monkeypatch.setattr(builder, "ensure_backhaul", lambda *a, **k: None)
        monkeypatch.setattr(builder, "check_tunnel", lambda tid: None)
        monkeypatch.setattr(builder.time, "sleep", lambda s: None)
        monkeypatch.setattr(eng, "ensure_on_node", lambda *a, **k: False)
        monkeypatch.setattr(builder, "_say", lambda aid, line, done=None, ok=None: self.said.append(line))

    def script(self, node, name, params=None, timeout=40):
        params = params or {}
        self.calls.append((node.host, name))

        if name == "build_facts":
            return f"IPS={node.host}/24\nLINKS=\nLISTEN=22\nUNITS=\nBROKEN=\nGRE_MOD=1\nBACKHAUL=v0.7\nIPTABLES=1\n"
        if name == "link_facts":
            return f"WG_MOD=1\nWG_TOOL={0 if getattr(self, 'no_tool', False) else 1}\nFOU_MOD=1\nUDP=\nFOU_PORTS=\nAPT=1\nMODS=ip_gre wireguard\nIPIP_PAIRS=\nTCP=22\nCHISEL=1\n"
        if name == "mtu_probe":
            return f"MTU={self.path_payload}\n"
        if name == "link_probe_ping":
            self.peer[node.host] = params["PEER"]
            return "10 packets transmitted, 10 received, 0% packet loss\nrtt min/avg/max/mdev = 40.0/41.5/43.0/1.0 ms\n"
        if name == "gre_hold":
            return "20 packets transmitted, 20 received, 0% packet loss\n"
        if name == "estab_check":
            return f"ESTAB 0 0 x:9900 {self.peer.get(IRAN, '')}:41000\n"
        if name == "http_probe":
            return "200"
        if name == "tunnel_side":
            return "".join(f"UNIT {u} active\n" for u in params["UNITS"].split())
        return ""

    def action(self, node, name, params=None, timeout=90):
        self.calls.append((node.host, name))
        return {"wg_keygen": f"PUB={PUB_I if node.host == IRAN else PUB_F}\n", "sysctl_tune": "tuned\n", "data_test_server": "started\n"}.get(name, "")


def make_build(transport, opts, name="test-8"):
    with session() as db:
        i, f = Server(name="ir-b", host=IRAN, role="iran", online=True), Server(name="fr-b", host=FOREIGN, role="foreign", online=True)
        db.add_all([i, f])
        db.flush()
        db.expunge(i)
        db.expunge(f)

    return builder.Build(78, i, f, builder.parse_ports("8075"), transport, 0, 75, True, opts=opts, name=name)


@pytest.mark.parametrize("transport", ["tcpmux", "tcp", "wsmux", "wssmux"])
def test_every_backhaul_transport_builds_over_wireguard_with_a_key_on_each_server_and_the_health_check_of_a_wireguard_link(monkeypatch, transport):
    pair = Pair(monkeypatch)
    b = make_build(transport, {"link": "wg"})
    builder._build(b)
    assert [c for c in pair.calls if c[1] == "wg_keygen"] == [(IRAN, "wg_keygen"), (FOREIGN, "wg_keygen")] and "mtu_probe" in [c[1] for c in pair.calls]            # (a plain WireGuard link measures the path)
    assert all("wg_install" != c[1] for c in pair.calls)                                                                   # the tools are there: nothing is installed
    check = pair.puts[(IRAN, "/usr/local/sbin/nikatel-gre-check-test-8")][0]
    assert "latest-handshakes" in check and "latest-handshakes" in pair.puts[(FOREIGN, "/usr/local/sbin/nikatel-gre-check-test-8")][0]
    assert all("PrivateKey" not in text and "wg genkey" not in text for (_h, _p), (text, _m) in pair.puts.items())

    if transport in ("wssmux", "wss"):
        assert "backhaul_tls" in [c[1] for c in pair.calls]

    with session() as db:
        t = db.query(Tunnel).filter(Tunnel.name == "test-8").one()
        rec = dict(t.record)
        assert t.transport == transport and rec["link"] == "wg" and 51820 <= rec["link_port"] <= 51919 and rec["wg"] == {"iran_pub": PUB_I, "foreign_pub": PUB_F} and rec["mtu"] == 1360
        assert t.secrets["gre_key"] and "private" not in str(rec).lower()


def test_a_path_of_1400_bytes_gets_a_wireguard_mtu_that_fits_and_says_why(monkeypatch):
    pair = Pair(monkeypatch, path_payload=1372)
    b = make_build("tcpmux", {"link": "wg"})
    builder._build(b)
    assert b.mtu == 1320 and any("→ WireGuard MTU 1320 (the link adds up to 80 bytes)" in s for s in pair.said)
    unit_up = pair.puts[(IRAN, "/usr/local/sbin/nikatel-gre-up-test-8")][0]
    assert "MTU=1320" in unit_up


def test_the_removal_of_a_wireguard_tunnel_asks_both_servers_and_a_rebuild_keeps_the_link(monkeypatch):
    monkeypatch.setattr(get_settings(), "read_only", False)

    with session() as db:
        i, f = Server(name="ir-d", host=IRAN, role="iran", online=True), Server(name="fr-d", host=FOREIGN, role="foreign", online=True)
        db.add_all([i, f])
        db.flush()
        t = Tunnel(name="fr-d-ir-d", type="backhaul", transport="tcpmux", iran_server_id=i.id, foreign_server_id=f.id, port=9905, forwarded_ports=[8075],
                   record={"link": "wg", "link_port": 51830, "network": "10.9.8.0/30", "mtu": 1320, "keepalive": 75, "opts": {"link": "wg", "install_wg": True, "tune": True}, "port_mapping": "8075"})
        db.add(t)
        db.flush()
        tid = t.id

    seen = {}
    monkeypatch.setattr(builder, "pending_put", lambda item: seen.setdefault("pending", item))
    removed = []
    monkeypatch.setattr(builder, "run_action", lambda node, name, params=None, timeout=90: removed.append((node.host, name, dict(params or {}))) or "REMOVED")
    monkeypatch.setattr(builder, "start_build", lambda iran_id, foreign_id, ports, transport, mtu, keepalive, forced_name="", nodelay=True, opts=None: seen.update(opts=dict(opts or {}), name=forced_name, mtu=mtu) or 5)
    assert builder.rebuild_tunnel(tid) == 5
    assert {(h, n) for h, n, _ in removed} >= {(IRAN, "remove_tunnel_files"), (FOREIGN, "remove_tunnel_files")} and all(p["NAME"] == "fr-d-ir-d" for _, n, p in removed if n == "remove_tunnel_files")
    assert seen["opts"]["link"] == "wg" and seen["opts"]["install_wg"] is True and seen["mtu"] == 1320 and seen["name"] == "fr-d-ir-d"            # (the same link, the same MTU; the owner's consent to the tools stays with the tunnel)
    assert autopilot.kind_of("backhaul", "tcpmux", {"link": "wg"}) == "wg:tcpmux"


def test_a_standby_over_wireguard_without_the_tools_fails_at_once_and_installs_nothing(monkeypatch):
    pair = Pair(monkeypatch)
    pair.no_tool = True
    b = make_build("tcpmux", {"link": "wg"}, name="sb-test")
    b.standby_of = "main-tunnel"

    with pytest.raises(builder.LinkFailed, match="wireguard-tools is not installed"):
        builder._build(b)

    assert not any(c[1] == "wg_install" for c in pair.calls)

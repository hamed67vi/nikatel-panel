"""The automatic type goes on to the next type when a build ends badly, a tunnel that carries no download is not ready, and a tunnel that works gets a suggestion when a better type is known for its pair
(the owner, 2026-10-20: "it set these types - are they the best? it must test whether a better and more stable one exists"; "it chose Paqet and the tunnel carried nothing")."""
import time

from nikatel.db import session
from nikatel.models import ActionLog, Server
from nikatel.services import (
    build_other,
    builder,
    create,
    doctor,
    knowledge,
    license,
    tunnel_test,
)

NOW = 1_800_000_000


def _pair(kinds, now=NOW):
    """A memory of a pair: {kind: (down_mbps, loss, score[, ping ms])} as FULL measurements made a day before `now`."""
    return {"kinds": {k: {"full": {"score": v[2], "down_mbps": v[0], "loss_pct": v[1], "latency_ms": v[3] if len(v) > 3 else 80.0, "at": now - 86400}, "last": {"ok": True, "at": now - 86400}} for k, v in kinds.items()}}


def test_a_better_type_is_one_that_carried_three_times_as_much_and_100_mbps_more_and_was_stable():
    p = _pair({"tcpmux": (160, 0, 62), "bp:tcpmux": (1293, 0, 95), "tcp": (2218, 0, 94), "bp:wssmux": (1059, 4, 94), "wsmux": (300, 0, 60), "chisel:tls": (700, 0, 50, 230)})
    rows = knowledge.upgrade_kinds(p, NOW, "tcpmux")
    assert [r["kind"] for r in rows] == ["tcp", "bp:tcpmux"]                      # (the fastest first; 1059 lost 4 %: not stable; 300 is not 3 times; chisel scored worse than the running type)
    assert knowledge.upgrade_kinds(p, NOW, "tcpmux", allowed=lambda k: k != "tcp")[0]["kind"] == "bp:tcpmux"
    assert knowledge.upgrade_kinds(p, NOW, "tcp") == []                           # (the best one has nothing better)
    assert knowledge.upgrade_kinds(p, NOW, "paqet") == []                         # (the running type was never measured on this pair: nothing to compare with)
    assert knowledge.upgrade_kinds(_pair({"tcpmux": (40, 0, 60), "tcp": (110, 0, 70)}), NOW, "tcpmux") == []      # (110 is 2.75 times and only 70 Mb/s more)
    assert knowledge.upgrade_kinds(p, NOW + 20 * 86400, "tcpmux") == []           # (a number of three weeks ago says little about today's route)


def test_the_best_kinds_are_the_good_ones_then_the_weak_ones_by_what_they_carried():
    with session() as db:
        i, f = Server(name="ir-bk", host="203.0.113.72", role="iran", online=True), Server(name="fr-bk", host="198.51.100.72", role="foreign", online=True)
        db.add_all([i, f])
        db.flush()
        ir, fr = i.id, f.id

    picks, why = knowledge.best_kinds(ir, fr, limit=3)
    assert picks and why == "" and picks[0][1].endswith("first choice for a new pair")           # (nothing measured: the Doctor's first choice for a new pair)
    kinds = [k for k, _ in picks]
    assert kinds[0] == "tcpmux" and sum(k in knowledge.GRE_KINDS for k in kinds) == 1 and kinds[1:] == ["bp:wssmux", "chisel:tls"]          # ONE GRE kind at most: a filtered GRE must not burn every try (2026-10-23, a real pair)
    assert knowledge.best_kinds(ir, fr, allowed=lambda k: False)[0] == [] and "the licence" in knowledge.best_kinds(ir, fr, allowed=lambda k: False)[1]
    assert [k for k, _ in knowledge.best_kinds(ir, fr, allowed=lambda k: k not in knowledge.GRE_KINDS, limit=2)[0]] == ["bp:wssmux", "chisel:tls"]


def _finished(ok, line):
    with session() as db:
        e = ActionLog(ref="b", action="build", source="manual", story=[line], ok=ok, finished_at=int(time.time()))
        db.add(e)
        db.flush()
        return e.id


def test_a_build_that_ends_badly_goes_on_to_the_next_best_type_before_the_next_iran_server(client, monkeypatch):
    from nikatel.config import get_settings

    monkeypatch.setattr(get_settings(), "read_only", False)
    monkeypatch.setattr(create, "_POLL", 0.05)

    with session() as db:
        ir, fr = Server(name="ir-af", host="203.0.113.71", role="iran", online=True), Server(name="fr-af", host="198.51.100.71", role="foreign", online=True)
        db.add_all([ir, fr])
        db.flush()
        ir_id, fr_id = ir.id, fr.id

    tried = []
    monkeypatch.setattr(builder, "start_build", lambda iran_id, foreign_id, ports, transport, mtu=0, keepalive=75, forced_name="", nodelay=True, opts=None:
                        tried.append(("backhaul", transport, dict(opts or {}))) or _finished(False, "Stopped: the tunnel carries no download"))
    monkeypatch.setattr(build_other, "start_build", lambda iran_id, foreign_id, ports, kind, transport="", opts=None, forced_name="", measure="full", standby_of="":
                        tried.append((kind, transport, dict(opts or {}))) or _finished(True, "Tunnel is ready."))
    aid = create.start(ir_id, fr_id, "9075", "backhaul", "tcpmux", {}, [], False, fallback_kinds=["fou:tcp", "bp:wssmux"])
    s = None

    for _ in range(120):
        s = client.get(f"/api/actions/{aid}").json()

        if s["done"]:
            break

        time.sleep(0.1)

    text = "\n".join(s["story"])
    assert s["ok"] is True and tried == [("backhaul", "tcpmux", {}), ("backhaul", "tcp", {"link": "fou"}), ("backpack", "wssmux", {})]
    assert "backhaul · tcpmux did not work - trying the next type" in text and "Trying backpack · wssmux" in text


def test_a_build_that_found_gre_filtered_teaches_the_pair_so_no_choice_tries_a_gre_kind_again():
    """Seen for real (2026-10-23): a Trident's tunnel failed on GRE, then its standby tried tcpmux and wssmux again, a minute each, with growing waits."""
    from nikatel.services import standby

    with session() as db:
        i, f = Server(name="ir-gt", host="203.0.113.74", role="iran", online=True), Server(name="fr-gt", host="198.51.100.74", role="foreign", online=True)
        db.add_all([i, f])
        db.flush()
        ir, fr = i.id, f.id

    now = int(time.time())
    knowledge.note_gre_filtered(ir, fr, "GRE (protocol 47) is probably filtered between these two servers", now=now)
    p = knowledge.pair(ir, fr)
    assert sorted(knowledge.blocked(p, now)) == sorted(knowledge.GRE_KINDS) and "probably filtered" in p["kinds"]["tcpmux"]["last"]["error"]
    kinds = [k for k, _ in knowledge.best_kinds(ir, fr, limit=3)[0]]
    assert kinds and not any(k in knowledge.GRE_KINDS for k in kinds)                                                            # Best of the day: no GRE kind at all now
    plan, _ = knowledge.plan_for(ir, fr, "bp:wssmux", False, False, now=now)
    chosen, _note = standby.choose_kind(plan, "bp:wssmux", p, now)
    assert chosen and chosen not in knowledge.GRE_KINDS and chosen.split(":")[0] != "bp"                                        # the standby: another carrier, and never a GRE kind
    assert knowledge.blocked(p, now + knowledge.FAIL_FRESH + 1) == []                                                            # (two weeks later it may be tried again)


def test_when_gre_is_filtered_the_other_gre_kinds_are_left_out_of_the_tries(client, monkeypatch):
    """Seen for real (2026-10-23): Best of the day tried tcpmux, wssmux and tcp on a pair where GRE is filtered - three tries, one cause, nothing built."""
    from nikatel.config import get_settings

    monkeypatch.setattr(get_settings(), "read_only", False)
    monkeypatch.setattr(create, "_POLL", 0.05)

    with session() as db:
        ir, fr = Server(name="ir-gf", host="203.0.113.73", role="iran", online=True), Server(name="fr-gf", host="198.51.100.73", role="foreign", online=True)
        db.add_all([ir, fr])
        db.flush()
        ir_id, fr_id = ir.id, fr.id

    tried = []
    monkeypatch.setattr(builder, "start_build", lambda iran_id, foreign_id, ports, transport, mtu=0, keepalive=75, forced_name="", nodelay=True, opts=None, hidden_key="standby_of":
                        tried.append(("backhaul", transport, dict(opts or {}))) or _finished(False, "Stopped: the GRE link is up but the inner addresses cannot ping each other (Iran→foreign 0/10, foreign→Iran 0/10). GRE (protocol 47) is probably filtered between these two servers."))
    monkeypatch.setattr(build_other, "start_build", lambda iran_id, foreign_id, ports, kind, transport="", opts=None, forced_name="", measure="full", standby_of="", hidden_key="standby_of":
                        tried.append((kind, transport, dict(opts or {}))) or _finished(True, "Tunnel is ready."))
    aid = create.start(ir_id, fr_id, "9076", "backhaul", "tcpmux", {}, [], False, fallback_kinds=["wssmux", "tcp", "fou:tcpmux", "bp:wssmux"])
    s = None

    for _ in range(120):
        s = client.get(f"/api/actions/{aid}").json()

        if s["done"]:
            break

        time.sleep(0.1)

    text = "\n".join(s["story"])
    assert s["ok"] is True and tried == [("backhaul", "tcpmux", {}), ("backhaul", "tcpmux", {"link": "fou"}), ("backpack", "wssmux", {})]          # wssmux and tcp (GRE too) were left out; a link that is not GRE is still tried
    assert "GRE is filtered between these two servers: the other GRE kinds are left out (backhaul · wssmux, backhaul · tcp)" in text


def test_the_arguments_of_a_build_follow_from_a_key_of_the_pair_test():
    assert tunnel_test.build_args("tcpmux") == ("backhaul", "tcpmux", {})
    assert tunnel_test.build_args("fou:wssmux") == ("backhaul", "wssmux", {"link": "fou"})
    assert tunnel_test.build_args("wg:tcp") == ("backhaul", "tcp", {"link": "wg", "install_wg": True})
    assert tunnel_test.build_args("bp:wssmux")[:2] == ("backpack", "wssmux")


def test_a_tunnel_that_carries_no_download_is_not_ready():
    dead = build_other.dead_download
    assert dead({"down_mbps": 0.0, "down1_mbps": 0.0}) and dead({"down_mbps": 0.2}) and dead({"down_mbps": 0.0, "down1_mbps": None})
    assert not dead({"down_mbps": 0.0, "down1_mbps": 30.0}) and not dead({"down_mbps": 12.0, "down1_mbps": 0.0}) and not dead({}) and not dead({"down_mbps": None})      # (one number that moved data: it is not dead)
    import inspect

    src = inspect.getsource(build_other._build)
    assert "dead_download(b.quality)" in src and "not is_temp(b.name) and not b.standby_of" in src            # (a pair test or a standby keeps its number)


def test_a_suggestion_of_a_better_type_of_an_older_version_is_withdrawn():
    e = {"kind": "type", "upgrade": True, "tunnel": "t1", "now_kind": "tcpmux", "at": NOW}
    assert "no longer suggests" in doctor.suggestion_valid(e, {"kind": "tcpmux", "bw_state": "ok"}, set(), NOW + 3600)          # (1.69.84: the Doctor changes a proven steady type by itself and suggests nothing)

def test_the_look_for_a_better_type_changes_a_proven_steady_one_by_itself_and_suggests_nothing(monkeypatch):
    from nikatel.config import get_settings
    from nikatel.services import actions, autopilot, bandwidth

    monkeypatch.setattr(get_settings(), "read_only", False)
    monkeypatch.setattr(actions, "doctor_mode", lambda: "fix")
    monkeypatch.setattr(autopilot, "_snapshot", lambda tid: {"type": "backhaul", "transport": "tcpmux", "record": {}, "iran_id": 1, "foreign_id": 2, "iran_name": "ir", "foreign_name": "fr", "name": "fr-ir"})
    p = _pair({"tcpmux": (160, 0, 62), "bp:tcpmux": (1293, 0, 95)}, now=int(time.time()))
    monkeypatch.setattr(knowledge, "pair", lambda i, f: p)
    monkeypatch.setattr(license, "tunnel_kind_allowed", lambda k: True)
    got, started = [], []
    monkeypatch.setattr(doctor, "suggest", lambda *a, **k: got.append((a, k)) or {})
    monkeypatch.setattr(autopilot, "_start", lambda label, fn, *a, **k: started.append((label, fn, a, k)) or 1)
    bandwidth._UPGRADE_AT.clear()
    bandwidth._upgrade_check(7)
    assert started == [] and got == []                                              # (one test of the better type: its steadiness is not proven - nothing is changed and nothing is suggested)
    now = int(time.time())
    p["kinds"]["bp:tcpmux"]["hist"] = [{"at": now - 86400 + i, "ok": True, "down": 1293, "loss": 0.0} for i in range(3)]
    bandwidth._UPGRADE_AT.clear()
    bandwidth._upgrade_check(7)
    bandwidth._upgrade_check(7)                                                     # (the same tunnel again at once: not looked at twice)
    assert len(started) == 1 and got == []
    label, fn, a, k = started[0]
    assert label == "fr-ir" and fn is doctor._run_retype and a[:3] == (7, "bp:tcpmux", "tcpmux") and a[4] is True and "1293 Mb/s" in a[3] and "160 Mb/s" in a[3] and k["source"] == "auto"
    monkeypatch.setattr(actions, "doctor_mode", lambda: "off")
    bandwidth._UPGRADE_AT.clear()
    bandwidth._upgrade_check(7)
    assert len(started) == 1                                                        # (the Doctor is off: nothing)
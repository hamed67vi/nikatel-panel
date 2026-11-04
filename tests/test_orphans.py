"""The Doctor's sweep of what nothing owns (services/orphans.py, 1.69.219; the owner, 2026-11-04: "I want the extra records and the extra ports gone from the panel and from the servers - the Arvan records stay").
Found by hand that evening: 49 idle units on 12 servers and two standby rows of tunnels that were gone."""
from sqlalchemy import select

from nikatel.config import get_settings
from nikatel.db import session
from nikatel.models import Event, Server, Tunnel
from nikatel.services import autopilot, builder, orphans
from nikatel.services import settings as st

NOW = 1_800_000_000


# ------------------------------------------------------------------ the pure parts
def test_a_leftover_unit_is_taken_off_with_the_reviewed_action_that_fits_it():
    assert orphans.removal_of("backhaul-iran-tcpmux-9910.service") == ("remove_backhaul_unit", {"UNIT": "backhaul-iran-tcpmux-9910.service"})
    assert orphans.removal_of("backhaul-kharej-tcpmux-9937.service") == ("remove_tunnel_files", {"NAME": "orphan9937", "PORT": "9937", "TRANSPORT": "tcpmux"})
    assert orphans.removal_of("backpack-astest-gamma02-kharej.service") == ("other_remove", {"NAME": "astest-gamma02", "KIND": "backpack", "PORT": "0", "PROTO": "tcp"})
    assert orphans.removal_of("paqet-us-delta05-sb2.service")[1]["NAME"] == "us-delta05-sb2" and orphans.removal_of("chisel-test-1.service")[1]["KIND"] == "chisel"
    assert orphans.removal_of("nikatel-gre-check-us-epsilon0-mv.timer") == ("remove_tunnel_files", {"NAME": "us-epsilon0-mv", "PORT": "0", "TRANSPORT": "tcpmux"})
    assert orphans.removal_of("nikatel-sink-bw604.service") == ("bw_sink_remove", {"NAME": "bw604"})
    assert orphans.removal_of("nikatel-sink-test-1.service")[0] == "other_remove"
    assert orphans.removal_of("backpack-a-name-that-is-much-too-long-for-the-panel.service") is None       # (the reviewed actions take the panel's own short names)
    assert orphans.removal_of("nginx.service") is None


def test_a_unit_is_owned_by_a_tunnel_that_names_it_or_whose_link_it_carries():
    exp, ifs, slugs, ids = {"backpack-xx-alpha01.service"}, {"xx-alpha01-sb"}, {"xx-alpha01"}, {604, 438}
    assert orphans.owned("backpack-xx-alpha01.service", exp, ifs, slugs, ids)
    assert orphans.owned("backpack-monitor.service", exp, ifs, slugs, ids) and orphans.owned("backpack-webui.service", exp, ifs, slugs, ids)      # the engine's own
    assert orphans.owned("nikatel-gre-xx-alpha01-sb.service", exp, ifs, slugs, ids) and orphans.owned("nikatel-gre-check-xx-alpha01-sb.timer", exp, ifs, slugs, ids)
    assert not orphans.owned("nikatel-gre-xx-alpha01.service", exp, ifs, slugs, ids)                  # (the link must carry the name of a link of that server: a moved tunnel's old link is a leftover)
    assert orphans.owned("nikatel-sink-bw604.service", exp, ifs, slugs, ids) and not orphans.owned("nikatel-sink-bw999.service", exp, ifs, slugs, ids)
    assert orphans.owned("nikatel-sink-xx-alpha0.service", exp, ifs, slugs, ids)                      # (a shortened name of a test door)
    assert not orphans.owned("backhaul-iran-tcpmux-9910.service", exp, ifs, slugs, ids) and not orphans.owned("paqet-x.service", exp, ifs, slugs, ids)


OUT = """U backhaul-iran-tcpmux-9910.service active 0
U backhaul-iran-tcpmux-9911.service active 3
U nikatel-gre-old.service active -
U nikatel-gre-live.service active -
U nikatel-gre-gone.service inactive -
U nikatel-gre-check-old.timer active -
U nikatel-sink-bw604.service active -
L old 0
L live 2240
L gone -1
garbage line
"""


def test_the_look_is_read_and_only_idle_unowned_units_are_left():
    units, links = orphans.parse_look(OUT)
    assert units["backhaul-iran-tcpmux-9911.service"] == {"active": "active", "estab": 3} and units["nikatel-gre-old.service"]["estab"] is None and links == {"old": 0, "live": 2240, "gone": -1}
    found = orphans.idle_orphans(units, links, lambda u: u == "nikatel-sink-bw604.service")
    # 9911 has a peer connected; the link "live" carries keepalives (its other end is alive); the sink is owned
    assert found == ["backhaul-iran-tcpmux-9910.service", "nikatel-gre-check-old.timer", "nikatel-gre-gone.service", "nikatel-gre-old.service"]
    units2, links2 = orphans.parse_look("U nikatel-gre-new.service active -\nL new -932\n")                  # (a link whose counters jumped back was made again a moment ago: somebody is building it)
    assert orphans.idle_orphans(units2, links2, lambda u: False) == []


def test_a_unit_goes_only_after_two_looks_apart_and_a_test_one_after_a_day():
    ready, mem = orphans.confirm({}, 7, ["a.service", "chisel-test-1.service"], NOW)
    assert ready == [] and set(mem) == {"7:a.service", "7:chisel-test-1.service"}                      # the first look only remembers
    ready, mem = orphans.confirm(mem, 7, ["a.service", "chisel-test-1.service"], NOW + orphans.CONFIRM_AFTER)
    assert ready == ["a.service"]                                                                       # a test one waits
    ready, mem = orphans.confirm(mem, 7, ["chisel-test-1.service"], NOW + orphans.TEST_AGE)
    assert ready == ["chisel-test-1.service"] and "7:a.service" not in mem                              # (a unit that is not found any more is forgotten)
    assert "8:b.service" in orphans.confirm({"8:b.service": NOW}, 7, [], NOW)[1]                        # (another server's memory is kept)


def test_the_rails_and_the_removals_of_one_leftover_are_one_removal():
    assert orphans.rails([], NOW) == "" and "30 leftovers" in orphans.rails([NOW - 60] * orphans.MAX_PER_DAY, NOW) and orphans.rails([NOW - 90000] * 40, NOW) == ""
    acts = orphans.actions_for(["nikatel-gre-x.service", "nikatel-gre-check-x.service", "nikatel-gre-check-x.timer", "backhaul-iran-tcpmux-9910.service", "nginx.service"])
    assert [a[0] for a in acts] == ["remove_tunnel_files", "remove_backhaul_unit"]                      # (a link, its check and its timer are one; an unknown unit is skipped)


# ------------------------------------------------------------------ the job
def _world(monkeypatch, mode="fix", out=OUT, owned_units=()):
    monkeypatch.setattr(get_settings(), "read_only", False)
    monkeypatch.setattr(orphans.jobs, "stopping", lambda: False)
    monkeypatch.setattr(orphans, "_TOLD", {})
    monkeypatch.setattr(autopilot, "storm_now", lambda: False)
    st.put("repair", {"mode": mode})
    st.put("orphans", {"seen": {}, "done": []})
    world = {"expected": {7: set(owned_units)}, "ifaces": {7: {"live"}}, "slugs": {7: set()}, "ids": {604}, "names": {"keep"}, "rows": []}
    monkeypatch.setattr(orphans, "_world", lambda: world)
    monkeypatch.setattr(orphans, "_servers", lambda: [{"id": 7, "name": "ir7", "host": "203.0.113.7", "user": "root", "port": 22}])
    log = []
    monkeypatch.setattr(orphans, "_look", lambda s: out)
    monkeypatch.setattr(orphans, "_backup", lambda s: log.append(("backup",)) or "/var/lib/nikatel-orphans/before-1.tgz")
    monkeypatch.setattr(orphans, "_act", lambda s, a, p: log.append((a, p)) or "REMOVED")
    return world, log


def test_nothing_is_removed_at_the_first_look_and_the_second_look_removes_with_a_backup_first(monkeypatch):
    _w, log = _world(monkeypatch)
    orphans.run()
    assert log == [] and st.get("orphans")["seen"]                                                        # remembered, not touched
    seen = {k: v - orphans.CONFIRM_AFTER - 1 for k, v in st.get("orphans")["seen"].items()}
    st.put("orphans", {"seen": seen})
    orphans.run()
    assert log[0] == ("backup",) and [x[0] for x in log[1:]] == ["remove_backhaul_unit", "remove_tunnel_files", "remove_tunnel_files"] and len(st.get("orphans")["done"]) == 3      # (the backhaul server, the link "old" with its check and timer, the unit of the link that is not there any more)
    with session() as db:
        said = [e.text for e in db.scalars(select(Event)) if "leftover" in e.text]

    assert said and "ir7" in said[-1] and "before-1.tgz" in said[-1] and "nothing owned them" in said[-1]


def test_nothing_is_touched_in_the_mode_off_in_a_storm_or_when_the_server_shows_too_many(monkeypatch):
    _w, log = _world(monkeypatch, mode="off")
    st.put("orphans", {"seen": {"7:backhaul-iran-tcpmux-9910.service": 1}})
    orphans.run()
    assert log == []
    st.put("repair", {"mode": "fix"})
    monkeypatch.setattr(autopilot, "storm_now", lambda: True)
    orphans.run()
    assert log == []
    monkeypatch.setattr(autopilot, "storm_now", lambda: False)
    many = "\n".join(f"U backhaul-iran-tcpmux-{9000 + i}.service active 0" for i in range(orphans.TOO_MANY + 1))
    monkeypatch.setattr(orphans, "_look", lambda s: many)
    st.put("orphans", {"seen": {f"7:backhaul-iran-tcpmux-{9000 + i}.service": 1 for i in range(orphans.TOO_MANY + 1)}})
    orphans.run()
    assert log == []                                                                                       # (the list of what the panel owns is the suspect, not the server)


def test_a_server_that_cannot_be_backed_up_is_not_touched(monkeypatch):
    _w, log = _world(monkeypatch)
    st.put("orphans", {"seen": {"7:backhaul-iran-tcpmux-9910.service": 1}})
    monkeypatch.setattr(orphans, "_backup", lambda s: (_ for _ in ()).throw(RuntimeError("no space")))
    orphans.run()
    assert log == []


def test_a_hidden_helper_whose_parent_is_gone_is_removed_after_the_grace_and_one_with_a_parent_is_not(monkeypatch):
    world, _log = _world(monkeypatch, out="")
    world["rows"] = [{"id": 470, "name": "de1-sb", "parent": "de1-gone", "rx": 0.0, "tx": 0.0}, {"id": 471, "name": "uk-sb", "parent": "keep", "rx": 0.0, "tx": 0.0}, {"id": 472, "name": "busy-sb", "parent": "gone", "rx": 9.0, "tx": 1.0}]
    deleted = []
    monkeypatch.setattr(builder, "delete_tunnel", lambda tid, **kw: deleted.append((tid, kw)) or [])
    orphans.run()
    assert deleted == []                                                                                    # (seen for the first time)
    st.put("orphans", {"seen": {k: v - orphans.ROW_GRACE - 1 for k, v in st.get("orphans")["seen"].items()}})
    orphans.run()
    assert deleted == [(470, {"trash": True, "skip_offline": True})]                                        # the parent of 471 exists, 472 carries traffic
    with session() as db:
        assert any("standby or a strand of de1-gone" in e.text for e in db.scalars(select(Event)))


def test_the_standby_and_the_strands_of_a_removed_tunnel_go_with_it(monkeypatch):
    with session() as db:
        a, f = Server(name="ir-q", host="203.0.113.1", role="iran", online=True), Server(name="fx-q", host="203.0.113.2", role="foreign", online=True)
        db.add_all([a, f])
        db.flush()
        for name, rec, enabled in (("t-sb", {"standby_of": "t-main"}, False), ("t-strand", {"strand_of": "t-main"}, False), ("other-sb", {"standby_of": "other"}, False), ("t-main", {}, True)):
            db.add(Tunnel(name=name, type="backhaul", transport="tcpmux", iran_server_id=a.id, foreign_server_id=f.id, state="up", forwarded_ports=[80], record=rec, enabled=enabled))

    deleted = []
    monkeypatch.setattr(builder, "delete_tunnel", lambda tid, **kw: deleted.append(tid) or [])
    assert sorted(orphans.drop_helpers_of("t-main")) == ["t-sb", "t-strand"] and len(deleted) == 2


def test_the_job_is_registered_the_module_is_in_the_architecture_and_it_never_touches_dns():
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    assert 'jobs.register("orphans", orphans.EVERY, orphans.run)' in (root / "nikatel" / "main.py").read_text(encoding="utf-8")
    assert "orphans.py" in (root / "ARCHITECTURE.md").read_text(encoding="utf-8")
    src = (root / "nikatel" / "services" / "orphans.py").read_text(encoding="utf-8")
    assert "dnsprov" not in src and "point_names" not in src and "import dns" not in src      # (the records are the way back to Arvan: this module does not even import them)
    ssh = (root / "nikatel" / "ssh.py").read_text(encoding="utf-8")
    assert '"orphan_look"' in ssh and '"orphan_backup"' in ssh

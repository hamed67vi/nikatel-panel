"""The Doctor's repair POLICY as a table of scenarios (1.69.279; the owner, 2026-11-15, "بند ۲": a fake fleet, a whole repair, the steps and the builds asserted).
The two faults of the review of the Doctor (a repair on the new tunnel of a move, 38; a repair that lost its tunnel after its own rebuild, 117) lived BETWEEN features: every feature had its own tests, none ran the whole
repair for a kind of incident. Here `autopilot.smart_fix` runs for real - the ladder, the guards (protected, storm, a server off, a relapse), the step order, the record kept for the analyst - against a scripted fleet:
the diagnosis says the cause, every step (restart, rebuild, new link, other type, move) answers what the scenario says, nothing touches a server and no clock runs. What each scenario asserts is the PATH the repair took
(`tried`: step, ok, in order) and the work it did (what was restarted, rebuilt, tested, swapped, moved) - so a change of the policy shows in one place and has to be made on purpose."""
import itertools
import time
from dataclasses import dataclass, field

import pytest

from nikatel.config import get_settings
from nikatel.db import session
from nikatel.models import Server, Tunnel
from nikatel.services import (
    actions,
    analyst,
    autopilot,
    comeback,
    doctor,
    failover,
    knowledge,
    swap,
    testbudget,
    tunnel_test,
)
from nikatel.services import settings as st

_N = itertools.count(1)


@dataclass
class Run:
    healed: bool = False
    tried: list = field(default_factory=list)
    calls: list = field(default_factory=list)
    said: list = field(default_factory=list)
    recorded: int = 0
    lossy: dict | None = None          # what the repair told the swap beside the tunnel about the line it replaces (1.69.283)

    @property
    def path(self) -> list[str]:
        return [f"{s['step']}{'+' if s['ok'] else '-'}" for s in self.tried]

    def said_has(self, text: str) -> bool:
        return any(text in ln for ln in self.said)


def repair(monkeypatch, *, cause: str, kind: str = "tcpmux", level: str = "switch", protected: bool = False, where: str | None = None, off: bool = False, storm: bool = False,
           restart: bool = False, rebuild: bool = False, candidates: tuple = (), quiet: bool = True, replace: bool = False, switch_cut: str = "", move: bool = False,
           relapsed: tuple = (), now_score: float = 40.0, healthy_check: bool = False, busy: bool = False, beside: str = swap.DONE, steady: int | None = None, source: str = "auto",
           mass: bool = False, budget: bool = True, retyped: bool = False, at_once: int = 0) -> Run:
    """One whole `smart_fix` on a scripted fleet. The keyword arguments are what each step ANSWERS (restart / rebuild / replace / move: did it heal; candidates: the results of the tests beside the tunnel; quiet: may it be
    swapped now; switch_cut: the kind a cut tunnel's switch builds, "" = none connects). The no-cut rung for a tunnel with users (1.69.283): `busy` = it carries users now; `beside` = what the swap beside the tunnel comes to
    (the `swap` outcomes); `steady` = what the memory says of the candidate's steadiness; `mass` = a failover is running; `budget` = the day's volume of tests is not used up; `retyped` = the Doctor changed this
    tunnel's type less than a day ago; `at_once` = swaps beside a tunnel going on now."""
    n = next(_N)
    run = Run()
    rec = {"protected": True} if protected else {}
    st.put("repair", {"mode": "fix"})

    with session() as db:
        i = Server(name=f"sc-ir{n}", host=f"198.51.100.{n}", role="iran", online=True)
        f = Server(name=f"sc-fr{n}", host=f"198.51.100.{100 + n}", role="foreign", online=not off)
        db.add_all([i, f])
        db.flush()
        t = Tunnel(name=f"sc{n}", type="backhaul", transport="tcpmux", iran_server_id=i.id, foreign_server_id=f.id, state="down", forwarded_ports=[1000 + n], record=rec)
        db.add(t)
        db.flush()
        tid, iran_id, foreign_id = t.id, i.id, f.id

    def call(name, result=True):
        def fn(*a, **k):
            run.calls.append(name if not k.get("fresh_link") else f"{name}:fresh")
            return result
        return fn

    monkeypatch.setattr(get_settings(), "read_only", False)
    monkeypatch.setattr(autopilot, "diagnose", lambda tunnel_id, **k: {"cause": "healthy" if healthy_check else cause, "summary": f"scenario {cause}", "kind": kind, "state": "down", "where": where, "ext_blocked": False,
                                                                      "dial_i": True, "dial_f": True})
    monkeypatch.setattr(autopilot, "_say", lambda aid, line, done=None, ok=None: run.said.append(line))
    monkeypatch.setattr(autopilot, "storm_now", lambda: storm)
    monkeypatch.setattr(autopilot, "relapsed", lambda name, now: set(relapsed))
    monkeypatch.setattr(actions, "fix", lambda tunnel_id, source="manual": run.calls.append("restart") or 1)
    monkeypatch.setattr(autopilot, "_wait", lambda aid, limit=900: (restart, ["restarted" if restart else "still down"]))
    monkeypatch.setattr(autopilot, "_rebuild_safe", lambda aid, tunnel_id, fresh_link=False, check=True: (run.calls.append("relink-rebuild" if fresh_link else "rebuild") or rebuild, ["Plan: x", "ready"]))
    monkeypatch.setattr(autopilot, "_holds", lambda aid, tunnel_id, seconds=25: run.calls.append("soak") or True)
    monkeypatch.setattr(autopilot, "_recommend_now", lambda aid, iran, foreign, kinds, **k: run.calls.append(f"test:{','.join(kinds)}") or [dict(c) for c in candidates])
    monkeypatch.setattr(autopilot, "_wait_quiet", lambda aid, tunnel_id, **k: run.calls.append("quiet?") or quiet)
    monkeypatch.setattr(autopilot, "_replace", lambda aid, tunnel_id, iran, foreign, k, why, **kw: run.calls.append(f"replace:{k}") or replace)
    monkeypatch.setattr(autopilot, "_switch_cut", lambda aid, snap, kinds: run.calls.append(f"switch-cut:{','.join(kinds)}") or switch_cut)
    monkeypatch.setattr(autopilot, "probe_other_iran", lambda aid, snap, k: run.calls.append("probe-other"))
    monkeypatch.setattr(autopilot.freshlook, "look", lambda snap, *a, **k: run.calls.append("fresh-look") or {"text": "fresh look"})
    monkeypatch.setattr(tunnel_test, "quality", lambda tunnel_id: {"latency_ms": None, "score": now_score})
    monkeypatch.setattr(knowledge, "plan_for", lambda *a, **k: (["tcp", "tcpmux"], []))
    monkeypatch.setattr(knowledge, "recent_failure", lambda *a, **k: False)
    monkeypatch.setattr(knowledge, "remember", lambda *a, **k: None)
    monkeypatch.setattr(analyst, "hints_for", lambda tunnel, cause_, steps: (list(steps), []))
    monkeypatch.setattr(analyst, "record_case", lambda *a, **k: (run.tried.extend(a[5]), setattr(run, "recorded", run.recorded + 1)))
    monkeypatch.setattr(comeback, "note", lambda *a, **k: run.calls.append("comeback-note"))
    monkeypatch.setattr(failover, "after_ladder", lambda aid, tunnel_id, cause_: run.calls.append("move") or move)
    monkeypatch.setattr(autopilot, "_flow_of", lambda tunnel_id: (12.0, 3.0) if busy else (0.3, 0.1))          # (15 Mb/s of users, or nobody: the bar is QUIET_MBPS, 1.5)
    monkeypatch.setattr(autopilot, "mass_on", lambda: mass)
    monkeypatch.setattr(autopilot, "_steady_pct", lambda snap, kind: steady)
    monkeypatch.setattr(testbudget, "allow", lambda level="routine", now=None: budget)
    monkeypatch.setattr(swap, "at_once", lambda: at_once)

    def beside_call(aid, snap, kind, why, before, revert=False, lossy=None, close=True):
        run.calls.append(f"beside:{kind}")
        run.lossy = lossy
        return beside

    monkeypatch.setattr(swap, "beside", beside_call)

    if retyped:
        doctor.mark_retyped(f"sc{n}", int(time.time()) - 3600)

    run.healed = autopilot.smart_fix(7, tid, level, source)
    _ = (iran_id, foreign_id)
    return run


CLEAN = {"kind": "tcp", "ok": True, "score": 85, "loss_pct": 0.0}
SLIGHTLY_BETTER = {"kind": "tcp", "ok": True, "score": 45, "loss_pct": 0.0}
LOSSY_ONE = {"kind": "tcp", "ok": True, "score": 90, "loss_pct": 9.0}
MOVE_FAILED =["switch-", "move-", "move-"]          # (the ladder lists "move" too, but its turn in the loop does nothing - the real move runs after the loop: the record of a failed route repair has two "move" entries, as the analyst's data of the live panel shows)


# ------------------------------------------------------------------ the ladder as a table (what each cause climbs, by level)
@pytest.mark.parametrize("cause,level,steps", [
    ("units_down", "restart", ["restart"]),
    ("units_down", "rebuild", ["restart", "rebuild"]),
    ("units_down", "switch", ["restart", "rebuild", "switch"]),
    ("unknown", "switch", ["restart", "rebuild", "switch"]),
    ("gre_blocked", "rebuild", ["relink"]),
    ("gre_blocked", "switch", ["switch"]),                          # (a filter on the protocol does not care about a new key: the link is not tried again)
    ("port_blocked", "rebuild", ["relink"]),
    ("port_blocked", "switch", ["relink", "switch"]),
    ("lossy_route", "restart", ["move"]),
    ("lossy_route", "switch", ["switch", "move"]),                  # (a lossy tunnel is not restarted or rebuilt: it still carries users)
    ("route_cut", "switch", ["switch", "move"]),
    ("healthy", "switch", []),
])
def test_the_ladder_of_every_cause(cause, level, steps):
    assert autopilot.ladder_for(cause, "tcpmux", level) == steps


# ------------------------------------------------------------------ a cut tunnel: services down
def test_a_restart_that_holds_ends_the_repair(monkeypatch):
    r = repair(monkeypatch, cause="units_down", restart=True)
    assert r.healed and r.path == ["restart+"] and r.calls == ["restart", "soak"]                # (healed once: nothing is rebuilt, nothing moved)


def test_a_failed_restart_climbs_to_a_rebuild(monkeypatch):
    r = repair(monkeypatch, cause="units_down", rebuild=True)
    assert r.healed and r.path == ["restart-", "rebuild+"] and r.calls == ["restart", "rebuild", "soak"]


def test_restart_rebuild_and_a_failed_switch_end_without_a_move_for_a_cut_that_is_only_services(monkeypatch):
    r = repair(monkeypatch, cause="units_down")
    assert not r.healed and r.path == ["restart-", "rebuild-", "switch-"] and "move" not in r.calls            # (a move is the answer to a cut ROUTE, not to stopped services)
    assert r.calls[-1] == "switch-cut:tcp,tcpmux" and r.recorded == 1                                       # (and the failed repair is kept for the analyst once)


def test_the_switch_of_a_cut_tunnel_builds_the_first_type_that_connects_and_remembers_the_old_one(monkeypatch):
    r = repair(monkeypatch, cause="units_down", switch_cut="tcp")
    assert r.healed and r.path == ["restart-", "rebuild-", "switch+"] and r.tried[-1]["kind"] == "tcp"
    assert "comeback-note" in r.calls                                                                       # (a type the tunnel should go back to when the disturbance is over)


def test_a_repair_at_level_restart_stops_after_the_restart(monkeypatch):
    r = repair(monkeypatch, cause="units_down", level="restart")
    assert not r.healed and r.path == ["restart-"] and r.calls == ["restart"]


def test_a_step_that_healed_a_few_minutes_ago_did_not_hold_and_is_skipped(monkeypatch):
    r = repair(monkeypatch, cause="units_down", relapsed=("restart",), rebuild=True)
    assert r.healed and r.path == ["rebuild+"] and "restart" not in r.calls
    assert r.said_has("climbs past it")


# ------------------------------------------------------------------ a cut route: GRE filtered, route cut, port blocked
def test_where_gre_is_filtered_a_new_link_is_not_tried_a_type_that_connects_is_built(monkeypatch):
    r = repair(monkeypatch, cause="gre_blocked", switch_cut="bp:wssmux")
    assert r.healed and r.path == ["switch+"] and not any(c.startswith("relink") for c in r.calls)


def test_a_cut_that_no_type_heals_goes_to_the_move_and_then_a_test_from_another_iran_server(monkeypatch):
    r = repair(monkeypatch, cause="route_cut")
    assert not r.healed and r.path == MOVE_FAILED and r.calls[-2:] == ["move", "probe-other"]


def test_a_move_that_heals_ends_the_repair(monkeypatch):
    r = repair(monkeypatch, cause="route_cut", move=True)
    assert r.healed and r.path == ["switch-", "move-", "move+"] and "probe-other" not in r.calls


def test_a_blocked_port_tests_a_new_link_beside_and_rebuilds_only_when_it_works(monkeypatch):
    r = repair(monkeypatch, cause="port_blocked", candidates=({"kind": "tcpmux", "ok": True, "score": 60, "loss_pct": 0.0},), rebuild=True)
    assert r.healed and r.path == ["relink+"] and r.calls == ["test:tcpmux", "relink-rebuild", "soak"]


def test_a_new_link_that_does_not_pass_removes_nothing_and_the_repair_climbs_on(monkeypatch):
    r = repair(monkeypatch, cause="port_blocked", candidates=({"kind": "tcpmux", "ok": False, "score": None, "loss_pct": 100.0},))
    assert "relink-rebuild" not in r.calls and r.path[0] == "relink-"                                     # (nothing was removed: the tunnel stays as it was)
    assert r.said_has("Nothing was removed")


# ------------------------------------------------------------------ a lossy route: the tunnel still carries users
def test_a_loss_on_one_servers_own_network_builds_nothing_and_goes_to_the_move(monkeypatch):
    r = repair(monkeypatch, cause="lossy_route", where="iran_side")
    assert not r.healed and r.path == MOVE_FAILED and not any(c.startswith(("test:", "replace:")) for c in r.calls)
    assert r.said_has("one server's own network")


def test_a_clearly_better_candidate_replaces_a_lossy_tunnel_at_a_quiet_moment(monkeypatch):
    r = repair(monkeypatch, cause="lossy_route", candidates=(CLEAN,), replace=True)
    assert r.healed and r.path == ["switch+"] and r.calls[-2:] == ["quiet?", "replace:tcp"] and "move" not in r.calls


def test_a_candidate_that_is_not_clearly_better_changes_nothing_and_goes_to_the_move(monkeypatch):
    r = repair(monkeypatch, cause="lossy_route", candidates=(SLIGHTLY_BETTER,))
    assert not r.healed and r.path == MOVE_FAILED and not any(c.startswith("replace:") for c in r.calls)
    assert r.said_has("No candidate is clearly better")


def test_a_candidate_that_itself_loses_packets_is_not_a_candidate(monkeypatch):
    r = repair(monkeypatch, cause="lossy_route", candidates=(LOSSY_ONE,))
    assert not any(c.startswith("replace:") for c in r.calls) and r.said_has("No candidate is clearly better")


def test_a_better_candidate_that_cannot_be_swapped_now_waits_for_the_next_quiet_moment_and_the_repair_goes_on_to_the_move(monkeypatch):
    """The old way, which a tunnel that carries NOBODY still takes: the swap cuts the users, so it waits for under 1.5 Mb/s (a busy tunnel does not take it first any more - see the scenarios below)."""
    r = repair(monkeypatch, cause="lossy_route", candidates=(CLEAN,), quiet=False)
    assert not r.healed and r.path == MOVE_FAILED and not any(c.startswith(("replace:", "beside:")) for c in r.calls)
    assert r.said_has("swapped at the next quiet moment")


# ------------------------------------------------------------------ a BUSY lossy tunnel (1.69.283, the owner, 2026-11-16, «راه a»): the better type is built beside it, nobody is cut
def test_a_busy_lossy_tunnel_is_swapped_beside_without_a_cut_instead_of_waiting_for_a_quiet_moment(monkeypatch):
    """The gap of the review of 21 days (11 of the 39 lossy repairs found a clearly better type and could do nothing with it): users on the tunnel, so no quiet moment - the new tunnel is built beside it."""
    r = repair(monkeypatch, cause="lossy_route", candidates=(CLEAN,), busy=True)
    assert r.healed and r.path == ["switch+"] and r.tried[-1]["kind"] == "tcp"
    assert r.calls[-1] == "beside:tcp" and "quiet?" not in r.calls and not any(c.startswith("replace:") for c in r.calls) and "move" not in r.calls          # (no waiting, no cut, no move)
    assert r.lossy == {"score": 40.0, "loss": None}                                                      # (the swap is told how good the line it replaces is: the new tunnel must beat it)
    assert r.said_has("BESIDE it") and r.said_has("without a cut")


def test_a_tunnel_that_carries_nobody_is_still_replaced_at_a_quiet_moment_the_old_way(monkeypatch):
    r = repair(monkeypatch, cause="lossy_route", candidates=(CLEAN,), replace=True)
    assert r.healed and r.calls[-2:] == ["quiet?", "replace:tcp"] and not any(c.startswith("beside:") for c in r.calls)          # (a swap that hardly disturbs anyone does not need the load of a second tunnel)


def test_a_busy_lossy_tunnel_with_no_clearly_better_candidate_is_left_as_it_is(monkeypatch):
    r = repair(monkeypatch, cause="lossy_route", candidates=(SLIGHTLY_BETTER,), busy=True)
    assert not r.healed and r.path == MOVE_FAILED and r.said_has("No candidate is clearly better")
    assert not any(c.startswith(("beside:", "replace:")) for c in r.calls) and "quiet?" not in r.calls          # (nothing was built beside, nothing replaced, nothing waited for)


def test_a_busy_lossy_tunnel_on_one_servers_own_network_builds_nothing_beside_either(monkeypatch):
    r = repair(monkeypatch, cause="lossy_route", where="foreign_side", candidates=(CLEAN,), busy=True)
    assert not r.healed and r.path == MOVE_FAILED and not any(c.startswith(("test:", "beside:", "replace:")) for c in r.calls)          # (another type would suffer the same loss)


def test_a_protected_busy_lossy_tunnel_is_never_swapped_beside_or_moved(monkeypatch):
    r = repair(monkeypatch, cause="lossy_route", protected=True, candidates=(CLEAN,), replace=True, move=True, busy=True)
    assert not r.healed and r.calls == ["fresh-look"] and r.recorded == 0                                    # (no test, no standby, no swap, no move: each would change its addresses, its type or its server)


@pytest.mark.parametrize("outcome", [swap.REJECTED, swap.ROLLED_BACK, swap.KEPT, swap.HOLD])
def test_a_new_tunnel_that_does_not_prove_itself_changes_nothing_and_the_old_way_is_not_taken(monkeypatch, outcome):
    """Not clearly better through ITSELF (rejected), worse once it carried users (rolled back), the old tunnel could not be replaced (kept), or the swap was held back on purpose: the old tunnel stays, the users were
    never cut - and the cut at a quiet moment is NOT taken for the very type that just failed to prove itself, however bad the loss is."""
    r = repair(monkeypatch, cause="lossy_route", candidates=(CLEAN,), busy=True, beside=outcome, quiet=True, replace=True)
    assert not r.healed and r.path == MOVE_FAILED and r.calls.count("beside:tcp") == 1
    assert "quiet?" not in r.calls and not any(c.startswith("replace:") for c in r.calls) and r.said_has("was NOT changed to tcp")


def test_a_busy_lossy_tunnel_that_cannot_be_built_beside_takes_the_old_way(monkeypatch):
    r = repair(monkeypatch, cause="lossy_route", candidates=(CLEAN,), busy=True, beside=swap.CANNOT, replace=True)
    assert r.healed and r.calls[-3:] == ["beside:tcp", "quiet?", "replace:tcp"] and r.said_has("the old way is left")          # (nothing was built or moved: the cut at a quiet moment is what was there before)

    r = repair(monkeypatch, cause="lossy_route", candidates=(CLEAN,), busy=True, beside=swap.CANNOT, quiet=False)
    assert not r.healed and r.path == MOVE_FAILED and r.said_has("swapped at the next quiet moment")


def test_in_a_mass_disruption_nothing_is_built_beside_a_busy_lossy_tunnel(monkeypatch):
    r = repair(monkeypatch, cause="lossy_route", candidates=(CLEAN,), busy=True, mass=True)
    assert not r.healed and not any(c.startswith(("beside:", "replace:")) for c in r.calls) and "quiet?" not in r.calls and r.said_has("wait now")


def test_at_most_two_swaps_beside_a_tunnel_go_on_at_once_the_third_tunnel_takes_the_old_way(monkeypatch):
    r = repair(monkeypatch, cause="lossy_route", candidates=(CLEAN,), busy=True, at_once=1)
    assert r.healed and r.calls[-1] == "beside:tcp"                                                    # (one is going on: there is room)

    r = repair(monkeypatch, cause="lossy_route", candidates=(CLEAN,), busy=True, at_once=2, quiet=False)
    assert not r.healed and not any(c.startswith("beside:") for c in r.calls) and "quiet?" in r.calls and r.said_has("2 swaps beside a tunnel are going on already")


def test_when_the_days_volume_of_tests_is_used_up_nothing_is_built_beside_and_the_old_way_stays(monkeypatch):
    r = repair(monkeypatch, cause="lossy_route", candidates=(CLEAN,), busy=True, budget=False, quiet=False)
    assert not r.healed and not any(c.startswith("beside:") for c in r.calls) and r.said_has("volume of the Doctor's tests is used up")
    assert "quiet?" in r.calls and not any(c.startswith("replace:") for c in r.calls)                       # (the speed test through a new tunnel is a test: the cut at a quiet moment needs none, and it was told not to be quiet)


def test_the_automatic_change_of_a_type_is_once_a_day_and_the_owners_own_click_is_not_held_by_it(monkeypatch):
    r = repair(monkeypatch, cause="lossy_route", candidates=(CLEAN,), busy=True, retyped=True)
    assert not r.healed and not any(c.startswith(("beside:", "replace:")) for c in r.calls) and "quiet?" not in r.calls and r.said_has("was changed by the Doctor")

    r = repair(monkeypatch, cause="lossy_route", candidates=(CLEAN,), busy=True, retyped=True, source="manual")
    assert r.healed and r.calls[-1] == "beside:tcp"


def test_a_type_the_memory_calls_unsteady_is_not_taken_by_itself_one_that_is_not_proven_yet_is_tried_beside(monkeypatch):
    r = repair(monkeypatch, cause="lossy_route", candidates=(CLEAN,), busy=True, steady=40)
    assert not r.healed and not any(c.startswith(("beside:", "replace:")) for c in r.calls) and r.said_has("UNSTEADY")

    r = repair(monkeypatch, cause="lossy_route", candidates=(CLEAN,), busy=True, steady=None)
    assert r.healed and r.calls[-1] == "beside:tcp"                                                          # (it proves itself through itself before any user is sent to it, and the retype watch follows it)

    r = repair(monkeypatch, cause="lossy_route", candidates=(CLEAN,), busy=True, steady=90)
    assert r.healed and r.calls[-1] == "beside:tcp"


# ------------------------------------------------------------------ the guards
def test_a_protected_tunnel_is_repaired_in_place_only(monkeypatch):
    r = repair(monkeypatch, cause="units_down", protected=True, restart=True)
    assert r.healed and r.path == ["restart+"] and not any(c in r.calls for c in ("rebuild", "move"))
    assert r.said_has("is protected")


def test_a_protected_lossy_tunnel_gets_a_look_and_nothing_else(monkeypatch):
    r = repair(monkeypatch, cause="lossy_route", protected=True, candidates=(CLEAN,), replace=True, move=True)
    assert not r.healed and r.calls == ["fresh-look"] and r.recorded == 0                                    # (no candidate test, no swap, no move: each would change its addresses, its type or its server)
    assert r.said_has("nothing is offered to it for this cause")


def test_in_a_mass_disruption_nothing_is_rebuilt_or_removed(monkeypatch):
    r = repair(monkeypatch, cause="units_down", storm=True, rebuild=True)
    assert not r.healed and r.path == ["restart-"] and "rebuild" not in r.calls and r.said_has("Mass disruption")


def test_a_server_that_is_off_means_no_step_at_all(monkeypatch):
    r = repair(monkeypatch, cause="units_down", off=True, restart=True)
    assert not r.healed and r.calls == [] and r.recorded == 0 and r.said_has("nothing is done to this tunnel")


def test_a_tunnel_that_is_healthy_is_left_alone(monkeypatch):
    r = repair(monkeypatch, cause="units_down", healthy_check=True)
    assert r.healed and r.calls == [] and r.said_has("nothing to repair")


def test_a_connected_tunnel_with_no_service_is_not_swapped_or_moved(monkeypatch):
    r = repair(monkeypatch, cause="no_service")
    assert not r.healed and r.calls == [] and r.said_has("no service on the foreign port")

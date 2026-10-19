"""The warm standby is on by itself for every tunnel (1.69.23, the owner, 2026-10-19: "always on, by itself - and do not show it"): which tunnel gets its standby next, and what the owner's own switch-off does."""
from nikatel.services import settings as st
from nikatel.services import standby


def c(name, state="up", ports=(8075,), created=100):
    return {"name": name, "state": state, "ports": list(ports), "created_at": created}


def test_one_tunnel_at_a_time_the_oldest_first_only_when_it_is_up_forwards_a_port_and_the_owner_did_not_switch_it_off():
    cands = [c("b", created=200), c("a", created=100), c("down", "down", created=50), c("noport", ports=(), created=60), c("off", created=70), c("done", created=80), c("t-new", created=300)]
    pick = lambda **kw: standby.auto_pick(cands, kw.get("marked", {"done"}), kw.get("optout", {"off"}), kw.get("building", False), kw.get("auto", True), kw.get("last", 0.0), kw.get("now", 1000.0))
    assert pick() == "a"                                                                  # the oldest of the ones that are up, with a port, not marked, not switched off
    assert pick(marked={"done", "a"}) == "b" and pick(marked={"done", "a", "b", "t-new"}) == ""
    assert pick(building=True) == "" and pick(auto=False) == ""                           # not while a standby is being built; not when the Doctor is off or the panel read-only
    assert pick(last=950.0) == "" and pick(last=800.0) == "a"                              # one every AUTO_EVERY (120 s)
    assert standby.auto_pick([], set(), set(), False, True, 0.0, 1000.0) == ""


def test_the_owners_own_switch_off_is_remembered_and_the_switch_on_forgets_it():
    standby._mark("t1", True)
    assert "t1" in st.get("standby")["marked"] and "t1" not in st.get("standby")["optout"]
    standby._mark("t1", False)                                                            # (the job's own removal - the tunnel's row is gone for good - is not the owner's wish)
    assert "t1" not in st.get("standby")["optout"]
    standby._mark("t1", False, manual=True)
    assert "t1" in st.get("standby")["optout"] and "t1" not in st.get("standby")["marked"]
    standby._mark("t1", True)
    assert "t1" not in st.get("standby")["optout"] and "t1" in st.get("standby")["marked"]

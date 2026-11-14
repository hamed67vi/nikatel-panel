"""A blip is not told (1.69.273). The owner, 2026-11-15: sixty «🔴 DOWN» messages a day, each followed by its «🟢 UP again» a minute later - "حل کن که پیام‌های بیخودی نیاد". Each KIND of thing now waits its own
time before its DOWN is told (config 5 min, tunnel 1 min, server 90 s, node 2 min); the base `down_seconds` stays the floor."""
from nikatel.services import settings as st
from nikatel.services.alerts import Rules, Track, decide, rules_for, rules_from

BASE = Rules(down_seconds=10, up_seconds=15, by_kind={"config": 300, "tunnel": 60, "server": 90, "node": 120})
T0 = 1_800_000_000


def told(rules: Rules, kind: str, bad_for: int) -> list:
    """Run the rule every 5 seconds over a fault that lasts `bad_for` seconds and then clears: what was said."""
    r, track, said = rules_for(rules, kind), Track(), []

    for now in range(T0, T0 + bad_for, 5):
        track, v = decide(track, True, now, r, since=T0)
        said += [v] if v else []

    for now in range(T0 + bad_for, T0 + bad_for + 60, 5):
        track, v = decide(track, False, now, r, since=T0 + bad_for)
        said += [v] if v else []

    return said


def test_each_kind_waits_its_own_time():
    assert rules_for(BASE, "config").down_seconds == 300 and rules_for(BASE, "tunnel").down_seconds == 60 and rules_for(BASE, "server").down_seconds == 90 and rules_for(BASE, "node").down_seconds == 120
    assert rules_for(BASE, "panel").down_seconds == 10                                        # (a kind without an entry keeps the base)


def test_the_base_is_the_floor_a_longer_base_wins():
    assert rules_for(Rules(down_seconds=600, by_kind={"config": 300}), "config").down_seconds == 600
    assert rules_for(Rules(down_seconds=0), "config").down_seconds == 0 and rules_for(Rules(down_seconds=0, by_kind={}), "tunnel").down_seconds == 0


def test_a_blip_says_nothing_neither_down_nor_up():
    assert told(BASE, "config", 120) == []                      # a config that missed two tests in a row and came back
    assert told(BASE, "tunnel", 30) == []                       # a tunnel cut for 30 s (its standby carried it)
    assert told(BASE, "server", 60) == []                       # a server the panel host could not reach for a minute
    assert told(BASE, "node", 100) == []


def test_a_fault_that_lasts_is_told_as_before_and_its_repair_too():
    assert told(BASE, "config", 400) == ["down", "up"]
    assert told(BASE, "tunnel", 90) == ["down", "up"]
    assert told(BASE, "server", 120) == ["down", "up"]
    assert told(BASE, "panel", 20) == ["down", "up"]           # (not a kind with its own time: the base 10 s, as before)


def test_the_settings_carry_the_times_and_an_empty_one_means_none():
    d = st.get("alerts")
    assert d["down_seconds_by_kind"] == {"config": 300, "tunnel": 60, "server": 90, "node": 120}
    r = rules_from(d)
    assert r.by_kind == {"config": 300, "tunnel": 60, "server": 90, "node": 120} and r.down_seconds == int(d["down_seconds"])
    assert rules_from({**d, "down_seconds_by_kind": {}}).by_kind == {} and rules_for(rules_from({**d, "down_seconds_by_kind": {}}), "config").down_seconds == int(d["down_seconds"])
    assert rules_from({**d, "down_seconds_by_kind": {"config": 900, "x": "bad"}}).by_kind == {"config": 900}

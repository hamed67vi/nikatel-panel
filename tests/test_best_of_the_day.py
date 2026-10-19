"""The tunnel type "Best of the day" (the owner, 2026-10-19: "a tunnel mode that puts the best tunnel of the day on it"): in Create tunnel, Type -> Automatic -> Best of the day. When Build is pressed the panel picks the type that connects best
between the two servers - `knowledge.best_kind`: the types that connect on the pair, a fresh full measurement before a latency-only one, each by score, what the licence allows - and builds it with that type's defaults."""
from nikatel.db import session
from nikatel.models import Server
from nikatel.services import build_other, builder, knowledge


def two_servers(tag: str) -> tuple[int, int]:
    with session() as db:
        n = len(db.query(Server).all()) + 60
        ir = Server(name=f"ir-{tag}", host=f"203.0.113.{n}", role="iran", online=True)
        fr = Server(name=f"fr-{tag}", host=f"198.51.100.{n}", role="foreign", online=True)
        db.add_all([ir, fr])
        db.flush()
        return ir.id, fr.id


def measured(iran, foreign, kind, score):
    knowledge.record(iran, foreign, {"kind": kind, "ok": True, "score": score, "latency_ms": 100.0, "loss_pct": 0.0}, "latency")


def test_the_best_kind_is_the_best_that_connects_on_the_pair_and_the_licence_allows():
    ir, fr = two_servers("bk1")
    measured(ir, fr, "chisel:tls", 70)
    measured(ir, fr, "bp:wssmux", 90)
    kind, why = knowledge.best_kind(ir, fr)
    assert kind == "bp:wssmux" and "score 90" in why and "the best that connects" in why
    assert knowledge.best_kind(ir, fr, allowed=lambda k: not k.startswith("bp:"))[0] == "chisel:tls"                     # (what the licence does not allow is not chosen)
    assert knowledge.best_kind(ir, fr, allowed=lambda k: False)[0] == ""


def test_a_pair_that_nobody_measured_gets_the_first_choice_for_a_new_pair_and_never_a_wireguard_type():
    ir, fr = two_servers("bk2")
    kind, why = knowledge.best_kind(ir, fr)
    assert kind and not kind.startswith("wg") and "nothing is measured between these two servers yet" in why
    assert knowledge.best_kind(ir, fr, allowed=lambda k: False) == ("", "no tunnel type may be built here (the licence)")


def test_the_build_with_kind_auto_builds_the_type_that_was_chosen_and_says_which(client, monkeypatch):
    ir, fr = two_servers("bk3")
    calls = []
    monkeypatch.setattr(builder, "start_build", lambda *a, **k: calls.append(("builder", a, k)) or 11)
    monkeypatch.setattr(build_other, "start_build", lambda *a, **k: calls.append(("other", a, k)) or 12)
    r = client.post("/api/tunnels", json={"iran_id": 0, "foreign_id": fr, "ports": "8443", "kind": "auto", "transport": "best"})
    assert r.status_code == 400 and "pick the Iran server" in r.json()["error"]                                            # (the pair must be known)

    measured(ir, fr, "tcpmux", 60)
    measured(ir, fr, "bp:wssmux", 90)
    r = client.post("/api/tunnels", json={"iran_id": ir, "foreign_id": fr, "ports": "8443", "kind": "auto", "transport": "best", "opts": {"sni_domain": "x.example"}})
    assert r.status_code == 202 and r.json()["id"] == 12 and r.json()["kind"] == "backpack" and r.json()["transport"] == "wssmux" and "bp:wssmux" in r.json()["why"]
    assert calls[-1][0] == "other" and calls[-1][1][3:5] == ("backpack", "wssmux") and calls[-1][2]["measure"] == "full" and "sni_domain" not in calls[-1][1][5]      # (the options of the form are not the chosen type's: its defaults)

    measured(ir, fr, "tcpmux", 99)
    r = client.post("/api/tunnels", json={"iran_id": ir, "foreign_id": fr, "ports": "8443", "kind": "auto", "transport": "best"})
    assert r.status_code == 202 and r.json()["id"] == 11 and r.json()["kind"] == "backhaul" and r.json()["transport"] == "tcpmux"
    assert calls[-1][0] == "builder" and calls[-1][1][3] == "tcpmux"                                                    # (a Backhaul type goes the Backhaul way, over GRE)

    r = client.post("/api/tunnels", json={"iran_id": ir, "foreign_id": fr, "ports": "8443", "kind": "nonsense"})
    assert r.status_code == 422                                                                                          # (only the five engines and auto)

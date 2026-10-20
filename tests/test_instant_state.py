"""A server or a tunnel that is cut shows at once (the owner, 2026-10-20: "it detected very late - I want it shown at once if a tunnel or a server is cut"; "remove GRE from the error tag"; "the tags smaller and in two rows").

What is tested: a server that goes off or comes back makes EVERY tunnel that ends on it be looked at at once (also the ones that are up), the page's signature of the states, and the page's own code (the 2 s
signature, the tags without GRE, the Type cell in two rows)."""
import time

from nikatel import jobs
from nikatel.db import session
from nikatel.models import Server, Tunnel
from nikatel.services import pulse, tunnels

WEB = __import__("pathlib").Path(__file__).resolve().parents[1] / "nikatel" / "web"


def _world(tag):
    with session() as db:
        ir, fo, other = (Server(name=f"ir-{tag}", host="203.0.113.81", role="iran", online=True), Server(name=f"fo-{tag}", host="198.51.100.81", role="foreign", online=True),
                         Server(name=f"fo2-{tag}", host="198.51.100.82", role="foreign", online=True))
        db.add_all([ir, fo, other])
        db.flush()
        up = Tunnel(name=f"a-{tag}", type="backhaul", transport="tcpmux", iran_server_id=ir.id, foreign_server_id=fo.id, state="up", enabled=True, forwarded_ports=[8075])
        off = Tunnel(name=f"b-{tag}", type="backhaul", transport="tcpmux", iran_server_id=ir.id, foreign_server_id=fo.id, state="up", enabled=False, forwarded_ports=[8076])
        far = Tunnel(name=f"c-{tag}", type="backhaul", transport="tcpmux", iran_server_id=ir.id, foreign_server_id=other.id, state="up", enabled=True, forwarded_ports=[8077])
        db.add_all([up, off, far])
        db.flush()
        return {"ir": ir.id, "fo": fo.id, "other": other.id, "up": up.id, "off": off.id, "far": far.id}


def test_every_tunnel_that_ends_on_a_server_is_looked_at_at_once_also_the_ones_that_are_up(monkeypatch):
    w = _world("i1")
    seen = []
    monkeypatch.setattr(tunnels, "check_tunnel", lambda tid, fresh_ext=False, quick=False: seen.append((tid, fresh_ext)))
    tunnels.check_of_servers([w["fo"]])
    assert seen == [(w["up"], True)]                                                  # (the tunnel that is up, on that server; not the disabled one, not the one of another server)
    seen.clear()
    tunnels.check_of_servers([w["ir"]])
    assert sorted(i for i, _ in seen) == sorted([w["up"], w["far"]])                  # (the Iran server ends both of the enabled ones)
    seen.clear()
    tunnels.check_of_servers([])
    assert seen == []


def test_a_server_that_goes_off_wakes_the_look_at_its_tunnels(monkeypatch):
    w = _world("i2")
    looked, woke = [], []
    monkeypatch.setattr(jobs, "trigger", lambda name: woke.append(name))
    monkeypatch.setattr(tunnels, "check_of_servers", lambda ids: looked.append(list(ids)))
    monkeypatch.setattr(pulse, "tcp_open", lambda host, port, timeout=2.0: host != "198.51.100.81")          # the foreign server of the tunnel does not answer
    pulse._miss.clear()
    pulse.run()
    assert looked == [] and woke == []                                                # (one miss is not enough: two in a row)
    pulse.run()

    for _ in range(50):
        if looked:
            break

        time.sleep(0.05)

    assert looked == [[w["fo"]]] and woke == ["tunnels-fast"]
    monkeypatch.setattr(pulse, "tcp_open", lambda host, port, timeout=2.0: True)
    looked.clear()
    pulse.run()

    for _ in range(50):
        if looked:
            break

        time.sleep(0.05)

    assert looked == [[w["fo"]]]                                                      # (and when it comes back, its tunnels are looked at again at once)


def test_the_signature_of_the_states_changes_with_a_server_or_a_tunnel_and_carries_no_name(client):
    w = _world("i3")
    a = client.get("/api/tunnels/brief").json()
    assert a["ok"] is True and len(a["sig"]) == 12 and a["servers"] >= 3 and a["tunnels"] >= 2 and set(a) == {"ok", "sig", "servers", "tunnels"}
    assert client.get("/api/tunnels/brief").json()["sig"] == a["sig"]                 # (the same states: the same signature)

    with session() as db:
        db.get(Tunnel, w["up"]).state = "down"

    b = client.get("/api/tunnels/brief").json()["sig"]
    assert b != a["sig"]

    with session() as db:
        db.get(Server, w["fo"]).online = False

    assert client.get("/api/tunnels/brief").json()["sig"] not in (a["sig"], b)


def test_the_page_asks_for_the_signature_every_two_seconds_and_redraws_at_once_when_it_changes():
    js = (WEB / "app.js").read_text(encoding="utf-8")
    assert 'api("/api/tunnels/brief")' in js and "if (briefSig !== null && r.sig !== briefSig) lastTick = 0;" in js and "}, 2000);" in js.split("let briefSig = null;", 1)[1]


def test_the_tags_of_a_cut_tunnel_do_not_say_gre_and_the_type_cell_has_two_rows():
    js, css = (WEB / "app.js").read_text(encoding="utf-8"), (WEB / "lab.css").read_text(encoding="utf-8")
    tags = js.split("const CUT_TAGS = [", 1)[1].split("];", 1)[0]
    assert '"GRE foreign"' not in tags and '"GRE Iran"' not in tags and '"GRE cut"' not in tags and '"foreign link"' in tags and '"Iran link"' in tags and '"link cut"' in tags
    assert '<td class="td-ty2"><span class="td-type' in js and '</span><small class="mut">${gre ? linkTag(t)' in js
    assert "html body #page .tun-tbl td.td-ty2 small { display: block;" in css and "html body #page .tun-tbl td.td-ty2 .td-type { display: inline-block; font-size: 9px;" in css

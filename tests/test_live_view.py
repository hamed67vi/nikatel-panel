"""The Live window (1.69.57), like nload: the owner, 2026-10-20: "I used nload on the Iran or the foreign server, it showed the bandwidth at that moment - show it like that". One reading a second of a card's byte counters
(`live_sample`, `livebw.sample`, GET /api/tunnels/{id}/live-read and /api/servers/{id}/live-read) and, in the page, the rates, Curr / Avg / Min / Max / Ttl and the graph - the helpers run through node."""
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from nikatel.db import session
from nikatel.models import Server, Tunnel
from nikatel.services import livebw
from nikatel.ssh import SCRIPTS, SSHError

WEB = Path(__file__).resolve().parent.parent / "nikatel" / "web"
LAB = (WEB / "lab.js").read_text(encoding="utf-8")
NODE = shutil.which("node")


def _js(body: str):
    start, end = LAB.index("const LIVE_KEEP = 120;"), LAB.index("function liveDraw(L) {")
    prog = ("const esc = s => String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;'); let shown = ''; const document = {getElementById: id => id === 'lv-body' ? {set innerHTML(v) { shown = v; }} : null};\n"
            + LAB[start:end] + "\n" + body)
    out = subprocess.run([NODE, "-e", prog], check=False, capture_output=True, text=True, timeout=30)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_a_rate_is_two_readings_on_the_servers_own_clock_and_a_reset_or_a_first_reading_gives_none():
    r = _js("""
      const a = {up: 100, rx: 0, tx: 0}, b = {up: 101, rx: 1250000, tx: 250000}, c = {up: 103, rx: 1250000 + 5000000, tx: 250000};
      console.log(JSON.stringify({first: liveRate(null, a), r1: liveRate(a, b), r2: liveRate(b, c), same: liveRate(b, {...b}), clock: liveRate(b, {...c, up: 100.5}), back: liveRate(b, {up: 102, rx: 5, tx: 5}), huge: liveRate(a, {up: 101, rx: 1e14, tx: 0})}));
    """)
    assert r["first"] is None and r["same"] is None and r["clock"] is None and r["back"] is None and r["huge"] is None            # nothing between two equal clocks, a counter that went back (the card was made again), a rate no card can carry
    assert r["r1"]["down"] == pytest.approx(10.0) and r["r1"]["up"] == pytest.approx(2.0) and r["r1"]["dt"] == 1 and r["r1"]["rx"] == 1250000        # 1.25 MB in a second = 10 Mb/s
    assert r["r2"]["down"] == pytest.approx(20.0) and r["r2"]["up"] == 0 and r["r2"]["dt"] == 2                                                    # 5 MB in two seconds = 20 Mb/s: the time is the server's, not the browser's


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_curr_avg_min_max_and_the_total_are_kept_and_the_graph_holds_the_last_two_minutes():
    r = _js("""
      const st = liveNew();
      for (const [d, u, dt] of [[10, 1, 1], [30, 3, 1], [20, 2, 2]]) liveFold(st, {down: d, up: u, dt, rx: d * 125000 * dt, tx: u * 125000 * dt});
      const avg = st.down.sum / st.secs;
      for (let i = 0; i < 200; i++) liveFold(st, {down: 1, up: 1, dt: 1, rx: 1, tx: 1});
      console.log(JSON.stringify({cur: st.down.cur, avg: Math.round(avg * 100) / 100, min: st.down.min, max: st.down.max, secs: st.secs, pts: st.pts.length, nice: [liveNice(7), liveNice(0.3), liveNice(130), liveNice(45)], b: [liveBytes(512), liveBytes(2.5e6), liveBytes(3.2e9)], f: [liveFmt(null), liveFmt(5.236), liveFmt(52.36), liveFmt(523.6)]}));
    """)
    assert r["avg"] == 20.0 and r["min"] == 1 and r["max"] == 30 and r["cur"] == 1                                                                      # the average is by time: (10 + 30 + 20 x 2) / 4 s
    assert r["pts"] == 120 and r["secs"] == 204 and r["nice"] == [10, 0.5, 200, 50] and r["b"] == ["512 B", "2.5 MB", "3.20 GB"] and r["f"] == ["—", "5.24", "52.4", "524"]


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_the_window_says_what_it_waits_for_what_failed_and_shows_both_sides_with_their_numbers():
    r = _js("""
      const L = {st: liveNew(), via: "card", name: "t1", meta: {}, err: ""};
      liveBody(L); const waiting = shown;
      L.err = "the network card could not be read on <ir1>"; liveBody(L); const failed = shown;
      L.err = ""; L.meta = {card: "gre-t1", server: "ir1"};
      liveFold(L.st, {down: 45.2, up: 3.1, dt: 1, rx: 5650000, tx: 387500});
      liveBody(L); const card = shown; L.via = "iran_nic"; liveBody(L);
      console.log(JSON.stringify({waiting, failed, card, nic: shown, graph: liveGraph(L.st.pts).startsWith("<svg")}));
    """)
    assert "Waiting for the first readings" in r["waiting"] and 'class="err"' in r["failed"] and "&lt;ir1&gt;" in r["failed"]                              # the text of the error is escaped
    assert "Incoming ↓ (the users' download)" in r["card"] and "Outgoing ↑ (the users' upload)" in r["card"] and "<em>45.2</em>" in r["card"] and "<em>3.10</em>" in r["card"] and "gre-t1 on ir1" in r["card"]
    assert "Incoming ↓</b>" in r["nic"] and "the users" not in r["nic"] and r["graph"] is True                                                         # the main card of a server is plain Incoming / Outgoing, like nload


# ------------------------------------------------------------------ the script, the reading, the routes
def test_the_live_sample_script_reads_one_card_and_the_uptime_and_only_a_card_name_or_auto_is_accepted():
    script, rules = SCRIPTS["live_sample"]
    out = subprocess.run(["bash", "-c", script.replace("@IFACE@", "lo")], check=False, capture_output=True, text=True, timeout=20).stdout
    up, cards = livebw.parse_counters(out)
    assert up is not None and "lo" in cards and cards["lo"][0] >= 0                                                       # "IF lo <rx> <tx>" and "UP <seconds>"
    gone = subprocess.run(["bash", "-c", script.replace("@IFACE@", "nosuchcard0")], check=False, capture_output=True, text=True, timeout=20).stdout
    assert "IF " not in gone and "UP " in gone                                                                            # a card that does not exist: no IF line
    ok = re.compile(rules["IFACE"])
    assert all(ok.fullmatch(x) for x in ("auto", "eth0", "gre-t1", "ens3.100")) and not any(ok.fullmatch(x) for x in ("eth0;reboot", "", "a b", "$(id)", "x" * 20, "-h"))


def _world(kind="backhaul"):
    with session() as db:
        ir, fo = Server(name="ir1", host="203.0.113.5", role="iran", online=True), Server(name="fo1", host="203.0.113.50", role="foreign", online=True)
        db.add_all([ir, fo])
        db.flush()
        t = Tunnel(name="t1", type=kind, transport="tcpmux", iran_server_id=ir.id, foreign_server_id=fo.id, state="up", forwarded_ports=[8075], record={"slug": "t1"})
        db.add(t)
        db.flush()
        return {"ir": ir.id, "fo": fo.id, "t": t.id}


def test_one_reading_is_the_counters_and_the_uptime_of_the_card_of_the_tunnel_or_of_the_main_card_of_a_server(monkeypatch):
    w = _world()
    asked = []

    def fake(node, name, params=None, timeout=8):
        asked.append((node.host, name, dict(params or {})))
        return f"IF {params['IFACE'] if params['IFACE'] != 'auto' else 'eth0'} 1000 2000\nUP 5.5\n"

    monkeypatch.setattr(livebw, "run_light", fake)
    r = livebw.sample(w["t"], "card")
    assert r["via"] == "card" and r["server"] == "ir1" and r["rx"] == 1000 and r["tx"] == 2000 and r["up"] == 5.5 and r["card"] and r["card"] != "auto"
    assert asked[0][0] == "203.0.113.5" and asked[0][1] == "live_sample" and asked[0][2]["IFACE"] != "auto"             # the card of the TUNNEL on its Iran server
    assert livebw.sample(w["t"], "iran_nic")["server"] == "ir1" and asked[-1][2] == {"IFACE": "auto"}
    assert livebw.sample(w["t"], "foreign_nic")["server"] == "fo1" and asked[-1][0] == "203.0.113.50"                  # the main card of the foreign server, like nload there
    s = livebw.server_sample(w["fo"])
    assert s["via"] == "nic" and s["card"] == "eth0" and asked[-1] == ("203.0.113.50", "live_sample", {"IFACE": "auto"})

    with pytest.raises(ValueError, match="via must be"):
        livebw.sample(w["t"], "wlan")

    with pytest.raises(ValueError, match="tunnel not found"):
        livebw.sample(9999, "card")

    with pytest.raises(ValueError, match="server not found"):
        livebw.server_sample(9999)

    monkeypatch.setattr(livebw, "run_light", lambda node, name, params=None, timeout=8: "UP 5.5\n")                   # no IF line: the card is not there
    with pytest.raises(RuntimeError, match="could not be read on ir1"):
        livebw.sample(w["t"], "iran_nic")


def test_a_tunnel_with_no_card_of_its_own_says_so_and_the_server_card_stays_possible(monkeypatch):
    w = _world(kind="chisel")
    monkeypatch.setattr(livebw, "run_light", lambda node, name, params=None, timeout=8: "IF eth0 1 2\nUP 5\n")

    with pytest.raises(ValueError, match="no network card of its own"):
        livebw.sample(w["t"], "card")

    assert livebw.sample(w["t"], "iran_nic")["card"] == "eth0"


def test_the_routes_answer_a_reading_a_refusal_with_its_reason_and_a_bad_gateway_when_the_server_does_not_answer(client, monkeypatch):
    w = _world()
    monkeypatch.setattr(livebw, "run_light", lambda node, name, params=None, timeout=8: "IF eth0 10 20\nUP 7.25\n")
    r = client.get(f"/api/tunnels/{w['t']}/live-read?via=iran_nic")
    assert r.status_code == 200 and r.json()["rx"] == 10 and r.json()["up"] == 7.25 and r.json()["via"] == "iran_nic"
    assert client.get(f"/api/tunnels/{w['t']}/live-read?via=nope").status_code == 400 and client.get("/api/tunnels/9999/live-read").status_code == 400
    s = client.get(f"/api/servers/{w['fo']}/live-read")
    assert s.status_code == 200 and s.json()["server"] == "fo1" and s.json()["via"] == "nic" and client.get("/api/servers/9999/live-read").status_code == 400

    def down(node, name, params=None, timeout=8):
        raise SSHError("connection timed out")

    monkeypatch.setattr(livebw, "run_light", down)
    assert client.get(f"/api/tunnels/{w['t']}/live-read?via=iran_nic").status_code == 502 and client.get(f"/api/servers/{w['fo']}/live-read").status_code == 502


def test_the_menus_of_a_tunnel_and_of_a_server_open_the_window_and_the_words_are_in_persian():
    fa, css = (WEB / "i18n.fa.ui2.js").read_text(encoding="utf-8"), (WEB / "lab.css").read_text(encoding="utf-8")
    assert LAB.count('labIt("Live traffic…", "pulse", () => liveOpen("tunnel", t.id, t.name, !!(t.units || {}).iface)') == 1 and LAB.count('labIt("Live traffic…", "pulse", () => liveOpen("server", s.id, nm)') == 1   # the tunnel menu, and the one list of a server (its row and its window, since 1.69.134)
    assert "window.liveOpen = (kind, id, name, hasCard) =>" in LAB and "/live-read?via=${via}" in LAB and "/api/servers/${L.id}/live-read" in LAB and "Math.max(150, 1000 - (Date.now() - t0))" in LAB            # one reading a second, the time of the answer taken off
    assert "while (LIVE_W === L && document.getElementById(\"lv-\" + L.token))" in LAB and "if (L.fails >= 5)" in LAB                                                                               # a closed window stops everything; five failures in a row stop it too
    assert ".lv .lv-row" in css and ".lv .lv-g .l0" in css and '"Live traffic": "' in fa and '"Curr": "' in fa and '"Ttl": "' in fa

"""A new tunnel gets its capacity soon, its row shows an empty bar until then, and the Create window comes back with the build that runs (the owner, 2026-10-21: "the tunnel I built shows no total bandwidth - check
this for all the tunnels"; "I closed the box while it built, then Create tunnel showed nothing, later the tunnel appeared").

What is tested: the sink script waits for its unit and says why when it is down, a tunnel with no capacity is tried again after 90 s (one that has one after 15 minutes), the meter without a total is an empty bar with a
word, and a closed Create window comes back with the story of the running build."""
import time
from pathlib import Path

from nikatel import ssh
from nikatel.db import session
from nikatel.models import Server, Tunnel
from nikatel.services import bandwidth

WEB = Path(__file__).resolve().parents[1] / "nikatel" / "web"


def test_the_sink_script_waits_for_its_unit_and_says_why_when_it_is_down():
    text = ssh.render_action("sink_install", {"NAME": "bw7", "PORT": "39777", "KEY": "abcdefgh12", "BIND": "10.1.2.3"})
    assert "for i in 1 2 3 4 5 6 7 8 9 10 11 12; do sleep 0.5; if systemctl is-active --quiet nikatel-sink-bw7.service; then echo SINK_OK; exit 0; fi; done" in text
    assert 'echo "SINK_DOWN $(journalctl -u nikatel-sink-bw7.service -n 3 --no-pager -o cat' in text and "systemctl restart nikatel-sink-bw7.service\nfor i" in text      # (not one second any more)


_N = iter(range(100, 200))


def _tunnel(tag, bw):
    n = next(_N)

    with session() as db:
        i, f = Server(name=f"ir-{tag}", host=f"203.0.113.{n}", role="iran", online=True), Server(name=f"fo-{tag}", host=f"198.51.100.{n}", role="foreign", online=True)
        db.add_all([i, f])
        db.flush()
        t = Tunnel(name=f"t-{tag}", type="backhaul", transport="tcpmux", iran_server_id=i.id, foreign_server_id=f.id, state="up", enabled=True, forwarded_ports=[8075], record={"bw": bw} if bw else {})
        db.add(t)
        db.flush()
        return t.id


def test_a_tunnel_with_no_capacity_is_tried_again_soon_and_one_with_a_capacity_later():
    new, old = _tunnel("n1", None), _tunnel("n2", {"down": 400.0, "at": int(time.time()) - 60})
    now = int(time.time())
    bandwidth._failed(new, "the bandwidth sink did not start on the foreign server: SINK_DOWN")
    bandwidth._failed(old, "no answer")

    with session() as db:
        a, b = db.get(Tunnel, new).record["bw"], db.get(Tunnel, old).record["bw"]

    assert bandwidth.RETRY_FIRST == 90 and 80 <= a["next_at"] - now <= 100 and "SINK_DOWN" in a["error"]
    assert bandwidth.RETRY_AFTER - 20 <= b["next_at"] - now <= bandwidth.RETRY_AFTER + 20 and b["down"] == 400.0


def test_the_meter_without_a_total_is_an_empty_bar_with_a_word_and_the_create_window_comes_back():
    js, css = (WEB / "app.js").read_text(encoding="utf-8"), (WEB / "lab.css").read_text(encoding="utf-8")
    meter = js.split("const bwMeter = (t, tone, word, tip) => {", 1)[1].split("const bwMeterFor", 1)[0]
    assert 'if (none) { tone = "pend"; word = bwPending(t) && !(t.bw || {}).error ? "measuring" : "no test"; }' in meter and "pct = !none && u ?" in meter
    assert "html body #page .tun-tbl .bwm.pend .bwm-bar u { background: transparent; }" in css
    opener = js.split("window.openBuild = async () => {", 1)[1].split("window.newTunnel", 1)[0]
    assert 'if (bk.run && bk.run.type === "build" && state.page === "tunnels" && $("#page .tun-box")) {' in opener and "followStory(run.id, box, 600).finally(() => bkRun(null))" in opener
    assert opener.index("bk.run && bk.run.type") < opener.index("Array.isArray(state.serversCache)")          # (before the other checks: the window of a running build comes back first)


def test_the_sink_runs_on_the_python_the_server_has_and_on_python_2():
    """The owner, 2026-10-21: the new tunnel's foreign server had no /usr/bin/python3 - the journal said status=203/EXEC; the capacity never came."""
    text = ssh.render_action("sink_install", {"NAME": "bw8", "PORT": "39777", "KEY": "abcdefgh12", "BIND": "10.1.2.3"})
    assert "for c in python3 python3.12 python3.11 python3.10 python3.9 python3.8 python3.7 python3.6 python python2.7; do" in text and "ExecStart=/usr/bin/python3 /usr/local/sbin/nikatel-sink-bw8.py" in text
    assert 'sed -i "s#^ExecStart=/usr/bin/python3 #ExecStart=$PY #" /etc/systemd/system/nikatel-sink-bw8.service' in text
    assert 'echo "SINK_DOWN this server has no python (the test of the capacity needs it)"' in text and "daemon=True" not in text         # (python 2.7 has no daemon= keyword)
    assert text.index("PY=\"\"; for c in") < text.index("systemctl daemon-reload")                                                     # (the interpreter is chosen before the unit is started)


def test_a_server_with_no_python_is_not_tried_again_every_90_seconds():
    tid = _tunnel("p1", None)
    now = int(time.time())
    bandwidth._failed(tid, "the bandwidth sink did not start on the foreign server: SINK_DOWN this server has no python (the test of the capacity needs it)")

    with session() as db:
        nxt = db.get(Tunnel, tid).record["bw"]["next_at"]

    assert bandwidth.RETRY_AFTER - 20 <= nxt - now <= bandwidth.RETRY_AFTER + 20

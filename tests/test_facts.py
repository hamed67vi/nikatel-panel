import shutil
import subprocess
from pathlib import Path

import pytest

from nikatel.services.facts import parse_facts
from nikatel.ssh import SCRIPTS

SAMPLE = """CPU=1000 800 1100 870
NET=1000000 2000000 1500000 2600000
MEM=2000000 1200000 1000000 900000
DISK=40000000 8000000
LOAD=0.50 0.40 0.30 4
UP=86400
CONN=42
HOST=ir5 6.8.0-31-generic
"""


def test_parse_facts_numbers():
    f = parse_facts(SAMPLE)
    assert f["cpu"] == 30.0                 # (100 - 70 idle) / 100 jiffies
    assert f["ram"] == 40.0
    assert f["disk"] == 20.0
    assert f["swap"] == 10.0
    assert f["rx_mbps"] == 4.0 and f["tx_mbps"] == 4.8
    assert f["rx_bytes"] == 1500000 and f["tx_bytes"] == 2600000          # the counters since boot, as the last read saw them
    assert f["cores"] == 4 and f["conn"] == 42 and f["uptime"] == 86400
    assert f["hostname"] == "ir5" and f["kernel"].startswith("6.8")


def test_parse_facts_incomplete():
    with pytest.raises(ValueError):
        parse_facts("CPU=1 2\n")


def test_cpu_is_the_median_of_the_windows_so_one_burst_does_not_read_as_a_full_server():
    # three one-second windows: 99 %, 30 %, 32 % busy -> the median (32 %), not the first one
    text = SAMPLE.replace("CPU=1000 800 1100 870", "CPU=1000 800 1100 801\nCPU2=1100 801 1200 871\nCPU3=1200 871 1300 939")
    f = parse_facts(text)
    assert 30 <= f["cpu"] <= 32
    # the traffic is the average over the three seconds
    f3 = parse_facts(text + "NETDT=3\n")
    assert f3["rx_mbps"] < parse_facts(text)["rx_mbps"]


def test_the_facts_script_prints_every_counter_whole_and_reads_the_card_of_the_default_route():
    """The owner, 2026-10-15: "show the numbers right". mawk (the awk of many Ubuntu servers) prints a big number as 2.91282e+10, so the rate came out in steps of 1 or 10 MB per three seconds; and the sum of every
    interface counted a GRE link or a tunnel interface a second time. The counters are printed with printf "%.0f" and come from the sysfs counters of the default route's card."""
    script = SCRIPTS["facts"][0]
    assert "print rx+0" not in script and "print $2+" not in script and script.count('printf "%.0f %.0f\\n"') == 2
    assert "/sys/class/net/$IF/statistics/rx_bytes" in script and "/sys/class/net/$IF/statistics/tx_bytes" in script
    assert "gre|gretap|erspan|tun|tap|wg|sit|ip6tnl" in script                                     # the fallback (no default route) leaves the tunnel interfaces out
    assert 'echo "NET=$r1 $r2"' in script and 'echo "NETDT=3"' in script


@pytest.mark.skipif(shutil.which("bash") is None or not Path("/proc/net/dev").exists() or not Path("/proc/stat").exists(), reason="needs a Linux with bash")
def test_the_facts_script_runs_here_and_its_answer_parses():
    out = subprocess.run(["bash", "-c", SCRIPTS["facts"][0]], capture_output=True, text=True, timeout=30, check=False).stdout
    net = next(x for x in out.splitlines() if x.startswith("NET=")).split("=", 1)[1].split()
    assert len(net) == 4 and all(x.isdigit() for x in net)                                         # whole numbers, never 1.23e+10
    f = parse_facts(out)
    assert f["rx_mbps"] >= 0 and f["tx_mbps"] >= 0 and 0 <= f["cpu"] <= 100


def test_a_sample_does_not_hold_a_database_connection_while_the_server_is_asked(client, monkeypatch):
    """Review 2026-10-16: sample_server kept its session (a pool connection) for the whole SSH answer - up to 35 s - and a few slow servers used up the pool."""
    from nikatel import db
    from nikatel.db import session
    from nikatel.models import Server
    from nikatel.services import facts

    with session() as s:
        s.add(Server(name="s1", host="203.0.113.5", role="iran", online=True))
        sid = s.query(Server).one().id

    held = []

    def ask(node, script, *a, **k):
        held.append(db.engine().pool.checkedout())          # connections handed out right now, while "the server" answers
        raise facts.SSHError("ssh connection lost")

    monkeypatch.setattr(facts, "run_script", ask)
    facts.sample_server(sid)
    assert held == [0]
    with session() as s:
        srv = s.query(Server).one()
        assert srv.online is False and "ssh connection lost" in srv.last_error

def test_the_speed_the_card_reports_is_read_and_a_card_that_does_not_say_gives_none():
    """The owner, 2026-10-20: the Bandwidth column of the Lab is the LINE of the server: the speed of its card (/sys/class/net/<card>/speed), -1 on most virtual servers."""
    assert 'echo "LINK=$(cat /sys/class/net/$IF/speed 2>/dev/null || echo -1)"' in SCRIPTS["facts"][0]
    base = SAMPLE if SAMPLE.endswith("\n") else SAMPLE + "\n"
    assert parse_facts(base + "LINK=1000\n")["link_mbps"] == 1000
    assert parse_facts(base + "LINK=-1\n")["link_mbps"] is None and parse_facts(base)["link_mbps"] is None and parse_facts(base + "LINK=oops\n")["link_mbps"] is None
    assert parse_facts(base + "LINK=10000\n")["link_mbps"] == 10000


def test_the_highest_speed_seen_is_kept_for_a_week_and_written_only_when_it_changes():
    import time
    from types import SimpleNamespace

    from nikatel.services import facts

    srv = SimpleNamespace(extra={})
    facts._peak(srv, {"rx_mbps": 3.0, "tx_mbps": 12.5})
    assert srv.extra["peak"]["v"] == 12.5
    keep = srv.extra
    facts._peak(srv, {"rx_mbps": 1.0, "tx_mbps": 2.0})                                                  # lower: the peak stays (and nothing is written)
    assert srv.extra is keep and srv.extra["peak"]["v"] == 12.5
    facts._peak(srv, {"rx_mbps": 40.0, "tx_mbps": 2.0})                                                 # beaten
    assert srv.extra["peak"]["v"] == 40.0
    srv.extra = {"peak": {"v": 40.0, "at": int(time.time()) - facts.PEAK_SECONDS - 10}}
    facts._peak(srv, {"rx_mbps": 1.0, "tx_mbps": 2.0})                                                  # a week old: the peak is renewed with what is seen now
    assert srv.extra["peak"]["v"] == 2.0
    srv.extra = {}
    facts._peak(srv, {"rx_mbps": 0.0, "tx_mbps": 0.0})                                                  # nothing moved: nothing is made up
    assert srv.extra == {}

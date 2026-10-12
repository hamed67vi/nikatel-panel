"""The underuse watch: reading the health probe, saying what is wrong, deciding whether a tuning helped."""
import pytest

from nikatel.services import underuse as uu

OUT = """IF eth0
SPEED 10000
MTU 1500
CC cubic
QDISC pfifo_fast pfifo_fast
RMEM 212992 212992
TCPMEM 6291456 4194304
MTUPROBE 0
CT 1200 262144
TUNED no
MEM 3000000 4000000 0
ST1 100000 100
TCP1 1000000 10000
IF1 5000 5000 0 0 0 0
DOWN 12500000 30000000
ST2 101000 120
TCP2 1020000 10800
IF2 6000 6000 0 0 0 0
"""


def test_parse_reads_the_settings_and_the_deltas():
    p = uu.parse(OUT)
    assert p["iface"] == "eth0" and p["cc"] == "cubic" and p["rmem_max"] == 212992 and p["tuned"] is False and p["speed"] == 10000
    assert p["down_mbit"] == 100.0 and p["down_ok"] is True
    assert p["steal"] == 2.0                      # 20 of 1000 jiffies
    assert p["retrans"] == 4.0                    # 800 of 20000 segments
    assert p["drops"] == 0.0 and p["mem_avail_pct"] == 75.0 and p["ct_used"] == 0.5
    with pytest.raises(ValueError):
        uu.parse("nothing useful")


def test_judge_finds_what_the_kernel_settings_can_fix_and_what_they_cannot():
    p = uu.parse(OUT)
    j = uu.judge(p)
    ids = {x["id"]: x for x in j["findings"]}
    assert ids["congestion"]["fix"] == "tune" and ids["buffers"]["fix"] == "tune" and ids["retrans"]["fix"] is None
    assert j["verdict"] == "provider"             # the lost packets are not the kernel's fault
    only_kernel = uu.judge({**p, "retrans": 0.2})
    assert only_kernel["verdict"] == "tunable" and {x["id"] for x in only_kernel["findings"]} >= {"congestion", "buffers"}
    healthy = uu.judge({**p, "cc": "bbr", "rmem_max": 67108864, "wmem_max": 67108864, "qdisc": ["fq", "fq"], "retrans": 0.1})
    assert healthy["verdict"] == "healthy" and healthy["findings"] == []


def test_a_slow_line_is_slow_by_itself_or_against_the_fleet():
    base = {**uu.parse(OUT), "cc": "bbr", "rmem_max": 67108864, "wmem_max": 67108864, "qdisc": ["fq"], "retrans": 0.1}
    assert any(x["id"] == "speed" for x in uu.judge({**base, "down_mbit": 12.0})["findings"])                      # under 30 Mbit/s
    assert any(x["id"] == "speed" for x in uu.judge({**base, "down_mbit": 100.0}, fleet_down=400.0)["findings"])   # under 40 % of the fleet's median
    assert not any(x["id"] == "speed" for x in uu.judge({**base, "down_mbit": 300.0}, fleet_down=400.0)["findings"])
    assert any(x["id"] == "nospeed" for x in uu.judge({**base, "down_ok": False, "down_mbit": 0.0})["findings"])
    assert uu.judge({**base, "down_ok": False, "down_mbit": 0.0})["verdict"] == "healthy"                          # a speed test that could not run is not a fault
    assert any(x["id"] == "steal" for x in uu.judge({**base, "steal": 9.0})["findings"])
    assert any(x["id"] == "link" for x in uu.judge({**base, "speed": 100})["findings"]) and not any(x["id"] == "link" for x in uu.judge({**base, "speed": -1})["findings"])


def test_a_tuning_is_kept_only_when_it_did_not_make_things_worse():
    b = {"down_mbit": 100.0, "down_ok": True, "retrans": 3.0}
    assert uu.better(b, {"down_mbit": 250.0, "down_ok": True, "retrans": 2.0})[0] is True
    assert uu.better(b, {"down_mbit": 95.0, "down_ok": True, "retrans": 3.5})[0] is True            # within the noise
    ok, why = uu.better(b, {"down_mbit": 60.0, "down_ok": True, "retrans": 3.0})
    assert ok is False and "speed fell" in why
    ok, why = uu.better(b, {"down_mbit": 100.0, "down_ok": True, "retrans": 6.0})
    assert ok is False and "retransmits rose" in why
    assert uu.better({"down_ok": False}, {"down_ok": False})[0] is True                                # nothing measured: nothing got worse


def test_the_fleet_median_needs_three_measurements():
    rows = [{"role": "foreign", "down_ok": True, "down_mbit": v} for v in (100.0, 300.0, 500.0)] + [{"role": "iran", "down_ok": True, "down_mbit": 50.0}]
    assert uu.fleet_median(rows, "foreign") == 300.0 and uu.fleet_median(rows, "iran") is None


def test_the_api_and_the_settings(client):
    assert client.get("/api/analyst/underuse").json()["auto"] is True
    assert client.put("/api/analyst/underuse", json={"auto": False}).json()["auto"] is False
    assert client.post("/api/analyst/server/99/check").status_code == 400            # no such server
    assert client.post("/api/analyst/server/99/tune").status_code == 400

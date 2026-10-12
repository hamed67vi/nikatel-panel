import pytest

from nikatel.services.facts import parse_facts

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
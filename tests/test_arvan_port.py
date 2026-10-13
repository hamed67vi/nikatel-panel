"""Changing the address of an ArvanCloud A record keeps the port (and the weight, the country) of the record."""
from nikatel.services import arvan


def _base(ips_ports):
    return {"type": "a", "name": "uae", "ttl": 120, "cloud": False, "value": [{"ip": ip, "port": port, "weight": 5, "country": "IR"} for ip, port in ips_ports]}


def test_a_new_address_keeps_the_port_of_the_record():
    body = arvan.a_body("uae", ["198.51.100.7"], 120, False, base=_base([("203.0.113.4", 8075)]))
    assert body["value"] == [{"ip": "198.51.100.7", "port": 8075, "weight": 5, "country": "IR"}]


def test_the_same_address_keeps_its_own_item_and_a_second_new_one_takes_the_first_port():
    base = _base([("203.0.113.4", 8075), ("203.0.113.8", 8072)])
    body = arvan.a_body("uae", ["203.0.113.8", "203.0.113.9", "203.0.113.4"], 120, False, base=base)
    got = {v["ip"]: v["port"] for v in body["value"]}
    assert got["203.0.113.8"] == 8072 and got["203.0.113.4"] == 8075 and got["203.0.113.9"] == 8075
    swapped = arvan.a_body("uae", ["10.0.0.1", "10.0.0.2"], 120, False, base=base)          # two new addresses for two old items: by place
    assert [v["port"] for v in swapped["value"]] == [8075, 8072]


def test_a_record_without_a_port_stays_without_one():
    body = arvan.a_body("uae", ["198.51.100.7"], 120, False, base=_base([("203.0.113.4", None)]))
    assert body["value"][0]["port"] is None
    new = arvan.a_body("fresh", ["198.51.100.7"], 120, False)
    assert new["value"][0]["port"] is None


def test_port_and_origin_protocol_can_be_set_cleared_or_kept():
    base = _base([("203.0.113.4", 8075)])
    kept = arvan.a_body("uae", ["203.0.113.4"], 120, False, base=base)
    assert kept["value"][0]["port"] == 8075 and kept["upstream_https"] == "default"
    changed = arvan.a_body("uae", ["203.0.113.4"], 120, False, base=base, port=8072, upstream="https")
    assert changed["value"][0]["port"] == 8072 and changed["upstream_https"] == "https"
    cleared = arvan.a_body("uae", ["203.0.113.4"], 120, False, base=base, port=0)
    assert cleared["value"][0]["port"] is None
    for bad in ({"port": 70000}, {"upstream": "ftp"}):
        try:
            arvan.a_body("uae", ["203.0.113.4"], 120, False, base=base, **bad)
        except ValueError:
            continue

        raise AssertionError(f"{bad} should be refused")

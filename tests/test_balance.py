"""CPU balance: the network work of a server that sits on one core is found (pure judge) and steered to all cores (RPS) by the Doctor."""
from nikatel.services import balance


def _probe(cores, busy, net, rps="0"):
    lines = ["IF eth0", f"CORES {cores}", f"RXQ rx-0 {rps}"]
    t1 = {f"cpu{i}": (1000, 300) for i in range(cores)}
    lines += [f"S1 {c} {a} {b}" for c, (a, b) in t1.items()]
    lines.append("N1 " + " ".join("100" for _ in range(cores)))
    lines += [f"S2 cpu{i} {1000 + 200} {300 + round(200 * (100 - busy[i]) / 100)}" for i in range(cores)]
    lines.append("N2 " + " ".join(str(100 + n) for n in net))
    lines.append("UNIT no")
    return balance.parse("\n".join(lines))


def test_parse_reads_the_cores_the_queues_and_the_load_per_core():
    f = _probe(4, [90, 10, 10, 10], [2000, 20, 20, 20])
    assert f["iface"] == "eth0" and f["cores"] == 4 and f["queues"] == {"rx-0": "0"} and f["rps_on"] is False
    assert f["busy"] == [90.0, 10.0, 10.0, 10.0] and f["net"] == [2000, 20, 20, 20]
    assert balance.parse("IF ens3\nCORES 2\nRXQ rx-0 3\nRXQ rx-1 3")["rps_on"] is True


def test_the_network_work_stuck_on_one_core_is_found():
    skewed, why = balance.judge(_probe(4, [90, 10, 10, 10], [2000, 20, 20, 20]))
    assert skewed and "core 0" in why and "97%" in why
    assert balance.judge(_probe(4, [60, 55, 50, 58], [800, 700, 750, 760]))[0] is False                # spread already
    assert balance.judge(_probe(4, [90, 10, 10, 10], [2000, 20, 20, 20], rps="f"))[0] is False        # RPS is on: nothing to do
    assert balance.judge(_probe(1, [90], [2000]))[0] is False                                         # one core: nothing to spread
    assert balance.judge(_probe(4, [90, 10, 10, 10], [100, 5, 5, 5]))[0] is False                    # no traffic to judge by


def test_the_doctor_turns_rps_on_and_keeps_it_when_it_helped(client, monkeypatch):
    import time

    from nikatel.config import get_settings
    from nikatel.db import session
    from nikatel.models import Server

    real_sleep = time.sleep                                            # the wait inside apply() is skipped, the test's own waiting is not
    monkeypatch.setattr(get_settings(), "read_only", False)
    monkeypatch.setattr(balance.time, "sleep", lambda s: None)

    with session() as db:
        srv = Server(name="ir1", host="203.0.113.5", role="iran", online=True)
        db.add(srv)
        db.flush()
        sid = srv.id

    probes = iter([_probe(4, [90, 10, 10, 10], [2000, 20, 20, 20]), _probe(4, [40, 38, 41, 39], [500, 480, 520, 510], rps="f")])
    seen = []
    monkeypatch.setattr(balance, "probe", lambda node: next(probes))
    monkeypatch.setattr(balance, "run_action", lambda node, name, params: seen.append((name, dict(params))) or "RPS on eth0 (f)")
    aid = balance.apply_start(sid, True, "manual")

    for _ in range(100):
        e = autopilot_status(aid)

        if e and e.get("done"):
            break

        real_sleep(0.1)

    assert seen == [("rps_set", {"IFACE": "eth0", "MODE": "on"})]
    assert e["ok"] is True and any("busiest core 41 %" in line for line in e["story"])
    assert client.get("/api/balance").json()["servers"][str(sid)]["rps_on"] is True


def autopilot_status(aid):
    from nikatel.services import actions

    return actions.status(aid)
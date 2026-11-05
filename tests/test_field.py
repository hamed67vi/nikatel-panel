"""The field test (1.69.231, services/field.py): the result of the Iran test kit is kept compact, judged by the Doctor's rules, told once, and reaches the panel only through a token that may do that one thing."""
import pytest
from fastapi.testclient import TestClient

from nikatel.services import field, tokens

PW = "test-password-1"


@pytest.fixture(autouse=True)
def _fresh_token_clock():
    """`tokens._LAST_USED` is a module-wide dict keyed by token id; a test database starts ids at 1 again, so a token of an earlier test would keep the next one from being stamped as used."""
    tokens._LAST_USED.clear()
    yield
    tokens._LAST_USED.clear()


def _site(ok=True):
    return {"rc": 0 if ok else 28, "code": 200 if ok else 0, "ok": ok}


def _cfg(label, ok=True, bytes3=3_000_000, mbps=20.0, bulk=None, skipped=None, sites=None, kind="entry", lat=None, par=None, video=None, want=None):
    if skipped:
        return {"label": label, "kind": kind, "skipped": skipped}

    q = {s: _site(ok if sites is None else sites.get(s, ok)) for s in field.SITES}
    q["down3MB" if want is None else "download"] = {"bytes": bytes3 if ok else 0, "mbps": mbps, "ok": ok, **({} if want is None else {"want": want})}
    r = {"label": label, "kind": kind, "net": "tcp", "sec": "tls", "host": "h", "port": 1, "quick": q}

    if lat is not None:
        r["latency"] = {"n": 12, "ok": 12 - lat[2], "lost": lat[2], "first_ms": 300, "med": lat[0], "p95": lat[1], "max": lat[1], "sd": 5}

    if par is not None:
        r["parallel"] = {"n": 6, "fail": par[1], "seconds": 2.0, "mbps": par[0]}

    if video is not None:
        r["video"] = {"source": "dl.google.com", "segments": video[1], "seg_ms": [500] * video[1], "startup_s": 1.0, "tiers": {"2.5": {"stalls": 0}, "5": {"stalls": 0 if video[0] >= 5 else 2}}, "best_mbps": video[0], "error": ""}

    if bulk is not None:
        r["bulk"] = bulk
        r["upload"] = {"code": 200, "mbps": 5.0}
        r["exit_ip"] = "198.51.100.7"

    return r


def _bulk(avg=40.0, stalled=False, stall_at=-1, nbytes=25_000_000):
    return {"bytes": nbytes, "seconds": 5.0, "avg_mbps": avg, "per_second_mbps": [avg] * 5, "finished": not stalled, "stalled": stalled, "longest_zero_run_s": 3 if stalled else 0, "bytes_before_first_stall": stall_at, "error": ""}


def _net(key, labels, ok=10, tried=10, med=40.0, sd=5.0, dns=None):
    r = {"label": ",".join(labels), "host": key.split(":")[0], "port": int(key.split(":")[1]), "sec": "tls", "ip": "198.51.100.7", "tcp": {"tried": tried, "ok": ok, "fail": tried - ok, "errors": {"timeout": tried - ok} if tried > ok else {}, "ms": {"n": ok, "med": med, "max": med * 2, "sd": sd}},
         "ping": {"sent": 4, "lost": 0, "rtt": {"med": 30}}}

    if dns is not None:
        r["dns"] = dns

    return r


def _payload(cfgs, nets=(), operator="Irancell", city="Tabriz", mtu=None, connection="mobile", direct=None):
    return {"kit": "1", "mode": "all", "started": "2026-11-05T21:00:00", "operator": operator, "connection": connection, "city": city, "identity": {"ip": "198.51.100.9", "isp": "x", "asn": "AS44244 x", "country": "Iran"}, "ipv6": {"ok": False},
            "mtu": mtu or {"1.1.1.1": {"largest_payload": 1472, "path_mtu": 1500}}, "net": {k: v for k, v in nets}, "configs": {c["label"]: c for c in cfgs}, "direct": direct or {}}


def _good():
    return [_cfg("TCP-3", bulk=_bulk()), _cfg("TCP-4", bulk=_bulk()), _cfg("Cloud-1"), _cfg("LAB-TR1-reality1845", bulk=_bulk()), _cfg("LAB-TR1-xhttp2082", bulk=_bulk())]


def _kinds(findings):
    return {f["kind"] for f in findings}


def test_compact_keeps_what_is_needed_and_refuses_junk():
    for junk in (None, [], {}, {"configs": {}}, {"kit": "1"}, {"kit": "1", "configs": {}, "net": {}}):
        with pytest.raises(ValueError):
            field.compact(junk)

    p = _payload(_good(), operator="Iran<script>cell", connection="satellite")
    p["configs"]["TCP-3"]["bulk"]["per_second_mbps"] = list(range(500))
    p["configs"]["TCP-3"]["label"] = "TCP-3\n\x00evil"
    run = field.compact(p)
    assert run["operator"] == "Iranscriptcell" and run["connection"] == "other" and run["country"] == "Iran"
    assert len(run["cfg"]) == 5 and all("\n" not in c["label"] and "\x00" not in c["label"] for c in run["cfg"])
    assert len(next(c for c in run["cfg"] if c["label"].startswith("TCP-3"))["bulk"]["line"]) == 40
    assert next(c for c in run["cfg"] if c["label"] == "TCP-4")["sites"] == {s: True for s in field.SITES}


def test_a_row_is_ok_partial_slow_stall_closed_or_skipped():
    st = lambda **k: field.row_state(field.compact(_payload([_cfg("A", **k)]))["cfg"][0])
    assert st() == "ok" and st(bulk=_bulk()) == "ok"
    assert st(sites={"x": False}) == "partial"
    assert st(mbps=0.4) == "slow" and st(bulk=_bulk(avg=0.8)) == "slow"
    assert st(bytes3=38_000) == "stall" and st(bulk=_bulk(stalled=True, stall_at=40_000)) == "stall"
    assert st(ok=False) == "closed"
    assert st(skipped="tcp unreachable") == "skipped"


def test_a_healthy_network_has_nothing_to_report():
    run = field.compact(_payload(_good(), [("h:1", _net("h:1", ["TCP-3"]))]))
    assert field.judge(run) == []


def test_a_direct_config_that_stalls_while_another_type_to_the_same_node_passes_is_a_protocol_problem():
    cfgs = [_cfg("TCP-3", bytes3=38_000, bulk=_bulk(stalled=True, stall_at=38_000)), _cfg("LAB-TR1-reality1845", bulk=_bulk()), _cfg("LAB-TR1-xhttp2082", bulk=_bulk()), _cfg("Cloud-1")]
    found = field.judge(field.compact(_payload(cfgs)))
    assert _kinds(found) == {"stall", "protocol"}
    pr = next(f for f in found if f["kind"] == "protocol")
    assert pr["cfgs"] == ["LAB-TR1-reality1845", "LAB-TR1-xhttp2082", "TCP-3"] and "Irancell (Tabriz)" in pr["text"] and "TR1" in pr["text"] and pr["hint"]
    assert "38 KB" in next(f for f in found if f["kind"] == "stall")["text"]
    assert found[0]["kind"] == "protocol"                                  # the comparison is told first


def test_when_every_way_into_the_node_fails_the_address_is_the_problem():
    cfgs = [_cfg("TCP-4", bytes3=20_000), _cfg("LAB-DE1-reality1845", ok=False), _cfg("LAB-DE1-xhttp2082", bytes3=10_000), _cfg("Cloud-1")]
    found = field.judge(field.compact(_payload(cfgs)))
    assert "path" in _kinds(found) and "protocol" not in _kinds(found)
    assert "DE1" in next(f for f in found if f["kind"] == "path")["text"]


def test_blocked_closed_dns_unstable_and_mtu_are_told():
    cfgs = [_cfg("TCP-5", skipped="tcp unreachable"), _cfg("Cloud-2", ok=False), _cfg("Cloud-3", bytes3=50_000), _cfg("Turkey-1")]
    nets = [("a.example:443", _net("a.example:443", ["TCP-5"], ok=0, tried=4)),
            ("b.example:443", _net("b.example:443", ["Turkey-1"], ok=5, tried=10)),
            ("c.example:443", _net("c.example:443", ["Cloud-2"], dns={"system": {"ok": False, "ms": 2000, "ips": []}, "cf": {"ok": True, "ms": 40, "ips": ["203.0.113.5"]}})),
            ("d.example:443", _net("d.example:443", ["Cloud-3"], dns={"system": {"ok": True, "ms": 20, "ips": ["10.10.34.35"]}, "cf": {"ok": True, "ms": 40, "ips": ["203.0.113.5"]}})),
            ("e.example:443", _net("e.example:443", ["Cloud-1"], med=50.0, sd=200.0))]
    found = field.judge(field.compact(_payload(cfgs, nets, mtu={"1.1.1.1": {"largest_payload": 1272, "path_mtu": 1300}})))
    assert _kinds(found) == {"blocked", "closed", "stall", "dns", "unstable", "mtu"}
    assert sum(1 for f in found if f["kind"] == "dns") == 2 and sum(1 for f in found if f["kind"] == "unstable") == 2
    assert "tampered" in next(f["text"] for f in found if f["kind"] == "dns" and "10.10.34.35" in f["text"])
    assert found[0]["level"] == "bad"                                      # most of the configs fail: the network is badly cut


def test_a_config_that_goes_up_and_down_from_run_to_run_is_flaky():
    ok = field.compact(_payload([_cfg("TCP-3"), _cfg("Cloud-1")]))
    bad = field.compact(_payload([_cfg("TCP-3", bytes3=10_000), _cfg("Cloud-1")]))
    assert "flaky" in _kinds(field.judge(bad, [ok, bad])) and "flaky" in _kinds(field.judge(ok, [bad, ok]))          # down, up, down - up, down, up
    assert "flaky" not in _kinds(field.judge(bad, [ok])) and "flaky" not in _kinds(field.judge(ok, [bad, bad]))      # one change: a new failure, a recovery
    assert "flaky" not in _kinds(field.judge(ok, [ok, ok])) and "flaky" not in _kinds(field.judge(bad))

def test_ingest_keeps_a_compact_copy_tells_once_and_tells_when_the_network_is_fine_again(client):
    from nikatel.services import field as f

    bad = _payload([_cfg("TCP-3", bytes3=38_000), _cfg("LAB-TR1-reality1845", bulk=_bulk()), _cfg("Cloud-1")])
    r1 = f.ingest(bad, "token:iran-kit", now=1_000_000)
    assert r1["configs"] == 3 and r1["ok"] == 2 and {x["kind"] for x in r1["findings"]} == {"stall", "protocol"}
    f.ingest(bad, "token:iran-kit", now=1_000_600)                           # the same findings 10 minutes later: no second event
    texts = [e["text"] for e in client.get("/api/events?domain=configs").json()["events"] if e["text"].startswith("Field test")]
    assert len(texts) == 2 and all("Irancell (Tabriz)" in t for t in texts)
    f.ingest(bad, "token:iran-kit", now=1_000_000 + f.TELL_EVERY + 100)      # told again after 12 hours
    assert len([e for e in client.get("/api/events?domain=configs").json()["events"] if e["text"].startswith("Field test")]) == 4
    good = f.ingest(_payload(_good()), "token:iran-kit", now=1_000_000 + f.TELL_EVERY + 5000)
    assert good["findings"] == []
    assert any("passes again" in e["text"] for e in client.get("/api/events?domain=configs").json()["events"])
    s = f.summary(now=1_000_000 + f.TELL_EVERY + 5100)
    assert len(s["networks"]) == 1 and s["networks"][0]["key"] == "Irancell|mobile|Tabriz" and s["matrix"]["TCP-3"]["Irancell|mobile|Tabriz"] == "ok"


def test_one_token_may_send_only_so_many_runs_and_old_runs_go(client, monkeypatch):
    monkeypatch.setattr(field, "PER_HOUR", 2)
    field.ingest(_payload(_good()), "token:a", now=2_000_000)
    field.ingest(_payload(_good()), "token:a", now=2_000_100)

    with pytest.raises(field.TooMany):
        field.ingest(_payload(_good()), "token:a", now=2_000_200)

    field.ingest(_payload(_good()), "token:b", now=2_000_200)               # another token is its own count
    monkeypatch.setattr(field, "KEEP_RUNS", 2)
    field.ingest(_payload(_good()), "token:b", now=2_000_300)
    assert len(field.runs(50)) == 2
    field.ingest(_payload(_good()), "token:b", now=2_000_000 + field.KEEP_DAYS * 86400 + 1000)
    assert len(field.runs(50)) == 1                                           # the older ones are past 60 days


def test_the_doctors_look_reads_what_the_field_says_about_its_configs(client):
    field.ingest(_payload([_cfg("TCP-3", bytes3=38_000), _cfg("Cloud-1")]), "token:iran-kit", now=3_000_000)
    lines = field.story_for(["TCP-3", "Turkey-1"], now=3_000_000 + 600)
    assert len(lines) == 1 and "10 min ago" in lines[0] and "TCP-3" in lines[0]
    assert field.story_for(["Turkey-1"], now=3_000_600) == [] and field.story_for([], now=3_000_600) == []
    assert field.story_for(["TCP-3"], now=3_000_000 + 3 * 86400) == []                    # nothing in the last 48 hours: nothing said


def test_the_kit_reaches_the_panel_only_through_a_field_token(client):
    assert tokens.allowed("field", "POST", "/api/field/ingest") and tokens.allowed("field", "GET", "/api/auth/me")
    assert not tokens.allowed("field", "GET", "/api/field") and not tokens.allowed("field", "GET", "/api/servers") and not tokens.allowed("field", "POST", "/api/tunnels/health-all")
    assert not tokens.allowed("field", "POST", "/api/tokens") and not tokens.allowed("read", "POST", "/api/field/ingest")

    tok = client.post("/api/tokens", json={"name": "iran-kit", "scope": "field", "password": PW})
    assert tok.status_code == 201 and tok.json()["scope"] == "field"
    h = {"Authorization": "Bearer " + tok.json()["token"]}
    m = TestClient(client.app)
    r = m.post("/api/field/ingest", headers=h, json=_payload([_cfg("TCP-3", bytes3=38_000), _cfg("LAB-TR1-reality1845", bulk=_bulk())]))
    assert r.status_code == 200 and r.json()["ok"] and r.json()["id"] > 0 and {f["kind"] for f in r.json()["findings"]} >= {"stall", "protocol"}
    assert m.get("/api/field", headers=h).status_code == 403 and m.get("/api/servers", headers=h).status_code == 403 and m.get("/api/field/runs", headers=h).status_code == 403
    assert m.post("/api/field/ingest", headers=h, json={"nothing": 1}).status_code == 400
    assert m.post("/api/field/ingest", json=_payload(_good())).status_code == 401

    ro = client.post("/api/tokens", json={"name": "bot", "scope": "read", "password": PW}).json()["token"]
    assert m.post("/api/field/ingest", headers={"Authorization": "Bearer " + ro}, json=_payload(_good())).status_code == 403

    s = client.get("/api/field").json()
    assert s["ok"] and len(s["networks"]) == 1 and s["networks"][0]["operator"] == "Irancell" and s["matrix"]["TCP-3"]["Irancell|mobile|Tabriz"] == "stall"
    runs = client.get("/api/field/runs").json()["runs"]
    assert len(runs) == 1 and runs[0]["sender"] == "token:iran-kit" and runs[0]["findings"] >= 2
    one = client.get(f"/api/field/runs/{runs[0]['id']}").json()
    assert one["data"]["operator"] == "Irancell" and len(one["data"]["cfg"]) == 2 and one["findings"]
    assert client.get("/api/field/runs/999999").status_code == 404


def test_a_result_too_big_or_too_often_is_refused(client, monkeypatch):
    tok = client.post("/api/tokens", json={"name": "iran-kit", "scope": "field", "password": PW}).json()["token"]
    h = {"Authorization": "Bearer " + tok}
    m = TestClient(client.app)
    monkeypatch.setattr(field, "MAX_BYTES", 200)
    assert m.post("/api/field/ingest", headers=h, json=_payload(_good())).status_code == 413
    monkeypatch.setattr(field, "MAX_BYTES", 1_500_000)
    monkeypatch.setattr(field, "PER_HOUR", 1)
    assert m.post("/api/field/ingest", headers=h, json=_payload(_good())).status_code == 200
    assert m.post("/api/field/ingest", headers=h, json=_payload(_good())).status_code == 429

def _direct(ports, tls=None, ping=(0, 6), labels=("TCP-3", "LAB-TR1-reality1845", "LAB-TR1-xhttp2082"), cfgports=(8680, 1845, 2082)):
    """One direct address as the kit writes it: ports {port: (ok of 3, errors)}, tls {(port, sni): ok}, ping (lost, sent)."""
    return {"labels": list(labels), "cfgports": list(cfgports), "ports": {str(p): {"ok": ok, "n": 3, "err": err} for p, (ok, err) in ports.items()},
            "tls": {f"{p}|{s}": {"ok": ok, "ms": 50, "err": "" if ok else "handshake", "cert": "CN=x"} for (p, s), ok in (tls or {}).items()}, "ping": {"sent": ping[1], "lost": ping[0]}}


ALL_OPEN = {8680: (3, {}), 1845: (3, {}), 2082: (3, {}), 443: (3, {}), 8443: (3, {}), 80: (0, {"ConnectionRefused": 3})}


def _verdict(d):
    return field.direct_verdict(field.compact(_payload([_cfg("A")], direct={"192.0.2.1": d}))["direct"]["192.0.2.1"])[0]


def test_a_direct_address_is_blocked_filtered_reset_refused_dropped_or_fine():
    timeout = {"timeout": 3}
    dead = {p: (0, timeout) for p in ALL_OPEN}
    assert _verdict(_direct(dead, ping=(6, 6))) == "ipblock"                                                   # nothing answers, not even ping
    assert _verdict(_direct(dead, ping=(0, 6))) == "allports"                                                  # ping answers, every port is dropped
    assert _verdict(_direct({p: (0, {"ConnectionRefused": 3}) for p in ALL_OPEN}, ping=(0, 6))) == "refused"                  # every port refuses: the server is there, nothing listens
    assert _verdict(_direct({**ALL_OPEN, 8680: (0, timeout)})) == "port"                                       # the config's port is dropped, the others answer
    assert _verdict(_direct({**ALL_OPEN, 8680: (0, {"ConnectionReset": 3})})) == "reset"                       # an injected reset
    assert _verdict(_direct({**ALL_OPEN, 8680: (0, {"ConnectionRefused": 3})})) == "refused"                   # the server itself says no: not a filter
    tls_dead = {(8680, "api.example.org"): False, (8680, "www.google.com"): False, (8680, "192.0.2.1"): False}
    assert _verdict(_direct(ALL_OPEN, tls=tls_dead)) == "tls"
    assert _verdict(_direct(ALL_OPEN, tls={(8680, "api.example.org"): False, (8680, "www.google.com"): True, (8680, "192.0.2.1"): True})) == "sni"
    assert _verdict(_direct(ALL_OPEN, tls={(8680, "api.example.org"): True, (8680, "www.google.com"): True})) == "ok"
    assert field.direct_verdict({}) == ("ok", "the IP, its ports and the handshakes all answer")


def test_a_blocked_direct_ip_is_told_once_and_not_again_as_blocked_or_path():
    cfgs = [_cfg("TCP-3", skipped="tcp unreachable"), _cfg("LAB-TR1-reality1845", skipped="tcp unreachable"), _cfg("LAB-TR1-xhttp2082", skipped="tcp unreachable"), _cfg("Cloud-1")]
    dead = {p: (0, {"timeout": 3}) for p in ALL_OPEN}
    nets = [("h:8680", _net("h:8680", ["TCP-3"], ok=0, tried=4)), ("192.0.2.1:1845", _net("192.0.2.1:1845", ["LAB-TR1-reality1845"], ok=0, tried=4))]
    found = field.judge(field.compact(_payload(cfgs, nets, direct={"192.0.2.1": _direct(dead, ping=(6, 6))})))
    assert [f["kind"] for f in found].count("ipblock") == 1 and not {"blocked", "path", "protocol"} & _kinds(found)
    f = next(f for f in found if f["kind"] == "ipblock")
    assert f["level"] == "bad" and "TCP-3" in f["text"] and "looks blocked" in f["text"] and "another IP" in f["hint"]


def test_port_sni_tls_and_refused_findings_have_their_own_words():
    timeout = {"timeout": 3}
    for direct, kind in (({**ALL_OPEN, 8680: (0, timeout)}, "portblock"), ({**ALL_OPEN, 8680: (0, {"ConnectionRefused": 3})}, "refused")):
        found = field.judge(field.compact(_payload([_cfg("TCP-3"), _cfg("Cloud-1")], direct={"192.0.2.1": _direct(direct)})))
        assert kind in _kinds(found) and next(f for f in found if f["kind"] == kind)["level"] == "warn"

    found = field.judge(field.compact(_payload([_cfg("TCP-3")], direct={"192.0.2.1": _direct(ALL_OPEN, tls={(8680, "api.example.org"): False, (8680, "www.google.com"): True})})))
    assert "sniblock" in _kinds(found) and "api.example.org" in next(f for f in found if f["kind"] == "sniblock")["text"]
    found = field.judge(field.compact(_payload([_cfg("TCP-3")], direct={"192.0.2.1": _direct(ALL_OPEN, tls={(8680, "api.example.org"): False, (8680, "www.google.com"): False})})))
    assert "tlsblock" in _kinds(found)


def test_the_experience_numbers_are_kept_and_told_when_they_are_bad():
    run = field.compact(_payload([_cfg("TCP-3", lat=(40.0, 60.0, 0), par=(30.0, 0), video=(25.0, 8)), _cfg("TCP-4", video=(25.0, 2))]))
    c = next(c for c in run["cfg"] if c["label"] == "TCP-3")
    assert c["lat"] == {"n": 12, "lost": 0, "med": 40.0, "p95": 60.0, "first": 300.0} and c["par"] == {"n": 6, "fail": 0, "mbps": 30.0} and c["video"]["best"] == 25.0 and c["video"]["segs"] == 8
    assert next(c for c in run["cfg"] if c["label"] == "TCP-4")["video"] is None                 # fewer than 3 pieces: no verdict on video
    assert field.judge(run) == []

    cfgs = [_cfg("TCP-3", lat=(900.0, 1500.0, 0), par=(30.0, 0), video=(25.0, 8)), _cfg("Cloud-1", lat=(100.0, 2600.0, 1), par=(0.8, 0), video=(2.5, 8)),
            _cfg("Turkey-1", lat=(100.0, 200.0, 3), par=(20.0, 1), video=(0.0, 8)), _cfg("Cloud-2", lat=(100.0, 200.0, 0), par=(20.0, 0), video=(8.0, 8))]
    found = field.judge(field.compact(_payload(cfgs)))
    assert _kinds(found) == {"lag", "video", "images"}
    assert next(f for f in found if f["kind"] == "lag")["cfgs"] == ["Cloud-1", "TCP-3", "Turkey-1"]
    vt = next(f for f in found if f["kind"] == "video")
    assert vt["cfgs"] == ["Cloud-1", "Turkey-1"] and "Cloud-1 (only 720p)" in vt["text"] and "Turkey-1 (only below 720p)" in vt["text"]
    assert next(f for f in found if f["kind"] == "images")["cfgs"] == ["Cloud-1", "Turkey-1"]
    assert [field.tier_name(x) for x in (0, 2.5, 5, 8, 16, 25, 40)] == ["below 720p", "720p", "1080p", "1440p", "4K (light)", "4K", "4K"]


def test_the_overview_tells_the_verdict_of_every_direct_address(client):
    dead = {p: (0, {"timeout": 3}) for p in ALL_OPEN}
    field.ingest(_payload([_cfg("TCP-3", skipped="tcp unreachable")], direct={"192.0.2.1": _direct(dead, ping=(6, 6))}), "token:iran-kit", now=4_000_000)
    s = field.summary(now=4_000_100)
    assert s["networks"][0]["direct"] == [{"ip": "192.0.2.1", "labels": ["TCP-3", "LAB-TR1-reality1845", "LAB-TR1-xhttp2082"], "code": "ipblock", "words": "nothing answers, no port and no ping: the IP looks blocked from this network"}]

def _pget(works=True):
    return {"rc": 0, "code": 200 if works else 0, "ms": 300, "links": 28 if works else 0}


def _pname(works=True, tls_ok=True, tls_err="", sys_ok=True, sys_ip="9.9.9.9", cf_ok=True):
    return {"dns": {"system": {"ok": sys_ok, "ms": 40, "ips": [sys_ip] if sys_ok else []}, "cf": {"ok": cf_ok, "ms": 60, "ips": ["9.9.9.9"]}}, "ip": sys_ip,
            "tcp": {"ok": 3, "n": 3, "err": {}}, "tls": {"ok": tls_ok, "err": tls_err, "issuer": "CN=R3"}, "http": _pget(works)}


def _addr(ok=True, lost=0):
    return {"ip": "192.0.2.10", "ping": {"sent": 4, "lost": lost}, "tcp443": {"ok": 3 if ok else 0, "n": 3, "err": {} if ok else {"timeout": 3}}, "tcp80": {"ok": 3 if ok else 0, "n": 3, "err": {}}}


def _panel_run(names, matrix, addrs):
    p = _payload(_good())
    p["panel"] = {"names": names, "ips": addrs, "matrix": {k: _pget(v) for k, v in matrix.items()}}
    return field.compact(p)


ADDRS = {"Panel-new": _addr(), "boxB": _addr(), "iran-forwarder": _addr(), "monitor-host": _addr()}


def test_a_panel_that_opens_everywhere_has_nothing_to_report():
    run = _panel_run({"sub-a.example": _pname(), "monitor.example": _pname(works=False)},                      # (the monitor has no subscription: it is not fetched through every address, so it is not judged)
                     {"sub-a.example|Panel-new": True, "sub-a.example|boxB": True}, ADDRS)
    assert field.judge(run) == []
    assert field.panel_view(run) == {"names": {"sub-a.example": "ok"}, "addresses": {"Panel-new": "ok", "boxB": "ok", "iran-forwarder": "ok", "monitor-host": "ok"}}


def test_a_panel_address_that_does_not_answer_and_a_name_that_opens_only_through_another_address_are_told():
    addrs = {**ADDRS, "Panel-new": _addr(ok=False, lost=4)}
    run = _panel_run({"sub-b.example": _pname(works=False), "sub-a.example": _pname()},
                     {"sub-b.example|Panel-new": False, "sub-b.example|boxB": True, "sub-b.example|iran-forwarder": True, "sub-a.example|Panel-new": True}, addrs)
    found = field.judge(run)
    assert _kinds(found) == {"panelip", "panelname"}
    ip = next(f for f in found if f["kind"] == "panelip")
    assert ip["level"] == "bad" and ip["cfgs"] == ["Panel-new"] and "boxB" in ip["text"] and "ping lost 4 of 4" in ip["text"]
    nm = next(f for f in found if f["kind"] == "panelname")
    assert nm["cfgs"] == ["sub-b.example"] and "boxB, iran-forwarder" in nm["text"] and nm["level"] == "bad"
    assert field.panel_view(run)["names"] == {"sub-b.example": "fail", "sub-a.example": "ok"} and field.panel_view(run)["addresses"]["Panel-new"] == "dead"
    monitor_down = field.judge(_panel_run({"sub-a.example": _pname()}, {"sub-a.example|Panel-new": True}, {**ADDRS, "monitor-host": _addr(ok=False, lost=4)}))
    assert next(f for f in monitor_down if f["kind"] == "panelip")["level"] == "warn"                            # the monitor is not what customers use


def test_a_name_that_opens_through_no_address_is_filtered_or_simply_not_served():
    every_way = {"sub-a.example|Panel-new": False, "sub-a.example|boxB": False}
    filtered = field.judge(_panel_run({"sub-a.example": _pname(works=False, tls_ok=False, tls_err="The decryption operation failed")}, every_way, ADDRS))
    assert _kinds(filtered) == {"panelblock"} and filtered[0]["level"] == "bad" and "sub-a.example" in filtered[0]["text"]
    unserved = field.judge(_panel_run({"sub-a.example": _pname(works=False, tls_ok=False, tls_err="The message received was unexpected or badly formatted")}, every_way, ADDRS))
    assert _kinds(unserved) == {"panelnotserved"} and "not a filter" in unserved[0]["text"] and unserved[0]["level"] == "warn"


def test_the_dns_of_a_network_that_cannot_resolve_or_lies_about_a_subscription_name_is_told():
    ok_via = {"sub-c.example|Panel-new": True}
    none = field.judge(_panel_run({"sub-c.example": _pname(works=False, sys_ok=False)}, ok_via, ADDRS))
    assert _kinds(none) == {"paneldns", "panelname"} and "no answer" in next(f for f in none if f["kind"] == "paneldns")["text"]
    lies = field.judge(_panel_run({"sub-c.example": _pname(works=False, sys_ip="10.10.34.35")}, ok_via, ADDRS))
    assert "answers 10.10.34.35" in next(f for f in lies if f["kind"] == "paneldns")["text"]
    assert field.judge(_panel_run({"sub-c.example": _pname(sys_ip="8.8.8.8")}, ok_via, ADDRS)) == []     # another real address than 1.1.1.1 gives is no sign of tampering (a CDN has many)


def test_the_panel_section_of_a_result_is_bounded_and_ignores_junk():
    p = _payload(_good())
    p["panel"] = {"names": {"a.example": "junk", "b.example": {"dns": "x", "tcp": 5, "http": {"rc": "x", "code": 99999, "links": -4}}}, "ips": {"x": 7, "Panel-new": _addr()}, "matrix": {"b.example|x": {"rc": 0, "code": 200, "links": 9}, "bad": 3}}
    run = field.compact(p)
    assert list(run["panel"]["names"]) == ["b.example"] and run["panel"]["names"]["b.example"]["tcp"] is None and run["panel"]["names"]["b.example"]["http"] == {"rc": -1, "code": 999, "ms": None, "links": 0}
    assert list(run["panel"]["ips"]) == ["Panel-new"] and list(run["panel"]["matrix"]) == ["b.example|x"]
    assert field.compact(_payload(_good()))["panel"] == {"names": {}, "ips": {}, "matrix": {}}                  # an older kit has no panel section

def test_the_quick_download_may_be_smaller_than_it_used_to_be():
    ok = field.compact(_payload([_cfg("A", want=1_000_000, bytes3=1_000_000)]))["cfg"][0]
    assert ok["want"] == 1_000_000 and field.row_state(ok) == "ok" and set(ok["sites"]) == set(field.SITES)       # (the download is not one of the sites)
    run = field.compact(_payload([_cfg("A", want=1_000_000, bytes3=30_000)]))
    assert field.row_state(run["cfg"][0]) == "stall"
    assert "30 KB of a 1.0 MB download" in next(f for f in field.judge(run) if f["kind"] == "stall")["text"]
    assert field.compact(_payload([_cfg("A")]))["cfg"][0]["want"] == 3_000_000                                       # a kit of before asked for 3 MB

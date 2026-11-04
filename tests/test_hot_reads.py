"""The hot reads of the live panel (py-spy, 2026-11-04): a settings document parsed hundreds of times a turn, a reconnect that waited inside the database session of the panel check, and a whole week of
server samples held as objects. Each of them has a test that keeps it fixed."""
from contextlib import contextmanager

from nikatel.services import panels, trident, weather
from nikatel.services import settings as st


def test_peek_parses_once_and_put_drops_it():
    st.put("trident", {"tunnels": {"a": {"on": True}}})
    first = st.peek("trident")
    assert first["tunnels"] == {"a": {"on": True}}
    assert st.peek("trident") is first                    # the same document, no second read

    st.put("trident", {"tunnels": {"b": {"on": True}}})
    again = st.peek("trident")
    assert again is not first and again["tunnels"] == {"b": {"on": True}}          # a put is seen at once, not after the second


def test_peek_forgets_after_its_time(monkeypatch):
    st.put("trident", {"touched": [1]})
    first = st.peek("trident")
    monkeypatch.setattr(st, "PEEK_TTL", 0.0)
    assert st.peek("trident") is not first
    assert st.peek("trident")["touched"] == [1]


def test_the_readers_hand_out_copies_not_the_shared_document():
    st.put("trident", {"tunnels": {"t1": {"on": True}}, "applied": {"t1": {"w": [0.5, 0.5], "state": "active"}},
                       "weather": {weather.route_key(1, 2): {"gre": {"state": "blocked"}}}, "pattern": {weather.route_key(1, 2): {"gre": {"blocked": [0] * 24}}}})
    a = trident._applied("t1")
    a["w"].append(9.0)
    a["state"] = "changed"
    assert trident._applied("t1") == {"w": [0.5, 0.5], "state": "active"}          # what the caller does to its copy never reaches the next reader

    m = trident.marks()
    m["t1"]["on"] = False
    assert trident.marks() == {"t1": {"on": True}}

    w = weather.weather_of(1, 2)
    w["gre"]["state"] = "open"
    assert weather.weather_of(1, 2) == {"gre": {"state": "blocked"}}
    assert weather.pattern_of(1, 2, "gre") == {"blocked": [0] * 24}


def test_a_read_after_a_write_sees_the_write():
    trident._set_applied("t9", {"state": "active"})
    assert trident._applied("t9") == {"state": "active"}
    trident._set_applied("t9", None)
    assert trident._applied("t9") == {}


def test_the_reconnect_is_asked_outside_the_database_session(client, monkeypatch):
    """A node in trouble is asked to reconnect; the answer can take 20 s - not while the session of the check holds the one write lock of SQLite."""
    from nikatel.config import get_settings

    monkeypatch.setenv("NIKATEL_READ_ONLY", "false")
    get_settings.cache_clear()
    monkeypatch.setattr(panels, "_token", lambda c, fresh=False: "t")
    monkeypatch.setattr(panels, "_login", lambda c: "tok.tok.tok")
    sysd = {"version": "0.8.4", "total_user": 1, "users_active": 1, "online_users": 0, "cpu_usage": 1, "mem_total": 10, "mem_used": 1, "incoming_bandwidth": 0, "outgoing_bandwidth": 0}
    nodes = [{"id": 1, "name": "ok", "address": "2.2.2.2", "status": "connected"}, {"id": 2, "name": "broken", "address": "3.3.3.3", "status": "error", "message": "timeout"}]

    def fake(c, method, path, **kw):
        return sysd if path == "/api/system" else nodes if path == "/api/nodes" else {}

    monkeypatch.setattr(panels, "call", fake)
    r = client.post("/api/panels", json={"name": "shop", "type": "marzban", "url": "https://1.1.1.1:8000/dashboard", "username": "admin", "password": "pw"})
    assert r.status_code == 200, r.text

    depth, seen, real = {"n": 0}, [], panels.session

    @contextmanager
    def counted():
        depth["n"] += 1

        try:
            with real() as db:
                yield db
        finally:
            depth["n"] -= 1

    monkeypatch.setattr(panels, "session", counted)
    monkeypatch.setattr(panels, "node_reconnect", lambda c, node_id: seen.append((node_id, depth["n"])))
    panels.check_all()
    panels.check_all()                                    # the second failed look is the one that asks
    assert seen == [(2, 0)]                               # asked once, with no session open

    shown = client.get("/api/panels").json()["panels"][0]["nodes"]
    bad = next(n for n in shown if n["name"] == "broken")
    assert bad["fails"] == 2 and bad["reconnect_at"] > 0          # and the time of the ask is kept for the 30 s rule


def test_the_analysis_reads_samples_as_light_rows():
    from nikatel.db import session
    from nikatel.models import Server, ServerSample
    from nikatel.services import analyst

    now = 1_800_000_000

    with session() as db:
        s = Server(name="iran-a", host="192.0.2.10", role="iran", ssh_user="root", ssh_port=22)
        db.add(s)
        db.flush()
        db.add_all([ServerSample(server_id=s.id, ts=now - 60 * i, cpu=10.0 + i, ram=40.0, disk=5.0, load1=0.5, rx_mbps=2.0, tx_mbps=3.0, conn=10) for i in range(5)])

    analyst._CACHE.clear()
    out = analyst.overview(7)
    row = next(x for x in out["servers"] if x["name"] == "iran-a")
    assert row["samples"] == 5 and row["peak_mbps"] == 5.0 and row["peak_cpu_avg"] == 12.0          # the figures come out of the light rows as they did out of the objects (cpu 10, 11, 12, 13, 14)

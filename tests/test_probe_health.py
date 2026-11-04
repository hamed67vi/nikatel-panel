"""The Doctor watches the REAL configs (1.69.215; the owner, 2026-11-04: "the Doctor must always measure the quality"). On 2026-11-04 the Configs monitoring was found blind: the probe's test user had been `limited` (5 GB used) since
10-17, so every full test of every config failed for 18 days, and twenty configs of the real subscription were never in the list at all. Here: the test user is kept fit, the groups follow their subscriptions, the probe's own
bad minutes are not held against a config, and a config whose answers jump (a stall of seconds) is called jumpy."""
from sqlalchemy import select

from nikatel.db import session
from nikatel.models import Event, Subscription
from nikatel.services import configs, panels, userq

NOW = 1_800_000_000


# ------------------------------------------------------------------ the probe's own bad minutes
def test_a_bucket_in_which_most_configs_failed_at_once_is_the_probes_fault():
    b = NOW // userq.BUCKET
    rows = [(NOW, i, i < 2) for i in range(8)]                                       # eight configs tested, six failed
    assert userq.mass_failures(rows) == {b}
    assert userq.mass_failures([(NOW, i, i != 0) for i in range(8)]) == set()        # one failed: that one is its own
    assert userq.mass_failures([(NOW, i, False) for i in range(4)]) == set()         # four tested only: too few to blame the probe
    assert userq.mass_failures([(NOW, 1, False), (NOW + 5, 1, True)] + [(NOW, i, True) for i in range(2, 9)]) == set()      # a config that answered once in the bucket did answer


def test_the_tests_of_the_probes_bad_minutes_are_not_counted():
    bad = NOW - 600
    samples = [(NOW - 60 * i, True, 300.0) for i in range(30)] + [(bad - 10 * i, False, None) for i in range(10)]
    s = userq.config_stats(samples, NOW, 300.0)
    assert s["up_share"] < 0.85                                                      # held against it: unstable
    s = userq.config_stats(samples, NOW, 300.0, {bad // userq.BUCKET - 1, bad // userq.BUCKET})
    assert s["up_share"] == 1.0                                                      # the probe's minutes left out: all its own tests answered


# ------------------------------------------------------------------ jumps
def _answers(n, ping=300.0, stalls=0, stall_ping=4000.0):
    stalled = {9 * k for k in range(stalls)}                                         # spread over the window, as real stalls are (the newest answers are the usual ones)
    return [(NOW - 60 * i, True, stall_ping if i in stalled else ping) for i in range(n)]


def test_stalls_of_seconds_make_a_config_jumpy():
    share, stalls = userq.stall_share([300.0] * 40 + [4000.0] * 5)
    assert stalls == 5 and round(share, 2) == 0.11
    assert userq.stall_share([300.0] * 10) == (None, 0)                              # too few answers to say
    assert userq.stall_share([300.0] * 40 + [4000.0] * 2)[0] < userq.STALL_SHARE     # two in forty-two: a hiccup, not a jumpy line
    assert userq.stall_share([300.0] * 40 + [650.0] * 5)[0] == 0                     # twice the usual, only 350 ms more: not a stall


def test_a_jumpy_config_is_told_and_a_steady_one_is_not():
    jumpy = userq.config_stats(_answers(45, stalls=5), NOW, 300.0)
    assert userq.config_issue("up", 99999, jumpy)[0] == "jumpy" and "5 of its last 45" in userq.config_issue("up", 99999, jumpy)[1]
    steady = userq.config_stats(_answers(45), NOW, 300.0)
    assert userq.config_issue("up", 99999, steady) == ("good", "")


def test_when_most_configs_jump_at_once_it_is_the_probe_not_them():
    assert userq.probe_noisy([0.3, 0.3, 0.0, None]) is True
    assert userq.probe_noisy([0.3, 0.0, 0.0, 0.0]) is False
    assert userq.probe_noisy([0.3, 0.3]) is False                                    # two configs: nothing to compare
    stats = {**userq.config_stats(_answers(45, stalls=5), NOW, 300.0), "noisy": True}
    assert userq.config_issue("up", 99999, stats) == ("good", "")


# ------------------------------------------------------------------ the test user of the probe
def test_the_test_user_must_be_active_without_quota_and_with_time_left():
    now = 1_793_800_000                                                              # 2026-11-04
    assert panels.test_user_needs({"status": "limited", "data_limit": 5368709120}, now) == "it is limited"
    assert panels.test_user_needs({"status": "active", "data_limit": 5368709120}, now).startswith("it has a data quota")
    assert panels.test_user_needs({"status": "active", "data_limit": 0, "expire": "2027-11-04T00:00:00Z"}, now) == ""
    assert panels.test_user_needs({"status": "active", "expire": "2026-11-08T22:16:16Z"}, now) == "it expires within two weeks"
    assert panels.test_user_needs({"status": "active", "expire": now + 3 * 86400}, now) == "it expires within two weeks"
    assert panels.test_user_needs({"status": "active", "expire": 0}, now) == ""      # Marzban: 0 = never
    assert panels.test_user_needs({"status": "active", "expire": now + 90 * 86400}, now) == ""


def _panel_with_test_user(client, monkeypatch):
    monkeypatch.setattr(panels, "_token", lambda c, fresh=False: "t")
    monkeypatch.setattr(panels, "_login", lambda c: "tok.tok.tok")
    monkeypatch.setattr(panels, "call", lambda c, m, p, **kw: {"version": "1", "total_user": 1} if p == "/api/system" else [] if p == "/api/nodes" else (_ for _ in ()).throw(panels.PanelError("HTTP 404", 404)))
    pid = client.post("/api/panels", json={"name": "shop", "type": "marzban", "url": "https://1.1.1.1", "username": "a", "password": "p"}).json()["panel"]["id"]
    monkeypatch.setattr(panels, "test_user", lambda c, name, **kw: {"username": "nikatel-test-shop", "sub": "https://1.1.1.1/sub/x"})
    monkeypatch.setattr(configs, "import_subscription", lambda name, url, picks=None, prefix="": {"added": [], "skipped": [], "failed": []})
    assert client.post(f"/api/panels/{pid}/test-config").status_code == 200
    return pid


def test_the_job_renews_a_limited_test_user_and_says_so_and_leaves_a_fit_one(client, monkeypatch):
    _panel_with_test_user(client, monkeypatch)
    monkeypatch.setattr("nikatel.jobs.stopping", lambda: False)
    renewed, state = [], {"u": {"status": "limited", "data_limit": 5368709120}}
    monkeypatch.setattr(panels, "call", lambda c, m, p, **kw: state["u"])
    monkeypatch.setattr(panels, "test_user", lambda c, name, **kw: renewed.append((name, kw.get("uname"))) or {})
    panels.keep_test_users()
    assert renewed == [("shop", "nikatel-test-shop")]
    with session() as db:
        said = [e.text for e in db.scalars(select(Event)) if "test user" in e.text]

    assert said and "limited" in said[-1] and "renewed" in said[-1]
    state["u"] = {"status": "active", "data_limit": 0, "expire": "2099-01-01T00:00:00Z"}
    panels.keep_test_users()
    assert len(renewed) == 1                                                         # fit: left alone


# ------------------------------------------------------------------ the groups follow their subscriptions
def test_the_usage_line_of_the_test_user_is_no_config():
    assert configs.is_info_node("nikatel-test-nikatel-bot 3.95 GB") and configs.is_info_node("nikatel-test-nikatel-bot 366 D")
    assert not configs.is_info_node("Turkey-1") and not configs.is_info_node("")


def test_every_group_with_a_saved_link_follows_it_by_itself(client, monkeypatch):
    monkeypatch.setattr("nikatel.jobs.stopping", lambda: False)

    with session() as db:
        db.add_all([Subscription(name="one", url="https://x.example/sub/1", nodes=[], excluded=[]), Subscription(name="two", url="https://x.example/sub/2", nodes=[], excluded=[]), Subscription(name="none", url="", nodes=[], excluded=[])])

    seen = []
    monkeypatch.setattr(configs, "sync_subscription", lambda name: seen.append(name) or {})
    configs.sync_all_subscriptions()
    assert sorted(seen) == ["one", "two"]                                            # the one without a link is skipped
    monkeypatch.setattr(configs, "sync_subscription", lambda name: (_ for _ in ()).throw(RuntimeError("could not download")))
    configs.sync_all_subscriptions()                                                 # a link that fails is logged, the job goes on

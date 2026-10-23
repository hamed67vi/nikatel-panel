"""The Bale relay (relay/bale_relay.py), the program that runs on an Iran server and talks to the customers while the international internet is cut.
A fake Bale API and a fake panel (one local HTTP server) stand in for the two; nothing leaves the machine."""
import http.server
import importlib.util
import json
import os
import re
import threading
import time
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parent.parent / "relay" / "bale_relay.py"
TOKEN = "1234567:AAbbCCddEEffGGhhIIjjKKllMMnnOOppQQ"
GIB = 1073741824


def load():
    spec = importlib.util.spec_from_file_location("bale_relay_under_test", SRC)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


# ------------------------------------------------------------------ the fake world
class Fake(http.server.ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self):
        super().__init__(("127.0.0.1", 0), Handler)
        self.updates, self.sent, self.calls, self.panel, self.blocked, self.hits = [], [], [], {}, set(), []
        self.mids, self.copies, self.copy_ids, self.next_mid = [], [], [], 1000         # mids: the id of every message that was sent (parallel to `sent`); copies: (to, from, message id) of every copyMessage
        self.token = TOKEN
        threading.Thread(target=self.serve_forever, daemon=True).start()

    @property
    def port(self):
        return self.server_address[1]


class Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, code, doc):
        raw = json.dumps(doc).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):              # the panel: /sub/<token>/info
        self.server.hits.append(self.path)
        m = re.fullmatch(r"/sub/([^/]+)/info", self.path)
        doc = self.server.panel.get(m.group(1)) if m else None
        self._send(200, doc) if doc else self._send(404, {"detail": "Not found"})

    def do_POST(self):             # Bale: /bot<token>/<method>
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
        m = re.fullmatch(r"/bot([^/]+)/(\w+)", self.path)
        self.server.calls.append((m.group(2), body))

        if m.group(1) != self.server.token:
            return self._send(401, {"ok": False, "error_code": 401, "description": "Unauthorized"})

        if m.group(2) == "getMe":
            return self._send(200, {"ok": True, "result": {"id": 1, "username": "nikatest_bot"}})

        if m.group(2) == "getUpdates":
            ups, self.server.updates = self.server.updates, []
            return self._send(200, {"ok": True, "result": ups})

        if m.group(2) == "sendMessage":
            if body["chat_id"] in self.server.blocked:
                return self._send(403, {"ok": False, "error_code": 403, "description": "Forbidden: bot was blocked by the user"})

            self.server.sent.append((body["chat_id"], body["text"]))
            self.server.next_mid += 1
            self.server.mids.append(self.server.next_mid)
            return self._send(200, {"ok": True, "result": {"message_id": self.server.next_mid}})

        if m.group(2) == "copyMessage":
            if body["chat_id"] in self.server.blocked:
                return self._send(403, {"ok": False, "error_code": 403, "description": "Forbidden: bot was blocked by the user"})

            self.server.copies.append((body["chat_id"], body["from_chat_id"], body["message_id"]))
            self.server.next_mid += 1
            self.server.copy_ids.append(self.server.next_mid)
            return self._send(200, {"ok": True, "result": {"message_id": self.server.next_mid}})

        self._send(404, {"ok": False, "error_code": 404, "description": "no such method"})


def update(uid, chat, text, first="Ali", **extra):
    return {"update_id": uid, "message": {"chat": {"id": chat, "type": "private"}, "from": {"first_name": first}, "text": text, **extra}}


class Rig:
    def __init__(self, m, fake, tmp_path):
        self.m, self.fake, self.tmp = m, fake, tmp_path
        self.cfg_path = tmp_path / "config.json"
        self.set_config()
        self.relay = m.Relay(m.Config(str(self.cfg_path)))
        self.relay.store.init()

    def set_config(self, **kw):
        doc = {"token": TOKEN, "api_base": f"http://127.0.0.1:{self.fake.port}", "admins": [900], "allowed_hosts": [f"127.0.0.1:{self.fake.port}"], "watch": [], "pace": 0.02, **kw}
        self.cfg_path.write_text(json.dumps(doc), encoding="utf-8")
        self.n = getattr(self, "n", 0) + 1
        os.utime(self.cfg_path, (1_700_000_000 + self.n, 1_700_000_000 + self.n))      # (a new modification time every time, so that the program reads the file again)

    def sub(self, token="tok-ali-12345", **info):
        self.fake.panel[token] = {"username": "ali", "status": "active", "expire": int(time.time()) + 5 * 86400 + 60, "data_limit": 50 * GIB, "used_traffic": 10 * GIB, **info}
        return f"http://127.0.0.1:{self.fake.port}/sub/{token}"

    def last(self):
        return self.fake.sent[-1]


@pytest.fixture
def rig(tmp_path, monkeypatch):
    m = load()
    monkeypatch.setattr(m, "HOME", str(tmp_path))
    monkeypatch.setattr(m, "DATA", str(tmp_path / "data"))
    monkeypatch.setattr(m, "OUTBOX", str(tmp_path / "outbox"))
    (tmp_path / "data").mkdir()
    (tmp_path / "outbox").mkdir()
    m.STOP.clear()
    fake = Fake()
    yield Rig(m, fake, tmp_path)
    fake.shutdown()
    fake.server_close()


# ------------------------------------------------------------------ the rules
def test_a_subscription_link_is_accepted_only_on_the_hosts_the_owner_named():
    m = load()
    allowed = ["sub.example.test", "panel.example.test:2096"]
    info = "https://sub.example.test/sub/abcdEFGH1234/info"

    assert m.link_target("https://sub.example.test/sub/abcdEFGH1234", allowed) == ("sub.example.test", info)
    assert m.link_target("https://SUB.example.test/sub/abcdEFGH1234/", allowed)[1] == info
    assert m.link_target("https://sub.example.test/sub/abcdEFGH1234/info", allowed)[1] == info
    assert m.link_target("https://sub.example.test/sub/abcdEFGH1234/sing-box", allowed)[1] == info          # the client-type tail of a PasarGuard link
    assert m.link_target("https://panel.example.test:2096/sub/tok12345678", allowed)[1] == "https://panel.example.test:2096/sub/tok12345678/info"

    for bad in ("https://evil.example.test/sub/abcdEFGH1234",                    # another host: the relay never fetches what a customer names
                "https://sub.example.test@evil.test/sub/abcdEFGH1234",           # a name hidden in front of an @
                "https://evil.test@sub.example.test/sub/abcdEFGH1234",
                "https://sub.example.test/sub/abc",                              # too short to be a token
                "https://sub.example.test/sub/../../etc/passwd1234",
                "ftp://sub.example.test/sub/abcdEFGH1234",
                "https://sub.example.test/sub/abcdEFGH1234?x=1",
                "https://sub.example.test/sub/abcdEFGH1234#frag",
                "https://panel.example.test:2097/sub/tok12345678",               # another port than the one named
                "https://panel.example.test/sub/tok12345678",
                "https://sub.example.test:99999/sub/abcdEFGH1234",
                "https://localhost/sub/abcdEFGH1234", "javascript:alert(1)", ""):
        assert m.link_target(bad, allowed) is None, bad

    assert m.link_target("https://sub.example.test/sub/abcdEFGH1234", []) is None


def test_the_first_link_in_a_message_is_taken_without_its_punctuation():
    m = load()
    assert m.extract_url("لینک من: https://a.example.test/sub/abcdefgh12.") == "https://a.example.test/sub/abcdefgh12"
    assert m.extract_url("(https://a.example.test/sub/abcdefgh12)،") == "https://a.example.test/sub/abcdefgh12"
    assert m.extract_url("no link here") == ""


def test_the_look_at_the_outside_world_calls_a_cut_once_and_the_return_once():
    m = load()
    cfg = {"cut_after": 180, "restore_after": 120, "cooldown": 21600}
    st, t0 = {}, 1_000_000
    step = lambda up, dt: m.watch_step(st, up, t0 + dt, cfg)    

    assert step(False, 0) == [] and step(False, 100) == [] and step(False, 179) == []
    assert step(False, 180) == ["cut"]
    assert step(False, 200) == [] and step(False, 400) == []                  # told once
    assert step(True, 410) == [] and step(True, 500) == []                    # back, but not for long enough
    assert step(False, 510) == []                                              # a blip in the return: the wait starts again
    assert step(True, 520) == [] and step(True, 639) == []
    assert step(True, 640) == ["restored"]
    assert step(True, 700) == []                                               # told once

    cuts = [dt for dt in range(900, 21600 + 600, 60) if step(False, dt) == ["cut"]]          # a new cut is not announced again inside the cooldown (6 h after the first notice)
    assert cuts == [21780]


def test_a_short_loss_is_not_a_cut():
    m = load()
    cfg = {"cut_after": 180, "restore_after": 120, "cooldown": 0}
    st, t0 = {}, 5_000_000

    for i in range(0, 600, 15):                       # 60 s down, 60 s up, over and over
        assert m.watch_step(st, (i // 60) % 2 == 1, t0 + i, cfg) == []


def test_after_the_relay_was_off_the_old_timers_mean_nothing():
    m = load()
    cfg = {"cut_after": 180, "restore_after": 120, "cooldown": 0}
    st = {"down_since": 1000, "seen_at": 1000}
    assert m.watch_step(st, False, 1000 + 4000, cfg) == []                      # it was off for an hour: the cut is counted from now
    assert st["down_since"] == 1000 + 4000


def test_the_status_text_shows_the_state_and_keeps_the_user_name_as_it_is():
    m = load()
    t = 2_000_000
    info = {"status": "active", "expire": t + 5 * 86400 + 30, "data_limit": 50 * GIB, "used_traffic": 10 * GIB}
    txt = m.fmt_status({"username": "ali123", "info": json.dumps(info), "info_at": t - 3600}, t)
    assert "ali123" in txt and "فعال" in txt and "۴۰.۰ گیگ مانده از ۵۰" in txt and "۵ روز مانده" in txt and "۶۰ دقیقه پیش" in txt

    free = m.fmt_status({"username": "u", "info": json.dumps({"status": "expired", "expire": t - 10, "data_limit": 0, "used_traffic": 5}), "info_at": t}, t)
    assert "نامحدود" in free and "منقضی‌شده" in free and "تاریخ انقضا گذشته" in free and "هم‌اکنون" in free

    none = m.fmt_status({"username": "u", "info": "not json", "info_at": 0}, t)
    assert "؟" in none and "بدون تاریخ انقضا" in none


def test_a_config_file_with_the_wrong_shapes_falls_back_to_safe_values(tmp_path):
    m = load()
    p = tmp_path / "c.json"
    p.write_text(json.dumps({"token": 5, "admins": ["12", "x", 7, None], "allowed_hosts": [" SUB.Example.test ", "", 3], "watch": [["1.1.1.1", 443], ["x"], "y", ["h", "z"], ["9.9.9.9", "53"]],
                             "cut_after": 1, "restore_after": "abc", "cooldown": 10**12, "pace": "fast", "texts": {"cut": "  my cut  ", "nope": "x", "restored": 5, "welcome": "   "},
                             "api_base": ""}), encoding="utf-8")
    c = m.Config(str(p)).get()
    assert c["token"] == "5" and c["admins"] == [12, 7] and c["allowed_hosts"] == ["sub.example.test", "3"]
    assert c["watch"] == [["1.1.1.1", 443], ["9.9.9.9", 53]]
    assert c["cut_after"] == 30 and c["restore_after"] == m.DEFAULTS["restore_after"] and c["cooldown"] == 604800 and c["pace"] == m.DEFAULTS["pace"]
    assert c["texts"]["cut"] == "my cut" and c["texts"]["restored"] == m.DEFAULT_TEXTS["restored"] and c["texts"]["welcome"] == m.DEFAULT_TEXTS["welcome"] and "nope" not in c["texts"]
    assert c["api_base"] == m.DEFAULTS["api_base"]
    assert m.Config(str(tmp_path / "missing.json")).get()["texts"] == m.DEFAULT_TEXTS          # no file: the defaults, the program still runs

    p.write_text("{broken", encoding="utf-8")
    assert m.Config(str(p)).get()["watch"] == m.DEFAULTS["watch"]


def test_the_addresses_of_the_bale_host_are_kept_for_the_day_the_name_does_not_resolve(tmp_path, monkeypatch):
    m = load()
    k = m.DnsKeep(str(tmp_path / "dns.json"))
    monkeypatch.setattr(m, "_resolve", lambda host, wait=4.0: ["198.51.100.1", "198.51.100.2"])
    assert k.addresses("tapi.bale.example") == ["198.51.100.1", "198.51.100.2"]

    k2 = m.DnsKeep(str(tmp_path / "dns.json"))                                    # a restart in the middle of the cut
    monkeypatch.setattr(m, "_resolve", lambda host, wait=4.0: [])
    assert k2.addresses("tapi.bale.example") == ["198.51.100.1", "198.51.100.2"]
    assert k2.addresses("127.0.0.1") == ["127.0.0.1"]                              # an address needs no name


def test_a_refused_connection_still_means_the_road_is_open(rig, monkeypatch):
    m, r = rig.m, rig.relay
    rig.set_config(watch=[["198.51.100.9", 443]])
    calls = []

    def fake(addr, timeout):
        calls.append(addr)
        raise ConnectionRefusedError()

    monkeypatch.setattr(m.socket, "create_connection", fake)
    assert r.look() is True and r.targets == {"198.51.100.9:443": True}

    def timeout(addr, timeout):
        raise TimeoutError()

    monkeypatch.setattr(m.socket, "create_connection", timeout)
    assert r.look() is False and r.targets == {"198.51.100.9:443": False}


# ------------------------------------------------------------------ the bot, against the fake Bale
def test_a_customer_starts_the_bot_links_a_subscription_and_asks_for_its_state(rig):
    r, f = rig.relay, rig.fake
    link = rig.sub()
    r.handle(update(1, 100, "/start"))
    assert f.sent[-1][0] == 100 and "اطلاع‌رسانی" in f.sent[-1][1]
    assert r.store.counts()["total"] == 1

    r.handle(update(2, 100, f"اشتراک من {link}"))
    assert "وصل شد" in rig.last()[1] and "ali" in rig.last()[1]
    assert r.store.counts()["linked"] == 1

    r.handle(update(3, 100, "/status"))
    assert "ali" in rig.last()[1] and "فعال" in rig.last()[1]

    # in a cut the panel does not answer: the last known state is still shown, never an error
    f.panel.clear()
    r.store.update_info(100, "ali", {"status": "active", "expire": int(time.time()) + 86400 * 3, "data_limit": 0, "used_traffic": 0})
    with r.store.conn() as c:
        c.execute("UPDATE links SET info_at=?", (int(time.time()) - 7200,))

    r.handle(update(4, 100, "وضعیت اشتراک"))           # the keyboard button says the same as /status
    assert "ali" in rig.last()[1] and "ساعت پیش" in rig.last()[1]


def test_a_status_asks_the_panel_again_at_most_once_a_minute_and_stops_asking_a_panel_that_is_gone(rig):
    r, f = rig.relay, rig.fake
    link = rig.sub()
    r.handle(update(1, 100, link))
    f.hits.clear()

    r.handle(update(2, 100, "/status"))
    assert f.hits == []                                  # linked a moment ago: nothing to ask

    with r.store.conn() as c:
        c.execute("UPDATE links SET info_at=?", (int(time.time()) - 600,))

    f.panel["tok-ali-12345"]["used_traffic"] = 30 * GIB
    r.handle(update(3, 100, "/status"))
    assert len(f.hits) == 1 and "۲۰.۰ گیگ مانده" in rig.last()[1]          # fresh numbers

    with r.store.conn() as c:
        c.execute("UPDATE links SET info_at=?", (int(time.time()) - 600,))

    f.panel.clear()                                        # the panel answers 404 now: the old state stays
    r.handle(update(4, 100, "/status"))
    assert "ali" in rig.last()[1]


def test_a_link_from_another_site_is_refused_without_a_single_request(rig):
    r, f = rig.relay, rig.fake
    rig.sub()
    r.handle(update(1, 100, "https://evil.example.test/sub/tok-ali-12345"))
    assert rig.last()[1] == rig.m.DEFAULT_TEXTS["bad_link"] and f.hits == []

    r.handle(update(2, 100, f"http://127.0.0.1:{f.port + 1}/sub/tok-ali-12345"))      # the right host, not the port the owner named
    assert rig.last()[1] == rig.m.DEFAULT_TEXTS["bad_link"] and f.hits == []

    r.handle(update(3, 100, f"http://127.0.0.1:{f.port}/sub/not-a-known-token"))      # the panel does not know it
    assert rig.last()[1] == rig.m.DEFAULT_TEXTS["bad_link"] and r.store.counts()["linked"] == 0


def test_link_attempts_are_limited_so_the_bot_cannot_be_used_to_knock_on_the_panel(rig):
    r, f = rig.relay, rig.fake
    link = rig.sub()

    for i in range(4):
        r.handle(update(i, 100, link))

    f.hits.clear()
    r.handle(update(9, 100, link))
    assert rig.last()[1] == rig.m.DEFAULT_TEXTS["cannot_check"] and f.hits == []
    r.handle(update(10, 101, link))                       # another customer is not held back by the first
    assert "وصل شد" in rig.last()[1]


def test_a_panel_that_cannot_be_reached_is_told_to_the_customer_as_try_later(rig):
    r, f = rig.relay, rig.fake
    link = rig.sub().replace(str(f.port), "1")           # nothing listens there (it is also not the port that was named: allow it)
    rig.set_config(allowed_hosts=["127.0.0.1:1"])
    r.handle(update(1, 100, link))
    assert rig.last()[1] == rig.m.DEFAULT_TEXTS["cannot_check"]


def test_stop_and_start_and_unlink(rig):
    r = rig.relay
    r.handle(update(1, 100, "/start"))
    r.handle(update(2, 101, "/start"))
    r.handle(update(3, 100, "/stop"))
    assert r.store.recipients() == [101]
    r.handle(update(4, 100, "/start"))
    assert r.store.recipients() == [100, 101]

    r.handle(update(5, 100, rig.sub()))
    assert r.store.counts()["linked"] == 1
    r.handle(update(6, 100, "/unlink"))
    assert r.store.counts()["linked"] == 0 and rig.last()[1] == rig.m.DEFAULT_TEXTS["unlinked"]
    r.handle(update(7, 100, "/status"))
    assert rig.last()[1] == rig.m.DEFAULT_TEXTS["no_links"]
    r.handle(update(8, 100, "/id"))
    assert "100" in rig.last()[1]


def test_only_an_admin_can_send_a_notice_or_see_the_numbers(rig):
    r = rig.relay
    r.handle(update(1, 100, "/start"))
    r.handle(update(2, 100, "/send سلام به همه"))                          # a customer: it is only a chat message, answered with the welcome
    assert rig.last()[1] == rig.m.DEFAULT_TEXTS["welcome"] and not list(rig.tmp.joinpath("outbox").glob("*.msg"))
    r.handle(update(3, 100, "/stats"))
    assert rig.last()[1] == rig.m.DEFAULT_TEXTS["welcome"]

    r.handle(update(4, 900, "/send سلام به همه\nخط دوم"))                 # an admin
    queued = list(rig.tmp.joinpath("outbox").glob("*.msg"))
    assert len(queued) == 1 and json.loads(queued[0].read_text(encoding="utf-8"))["text"] == "سلام به همه\nخط دوم"
    r.handle(update(5, 900, "/stats"))
    assert "کاربران: 2" in rig.last()[1]
    r.handle(update(6, 900, "/send"))
    assert "بعد از /send" in rig.last()[1]


def test_a_notice_reaches_everybody_who_started_the_bot_except_those_who_stopped_it_or_left(rig):
    r, f = rig.relay, rig.fake

    for i, cid in enumerate((100, 101, 102, 103), 1):
        r.handle(update(i, cid, "/start"))

    r.handle(update(9, 101, "/stop"))
    f.blocked.add(103)                                     # 103 blocked the bot: Bale answers 403
    f.sent.clear()
    rig.m.queue("اطلاعیه", "manual")
    path = next(rig.tmp.joinpath("outbox").glob("*.msg"))
    r.run_broadcast(str(path))

    assert sorted(c for c, t in f.sent if t == "اطلاعیه") == [100, 102]
    assert r.store.counts()["blocked"] == 1 and r.store.recipients() == [100, 102]           # the one who left is not tried again
    done = r.store.meta("broadcasts")[-1]
    assert (done["sent"], done["blocked"], done["failed"], done["kind"]) == (2, 1, 0, "manual") and done["done_at"]
    assert not path.exists() and not list(rig.tmp.joinpath("outbox").glob("*"))


def test_a_test_notice_goes_only_to_the_named_chats(rig):
    r, f = rig.relay, rig.fake

    for i, cid in enumerate((100, 900), 1):
        r.handle(update(i, cid, "/start"))

    f.sent.clear()
    p = rig.tmp / "outbox" / "t1.msg"
    p.write_text(json.dumps({"id": "t1", "text": "آزمایش", "kind": "test", "to": [900]}), encoding="utf-8")
    r.run_broadcast(str(p))
    assert f.sent == [(900, "آزمایش")]


def test_a_broadcast_that_is_cut_in_the_middle_goes_on_where_it_stopped(rig):
    r, f = rig.relay, rig.fake

    for i, cid in enumerate((100, 101, 102), 1):
        r.handle(update(i, cid, "/start"))

    f.sent.clear()
    p = rig.tmp / "outbox" / "r1.msg"
    p.write_text(json.dumps({"id": "r1", "text": "پیام", "kind": "manual"}), encoding="utf-8")
    (rig.tmp / "outbox" / "r1.pos").write_text(json.dumps({"after": 100, "rec": {"id": "r1", "kind": "manual", "at": 1, "sent": 1, "failed": 0, "blocked": 0}}), encoding="utf-8")
    r.run_broadcast(str(p))
    assert [c for c, t in f.sent] == [101, 102]                       # 100 had it before the restart
    assert r.store.meta("broadcasts")[-1]["sent"] == 3


def test_when_bale_cannot_be_reached_the_notice_waits_and_nobody_is_counted_as_failed(rig, monkeypatch):
    r = rig.relay

    for i, cid in enumerate((100, 101), 1):
        r.handle(update(i, cid, "/start"))

    rig.set_config(api_base="http://127.0.0.1:1")          # nothing answers there
    monkeypatch.setattr(rig.m.STOP, "wait", lambda s=None: None)
    p = rig.tmp / "outbox" / "w1.msg"
    p.write_text(json.dumps({"id": "w1", "text": "پیام", "kind": "cut"}), encoding="utf-8")
    r.run_broadcast(str(p))
    assert p.exists() and r.store.meta("broadcasts", []) == []        # still queued, not "done"
    assert r.api.state["ok"] is False and r.api.state["err"]
    assert TOKEN not in r.api.state["err"]


def test_the_refusal_of_a_bad_token_is_shown_not_swallowed(rig, monkeypatch):
    r = rig.relay
    rig.set_config(token="999:wrongwrongwrongwrongwrong")
    d = r.api.call("getUpdates", {})
    assert d["ok"] is False and d["error_code"] == 401


def test_the_watch_queues_the_cut_notice_and_the_return_notice_by_itself(rig, monkeypatch):
    r, f = rig.relay, rig.fake
    r.handle(update(1, 100, "/start"))
    f.sent.clear()
    rig.set_config(texts={"cut": "قطعی!", "restored": "وصل شد!"}, cut_after=30, restore_after=10, cooldown=0, watch=[["198.51.100.9", 443]])
    clock, outside = {"t": 3_000_000}, {"up": False}
    monkeypatch.setattr(rig.m, "now", lambda: clock["t"])
    monkeypatch.setattr(r, "look", lambda: outside["up"])

    def tick(dt):
        clock["t"] += dt
        return r.watch_once()

    def queued():
        out = []

        for p in sorted(rig.tmp.joinpath("outbox").glob("*.msg")):
            out.append(json.loads(p.read_text(encoding="utf-8")))
            r.run_broadcast(str(p))

        return out

    assert tick(0) == [] and tick(20) == [] and queued() == []
    assert tick(15) == ["cut"]
    q = queued()
    assert [(x["text"], x["kind"]) for x in q] == [("قطعی!", "cut")] and f.sent == [(100, "قطعی!")]
    assert tick(15) == [] and queued() == []                                         # once

    outside["up"] = True
    f.sent.clear()
    assert tick(5) == [] and tick(10) == ["restored"]
    assert [(x["text"], x["kind"]) for x in queued()] == [("وصل شد!", "restored")] and f.sent == [(100, "وصل شد!")]

    r.write_status()
    st = json.loads((rig.tmp / "data" / "status.json").read_text(encoding="utf-8"))
    assert st["watch"]["up"] is True and st["watch"]["notified"] is False and [b["kind"] for b in st["broadcasts"]] == ["cut", "restored"]


def test_the_watch_can_be_told_to_keep_quiet(rig, monkeypatch):
    r = rig.relay
    rig.set_config(auto_notice=False, cut_after=30, watch=[["198.51.100.9", 443]])
    clock = {"t": 3_000_000}
    monkeypatch.setattr(rig.m, "now", lambda: clock["t"])
    monkeypatch.setattr(r, "look", lambda: False)
    r.watch_once()
    clock["t"] += 100
    assert r.watch_once() == ["cut"] and not list(rig.tmp.joinpath("outbox").glob("*.msg"))          # the cut is seen and shown on the panel, but nothing is sent
    assert json.loads((rig.tmp / "data" / "status.json").read_text(encoding="utf-8"))["watch"]["notified"] is True


def test_the_status_file_says_what_the_panel_needs_to_know(rig):
    r = rig.relay
    r.handle(update(1, 100, "/start"))
    r.bot = {"id": 1, "username": "nikatest_bot"}
    r.targets = {"1.1.1.1:443": False}
    r.watch_state = {"down_since": 123, "notified": True, "last_cut_at": 123}
    r.write_status()
    d = json.loads((rig.tmp / "data" / "status.json").read_text(encoding="utf-8"))
    assert d["v"] == 1 and d["bot"]["username"] == "nikatest_bot" and d["users"]["total"] == 1 and d["watch"] == {"up": False, "down_since": 123, "notified": True, "last_cut_at": 123, "targets": {"1.1.1.1:443": False}}
    assert d["queue"] == 0 and d["broadcasts"] == [] and "token" not in json.dumps(d).lower()


def test_one_poll_reads_the_updates_and_remembers_the_place(rig):
    r, f = rig.relay, rig.fake
    f.updates = [update(41, 100, "/start"), {"update_id": 42, "edited_message": {}}, update(43, 101, "/start"),
                 {"update_id": 44, "message": {"chat": {"id": -5, "type": "group"}, "text": "/start"}}]
    assert r.poll_once(0) is True
    assert sorted(c for c, t in f.sent) == [100, 101]              # the group message and the edited one were ignored
    assert r.store.meta("offset") == 45
    assert [c[0] for c in f.calls[:2]] == ["getMe", "getUpdates"] and f.calls[1][1]["offset"] == 0 and r.bot["username"] == "nikatest_bot"
    assert r.poll_once(0) is True and f.calls[-1][1]["offset"] == 45          # the next round asks from there
    d = json.loads((rig.tmp / "data" / "status.json").read_text(encoding="utf-8"))
    assert d["bot"]["username"] == "nikatest_bot"                              # the first look at Bale is written at once, the panel reads it right after the install


def test_a_poll_that_bale_refuses_or_cannot_answer_is_a_false_not_a_crash(rig):
    r = rig.relay
    rig.set_config(token="999:wrongwrongwrongwrongwrong")
    assert r.poll_once(0) is False and r.store.meta("offset", 0) == 0

    rig.set_config(api_base="http://127.0.0.1:1")
    assert r.poll_once(0) is False and r.api.state["ok"] is False


def test_the_program_runs_the_way_the_service_runs_it(tmp_path):
    """`python3 bale_relay.py run` as a real process: it answers a customer, sends a notice that was queued with its own command line, writes its status and stops on SIGTERM."""
    import signal
    import subprocess
    import sys

    fake = Fake()
    env = {**os.environ, "NIKATEL_RELAY_HOME": str(tmp_path), "PYTHONUNBUFFERED": "1"}
    (tmp_path / "config.json").write_text(json.dumps({"token": TOKEN, "api_base": f"http://127.0.0.1:{fake.port}", "admins": [900], "allowed_hosts": [], "watch": [], "pace": 0.02}), encoding="utf-8")
    fake.updates = [update(1, 100, "/start")]
    proc = subprocess.Popen([sys.executable, str(SRC), "run"], env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)

    def wait_for(cond, secs=15):
        end = time.time() + secs

        while time.time() < end:
            if cond():
                return True

            time.sleep(0.1)

        return False

    try:
        assert wait_for(lambda: any(c == 100 for c, t in fake.sent)), "no welcome"
        done = subprocess.run([sys.executable, str(SRC), "send", "خبر", "تازه"], env=env, capture_output=True, text=True, timeout=20, check=False)
        assert done.returncode == 0 and done.stdout.startswith("QUEUED")
        assert wait_for(lambda: (100, "خبر تازه") in fake.sent), "the notice was not sent"
        sf = tmp_path / "data" / "status.json"
        assert wait_for(lambda: sf.exists() and json.loads(sf.read_text(encoding="utf-8")).get("bot", {}).get("username") == "nikatest_bot" and json.loads(sf.read_text(encoding="utf-8"))["users"]["total"] == 1)
        shown = json.loads(subprocess.run([sys.executable, str(SRC), "status"], env=env, capture_output=True, text=True, timeout=20, check=False).stdout)
        assert shown["users"]["total"] == 1 and shown["version"]
        assert oct((tmp_path / "data" / "relay.db").stat().st_mode & 0o777) == "0o600"          # the list of customers is for root only
        proc.send_signal(signal.SIGTERM)
        assert proc.wait(timeout=15) == 0
    finally:
        if proc.poll() is None:
            proc.kill()

        fake.shutdown()
        fake.server_close()


def test_the_command_line_says_how_to_use_it(tmp_path):
    import subprocess
    import sys

    r = subprocess.run([sys.executable, str(SRC), "bogus"], env={**os.environ, "NIKATEL_RELAY_HOME": str(tmp_path)}, capture_output=True, text=True, timeout=20, check=False)
    assert r.returncode == 2 and "usage" in r.stderr


def test_updates_of_a_strange_shape_are_skipped_and_the_rest_are_handled(rig):
    r, f = rig.relay, rig.fake
    f.updates = [{"update_id": "abc", "message": {"chat": {"id": 7, "type": "private"}, "text": "/start"}}, "junk", 5, None, update(5, 100, "/start")]
    assert r.poll_once(0) is True
    assert [c for c, t in f.sent] == [100] and r.store.meta("offset") == 6


def test_a_loop_that_meets_an_unexpected_error_goes_on_instead_of_dying(rig, monkeypatch):
    """A thread that dies leaves the program up and doing nothing: every loop keeps the error in the log and goes on."""
    r = rig.relay
    monkeypatch.setattr(rig.m.STOP, "wait", lambda s=None: None)
    calls = {"poll": 0, "send": 0, "watch": 0}

    def poll(wait=25):
        calls["poll"] += 1

        if calls["poll"] == 1:
            raise RuntimeError("boom")

        rig.m.STOP.set()
        return True

    monkeypatch.setattr(r, "poll_once", poll)
    r.poll_loop()
    assert calls["poll"] == 2

    rig.m.STOP.clear()
    (rig.tmp / "outbox" / "a.msg").write_text("{}", encoding="utf-8")

    def broadcast(path):
        calls["send"] += 1

        if calls["send"] == 1:
            raise OSError("disk full")

        rig.m.STOP.set()

    monkeypatch.setattr(r, "run_broadcast", broadcast)
    r.send_loop()
    assert calls["send"] == 2

    rig.m.STOP.clear()

    def watch():
        calls["watch"] += 1

        if calls["watch"] == 1:
            raise OSError("database is locked")

        rig.m.STOP.set()

    monkeypatch.setattr(r, "watch_once", watch)
    r.watch_loop()
    assert calls["watch"] == 2


def test_a_notice_file_that_is_not_a_notice_is_put_aside_once_and_for_all(rig):
    r, f = rig.relay, rig.fake
    r.handle(update(1, 100, "/start"))
    f.sent.clear()

    for i, body in enumerate(("[]", '"text"', '{"id": 1}', '{"text": "x", "at": "yesterday"}', "{broken")):
        p = rig.tmp / "outbox" / f"bad{i}.msg"
        p.write_text(body, encoding="utf-8")
        r.run_broadcast(str(p))
        assert not p.exists() and p.with_name(p.name + ".bad").exists(), body

    assert f.sent == []


# ------------------------------------------------------------------ the owner abroad, with nothing but Bale: customers write, he answers from the same chat
def mid_of(f, chat, starts):
    """The id of the last message the bot sent to `chat` whose text starts with `starts`."""
    return next(f.mids[i] for i in range(len(f.sent) - 1, -1, -1) if f.sent[i][0] == chat and f.sent[i][1].startswith(starts))


def test_a_customers_message_goes_to_the_admin_and_the_answer_comes_back(rig):
    r, f = rig.relay, rig.fake
    r.handle(update(1, 100, "سلام، سرویسم وصل نمی‌شود", first="Ali"))
    to_admin = [t for c, t in f.sent if c == 900]
    assert len(to_admin) == 1 and to_admin[0].startswith("💬 #100 · Ali") and "سرویسم وصل نمی‌شود" in to_admin[0]
    assert rig.last() == (100, rig.m.DEFAULT_TEXTS["support_ack"])

    r.handle(update(2, 100, "دوباره می‌پرسم"))                          # a second line: forwarded, not acknowledged again
    assert len([1 for c, t in f.sent if c == 900]) == 2 and [t for c, t in f.sent if c == 100].count(rig.m.DEFAULT_TEXTS["support_ack"]) == 1

    # the admin answers by replying to the forwarded message
    r.handle(update(3, 900, "لطفاً برنامه را ببندید و باز کنید", reply_to_message={"message_id": mid_of(f, 900, "💬 #100"), "text": to_admin[0]}))
    assert (100, "💬 پشتیبانی:\nلطفاً برنامه را ببندید و باز کنید") in f.sent and rig.last() == (900, "✓ رسید")


def test_a_reply_is_found_by_the_header_even_when_the_place_in_the_list_is_gone(rig):
    r, f = rig.relay, rig.fake
    r.handle(update(1, 100, "سلام"))
    r.handle(update(2, 900, "جواب", reply_to_message={"message_id": 424242, "text": "💬 #100 · Ali\n\nسلام"}))
    assert (100, "💬 پشتیبانی:\nجواب") in f.sent

    f.sent.clear()
    r.handle(update(3, 900, "نه", reply_to_message={"message_id": 5, "text": "ℹ️ اینترنت بیرونِ سرور: وصل است"}))
    assert f.sent == [(900, "این پیام مربوط به یک مشتری نیست. روی پیامی ریپلای کنید که با 💬 و # شروع می‌شود، یا /r شناسه متن بنویسید.")]


def test_r_answers_a_customer_without_a_reply(rig):
    r, f = rig.relay, rig.fake
    r.handle(update(1, 900, "/r 100 سلام از پشتیبانی"))
    assert (100, "💬 پشتیبانی:\nسلام از پشتیبانی") in f.sent and rig.last() == (900, "✓ رسید")
    r.handle(update(2, 900, "/r #100 با علامت"))
    assert (100, "💬 پشتیبانی:\nبا علامت") in f.sent

    for bad in ("/r", "/r abc متن", "/r 100"):
        r.handle(update(3, 900, bad))
        assert rig.last()[1].startswith("شکل درست: /r")

    f.blocked.add(101)                                       # a customer who left Bale
    r.handle(update(4, 900, "/r 101 سلام"))
    assert "مسدود" in rig.last()[1]


def test_a_photo_a_file_or_a_voice_is_copied_both_ways(rig):
    r, f = rig.relay, rig.fake
    r.handle(update(1, 100, None, photo=[{"file_id": "x"}], message_id=77, caption="رسید پرداخت"))
    head = next(t for c, t in f.sent if c == 900)
    assert head.startswith("💬 #100") and "رسید پرداخت" in head and f.copies == [(900, 100, 77)]

    # an answer to the COPY (it has no text to read the id from: the bot remembers which customer it was)
    copy_mid = f.copy_ids[-1]
    r.handle(update(2, 900, None, voice={"file_id": "v"}, message_id=88, reply_to_message={"message_id": copy_mid}))
    assert f.copies[-1] == (100, 900, 88) and rig.last() == (900, "✓ رسید")


def test_a_customer_can_be_muted_and_unmuted_and_a_flood_is_dropped(rig):
    r, f = rig.relay, rig.fake
    r.handle(update(1, 100, "/start"))
    r.handle(update(2, 900, "/mute 100"))
    f.sent.clear()
    r.handle(update(3, 100, "سلام"))
    assert f.sent == []                                       # nothing to the admin, no answer to the customer
    r.handle(update(4, 900, "/unmute 100"))
    r.handle(update(5, 100, "سلام دوباره"))
    assert any(c == 900 and "سلام دوباره" in t for c, t in f.sent)

    f.sent.clear()
    for i in range(20):
        r.handle(update(10 + i, 102, f"پیام {i}"))

    assert len([1 for c, t in f.sent if c == 900]) == 15      # at most 15 in ten minutes from one customer; the rest are dropped


def test_without_admins_or_with_the_switch_off_the_customer_gets_the_welcome(rig):
    r, f = rig.relay, rig.fake
    rig.set_config(support=False)
    r.handle(update(1, 100, "سلام"))
    assert f.sent == [(100, rig.m.DEFAULT_TEXTS["welcome"])]

    f.sent.clear()
    rig.set_config(admins=[])
    r.handle(update(2, 100, "سلام"))
    assert f.sent == [(100, rig.m.DEFAULT_TEXTS["welcome"])]


def test_when_the_admin_cannot_be_reached_the_customer_is_told_to_try_again(rig):
    r, f = rig.relay, rig.fake
    f.blocked.add(900)
    r.handle(update(1, 100, "سلام"))
    assert rig.last() == (100, rig.m.DEFAULT_TEXTS["support_fail"])


def test_a_customer_cannot_use_the_admin_commands_or_forge_a_reply(rig):
    r, f = rig.relay, rig.fake
    r.handle(update(1, 100, "/r 101 سلام"))
    r.handle(update(2, 100, "/send به همه"))
    r.handle(update(3, 100, "/cut"))
    r.handle(update(4, 100, "/info"))
    r.handle(update(5, 100, "جواب", reply_to_message={"message_id": 1, "text": "💬 #101 · X"}))
    assert not [1 for c, t in f.sent if c == 101] and not list(rig.tmp.joinpath("outbox").glob("*.msg"))
    assert all(t == rig.m.DEFAULT_TEXTS["welcome"] for c, t in f.sent if c == 100 and t != rig.m.DEFAULT_TEXTS["support_ack"] and not t.startswith("💬"))


def test_the_admin_runs_the_channel_from_bale_alone(rig):
    r, f = rig.relay, rig.fake
    r.handle(update(1, 100, "/start"))
    r.handle(update(2, 900, "/info"))
    assert "اینترنت بیرونِ سرور: وصل است" in rig.last()[1] and "بله: جواب می‌دهد" in rig.last()[1] and "مشتری: ۲ نفر" in rig.last()[1]

    r.watch_state = {"down_since": rig.m.now() - 600, "notified": True}
    r.handle(update(3, 900, "/info"))
    assert "قطع است (از ۱۰ دقیقه پیش)" in rig.last()[1] and "به مشتری‌ها خبر داده شد" in rig.last()[1]

    r.handle(update(4, 900, "/cut"))
    r.handle(update(5, 900, "/back"))
    texts = sorted(json.loads(p.read_text(encoding="utf-8"))["text"] for p in rig.tmp.joinpath("outbox").glob("*.msg"))
    assert texts == sorted([rig.m.DEFAULT_TEXTS["cut"], rig.m.DEFAULT_TEXTS["restored"]])

    r.handle(update(6, 900, "/admin"))
    assert rig.last()[1] == rig.m.ADMIN_HELP
    r.handle(update(7, 900, "یک جمله‌ی معمولی"))                       # an admin's own words are not a message to support
    assert rig.last()[1] == rig.m.ADMIN_HELP and not [1 for c, t in f.sent if c == 900 and t.startswith("💬")]
    r.handle(update(8, 900, "/start"))
    assert f.sent[-2][1] == rig.m.DEFAULT_TEXTS["welcome"] and f.sent[-1][1] == rig.m.ADMIN_HELP
    r.handle(update(9, 100, "/admin"))                               # a customer is not shown the admin help
    assert rig.last()[1] == rig.m.DEFAULT_TEXTS["welcome"]


def test_the_support_switch_travels_in_the_config_and_defaults_on(tmp_path):
    m = load()
    p = tmp_path / "c.json"
    p.write_text("{}", encoding="utf-8")
    assert m.Config(str(p)).get()["support"] is True
    p.write_text(json.dumps({"support": False}), encoding="utf-8")
    os.utime(p, (1_800_000_000, 1_800_000_000))
    assert m.Config(str(p)).get()["support"] is False


def test_a_message_the_bot_cannot_understand_does_not_stop_it(rig):
    r, f = rig.relay, rig.fake
    r.handle({"update_id": 1, "message": {"chat": {"id": 100, "type": "private"}, "from": {}, "text": None}})
    r.handle({"update_id": 2, "message": "text"})
    r.handle({"update_id": 3})
    r.handle(update(4, 100, "/start"))
    assert f.sent[-1][0] == 100

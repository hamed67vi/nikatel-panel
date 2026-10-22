"""The full QA round on configs, panels, DNS and the shop bot: what a walk of the pages and a read of the code found."""
import json
import threading
from pathlib import Path

import pytest

from nikatel.services import panels, shopbot
from nikatel.services import shopbot_admin as adm

WEB = Path(__file__).resolve().parents[1] / "nikatel" / "web"


# ------------------------------------------------------------------ the DNS page
def test_the_restore_of_a_dns_change_by_hand_says_what_it_does():
    js = (WEB / "app.js").read_text(encoding="utf-8")
    assert "window.foRestore = async (id, op) =>" in js and "Delete this record again?" in js and "Make this deleted record again" in js
    assert "foRestore('${esc(e.id)}', '${esc(e.op || \"\")}')" in js          # the Changes table hands the kind of change over


def test_the_panel_page_opens_on_a_panel_that_answers_when_none_was_picked():
    js = (WEB / "app.js").read_text(encoding="utf-8")
    assert '|| list.find(p => p.status === "up") || list[0];' in js


def test_a_panel_without_a_latency_is_not_called_null_ms():
    js = (WEB / "app.js").read_text(encoding="utf-8")
    assert "${p.latency_ms} ms</span>" not in js


# ------------------------------------------------------------------ panels: odd values are sentences, not 500s
class Panel:
    def __init__(self):
        self.calls = []

    def __call__(self, c, method, path, **kw):
        self.calls.append((method, path, kw.get("body")))
        return {"username": "u1", "status": "active", "expire": 0}


@pytest.fixture
def panel(monkeypatch):
    f = Panel()
    monkeypatch.setattr(panels, "call", f)
    return f


C = {"id": 1, "type": "marzban"}


@pytest.mark.parametrize("action, value", [("renew", None), ("renew", "abc"), ("renew", float("inf")), ("renew", float("nan")), ("limit", None), ("limit", "x"), ("limit", float("nan")), ("limit", float("inf"))])
def test_an_action_without_a_usable_number_is_a_400_not_a_type_error(panel, action, value):
    with pytest.raises(ValueError):
        panels.user_action(C, "u1", action, value)

    assert [x for x in panel.calls if x[0] != "GET"] == []          # nothing was sent to the panel


def test_a_bulk_action_with_a_bad_number_lists_the_failures_and_goes_on(panel):
    r = panels.users_bulk(C, ["a1", "b2"], "renew", None)
    assert r["done"] == 0 and r["failed_count"] == 2 and "number" in r["failed"][0]["error"]


def test_the_good_numbers_still_work(panel):
    panels.user_action(C, "u1", "renew", "30")
    panels.user_action(C, "u1", "limit", 5)
    puts = [x for x in panel.calls if x[0] == "PUT"]
    assert len(puts) == 2 and puts[1][2]["data_limit"] == 5 * 1024 ** 3


def test_a_marzban_host_key_without_a_hash_is_host_not_found(monkeypatch):
    monkeypatch.setattr(panels, "call", lambda c, m, p, **kw: {"VLESS": [{"remark": "a", "address": "x"}]})

    for fn in (lambda: panels.host_get(C, "nonsense"), lambda: panels.host_delete(C, "nonsense"), lambda: panels.host_clone(C, "nonsense")):
        with pytest.raises(ValueError, match="host not found"):
            fn()


def test_a_host_made_by_hand_that_ends_with_tls_is_never_removed(monkeypatch):
    """"Remove the TLS copies" took every host named "... TLS" with security tls: the owner's own TLS hosts (made by hand, no plain original) had the same name rule."""
    from nikatel.services import hosttls

    state = {"hosts": {"VLESS WS": [{"remark": "Turkey", "address": "a.example.test", "port": 80, "security": "none"},
                                    {"remark": "Turkey TLS", "address": "a.example.test", "port": 443, "security": "tls"},
                                    {"remark": "Hand made TLS", "address": "b.example.test", "port": 2083, "security": "tls"}]}, "puts": []}

    def call(c, method, path, **kw):
        if method == "PUT":
            state["puts"].append(kw["body"])
            state["hosts"] = json.loads(json.dumps(kw["body"]))
            return state["hosts"]

        return json.loads(json.dumps(state["hosts"]))

    monkeypatch.setattr(panels, "call", call)
    out = hosttls.remove(C)
    assert out["removed"] == 1 and out["kept"] == 1
    assert [h["remark"] for h in state["hosts"]["VLESS WS"]] == ["Turkey", "Hand made TLS"]
    out = hosttls.remove(C)
    assert out["removed"] == 0 and out["kept"] == 1 and len(state["puts"]) == 1          # nothing more to take, nothing written


# ------------------------------------------------------------------ the shop bot
class Db:
    def __init__(self, pay):
        self.pay, self.writes, self.tg = pay, [], []
        self.gate = threading.Event()

    def read(self, sql):
        if "FROM pays WHERE id" in sql:
            return [dict(self.pay)]

        if "COALESCE(wallet, 0) AS w" in sql:
            return [{"w": "1000"}]

        if "FROM chats WHERE id" in sql:
            return [{"id": "3", "user_id": "555", "title": "a <b>&"}]

        return []

    def write(self, sql):
        self.writes.append(sql)

        if sql.startswith("UPDATE users SET wallet"):
            self.gate.wait(2)          # the first approval is still busy here when the second one comes

        return "DONE\n"

    def telegram(self, method, body):
        self.tg.append((method, body))
        return {"ok": True}


@pytest.fixture
def db(monkeypatch):
    f = Db({"id": "7", "hash_id": "h", "user_id": "555", "price": "250000", "state": "have_sent"})
    monkeypatch.setattr(adm, "read", f.read)
    monkeypatch.setattr(adm, "write", f.write)
    monkeypatch.setattr(adm, "_tg", f.telegram)
    monkeypatch.setattr(adm, "_event", lambda *a, **k: None)
    return f


def test_two_clicks_on_approve_credit_the_wallet_once(db):
    first = {}
    t = threading.Thread(target=lambda: first.update(adm.pay_approve(7)))
    t.start()

    while not any(w.startswith("UPDATE users SET wallet") for w in db.writes):
        pass

    with pytest.raises(ValueError, match="being handled"):
        adm.pay_approve(7)

    db.gate.set()
    t.join(5)
    assert first["state"] == "approved" and sum(w.startswith("UPDATE users SET wallet") for w in db.writes) == 1
    assert adm._PAY_BUSY == set()          # and the payment can be looked at again


def test_a_failed_approve_frees_the_payment(db):
    db.pay["user_id"] = "not a number"

    with pytest.raises(ValueError):
        adm.pay_approve(7)

    assert adm._PAY_BUSY == set()


def test_what_the_customer_wrote_cannot_break_the_html_of_a_reply(db):
    adm.ticket_reply(3, "1 < 2 & <b>")
    msg = db.tg[-1][1]["text"]
    assert "a &lt;b&gt;&amp;" in msg and "1 &lt; 2 &amp; &lt;b&gt;" in msg and "<b>" not in msg
    adm.pay_decline(7, "no <receipt>")
    assert "no &lt;receipt&gt;" in db.tg[-1][1]["text"] or "no &lt;receipt&gt;" in db.tg[-2][1]["text"]


def test_the_inbounds_of_a_plan_are_never_cut_in_the_middle():
    big = json.dumps({"inbounds": {"vless": [f"VLESS WS inbound number {i}" for i in range(120)]}})
    assert 2000 < len(big) < 20000
    assert adm._clean({"custom_sni": big}, adm.PLAN_FIELDS)["custom_sni"] == big
    assert adm._clean({"custom_sni": ""}, adm.PLAN_FIELDS)["custom_sni"] == ""

    assert adm._clean({"custom_sni": "sni.example.com"}, adm.PLAN_FIELDS)["custom_sni"] == "sni.example.com"          # an older plan keeps a plain host name: untouched

    for bad in ('{"inbounds": ' + "x" * 20001, '{"inbounds": ', "[1, 2]"):
        with pytest.raises(ValueError, match="custom_sni|inbounds"):
            adm._clean({"custom_sni": bad}, adm.PLAN_FIELDS)


@pytest.mark.parametrize("value", ["inf", "nan", "1e400", "abc"])
def test_a_wallet_or_a_price_with_no_number_is_a_sentence(monkeypatch, value):
    monkeypatch.setattr(shopbot, "query", lambda sql: [{"x": "1", "userid": "5"}])
    monkeypatch.setattr(shopbot, "write", lambda sql: "DONE")

    for action in ("wallet", "wallet_set", "discount"):
        with pytest.raises(ValueError):
            shopbot.user_action(action, "5", value)

    with pytest.raises(ValueError):
        adm.topup_save("volume", 1000, value)          # (float() of the text: nan / inf / abc)

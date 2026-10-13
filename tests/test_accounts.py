"""One account over many panels: grouping by name and by hand-made link, a down panel, one action on every panel, the merged subscription link, the API."""
import base64

import pytest

from nikatel.db import session
from nikatel.models import Panel
from nikatel.services import accounts, panels


def _user(name, used=0, expire="", status="active"):
    return {"username": name, "status": status, "used": used, "limit": None, "expire": None, "expire_date": expire, "online_at": None, "created_at": None, "sub": "", "note": ""}


def _row(pid, panel, name, **kw):
    return {"panel_id": pid, "panel": panel, "type": "marzban", **_user(name, **kw)}


def test_group_joins_the_same_name_and_the_hand_made_links():
    rows = [_row(1, "alpha", "Ali", used=100, expire="2027-03-01"), _row(2, "beta", "ali", used=50, expire="2027-01-15"),
            _row(1, "alpha", "sara", used=7), _row(2, "beta", "sara_pg", used=3, expire="2027-02-02"), _row(2, "beta", "solo", used=1)]
    links = [{"id": "l1", "name": "Sara", "members": [{"panel_id": 1, "username": "sara"}, {"panel_id": 2, "username": "sara_pg"}]}]
    out = accounts.group(rows, links)
    assert [a["key"] for a in out] == ["link:l1", "name:ali", "name:solo"] or [a["key"] for a in out][:2] == ["name:ali", "link:l1"]      # the two-panel accounts first
    by = {a["key"]: a for a in out}
    assert by["name:ali"]["panels"] == 2 and by["name:ali"]["used"] == 150 and by["name:ali"]["expire_date"] == "2027-01-15"      # the earliest end
    assert by["name:ali"]["unlimited"] is False
    assert by["link:l1"]["name"] == "Sara" and by["link:l1"]["used"] == 10 and [m["panel"] for m in by["link:l1"]["members"]] == ["alpha", "beta"]
    assert by["link:l1"]["unlimited"] is True                                  # one of its users has no expiry
    assert by["name:solo"]["panels"] == 1


def test_member_checks():
    with pytest.raises(ValueError):
        accounts._check_members([{"panel_id": 1, "username": "a"}])                                         # one member is not a link
    with pytest.raises(ValueError):
        accounts._check_members([{"panel_id": 1, "username": "a"}, {"panel_id": 1, "username": "b"}])       # two on one panel
    assert accounts._check_members([{"panel_id": 1, "username": " a "}, {"panel_id": 2, "username": "b"}])[0]["username"] == "a"


@pytest.fixture
def two_panels(monkeypatch):
    with session() as db:
        a = Panel(name="alpha", type="marzban", url="https://panel-a.example.test", username="admin", secrets={"password": "x"}, status="up")
        b = Panel(name="beta", type="pasarguard", url="https://panel-b.example.test", username="admin", secrets={"password": "x"}, status="up")
        db.add_all([a, b])
        db.flush()
        ids = {"a": a.id, "b": b.id}

    data = {ids["a"]: [_user("ali", used=100), _user("sara", used=7), _user("only-a", used=1)],
            ids["b"]: [_user("Ali", used=50, expire="2027-01-15"), _user("sara_pg", used=3), _user("zed")]}

    def users(c, search="", offset=0, limit=40, status="", sort=""):
        lst = [u for u in data[c["id"]] if search.lower() in u["username"].lower()][:limit]
        return {"users": lst, "total": len(lst)}

    monkeypatch.setattr(panels, "users", users)
    return ids


def test_search_joins_by_name_and_finds_the_linked_user_with_another_name(two_panels):
    ids = two_panels
    out = accounts.search("ali")
    assert [a["name"] for a in out["accounts"]] == ["ali"] and out["accounts"][0]["panels"] == 2 and out["accounts"][0]["used"] == 150
    assert [p["shown"] for p in out["panels"]] == [1, 1] and all(p["ok"] for p in out["panels"])
    ln = accounts.make_link([{"panel_id": ids["a"], "username": "sara"}, {"panel_id": ids["b"], "username": "sara_pg"}], "Sara")
    assert ln["name"] == "Sara"
    out = accounts.search("sara")                                              # both names match, one account
    assert len(out["accounts"]) == 1 and out["accounts"][0]["link"] == ln["id"] and out["accounts"][0]["used"] == 10
    out = accounts.search("sara_pg")                                           # only beta matches, alpha's "sara" is fetched through the link
    assert len(out["accounts"]) == 1 and out["accounts"][0]["panels"] == 2
    out = accounts.search("")                                                  # no search: everyone newest-first, still grouped
    assert {a["name"] for a in out["accounts"]} == {"ali", "Sara", "only-a", "zed"} and out["accounts"][0]["panels"] == 2


def test_a_down_panel_and_a_failing_panel_are_reported_not_fatal(two_panels, monkeypatch):
    ids = two_panels

    with session() as db:
        db.get(Panel, ids["b"]).status = "down"

    out = accounts.search("ali")
    assert [a["panels"] for a in out["accounts"]] == [1] and [p["ok"] for p in out["panels"]] == [True, False]

    with session() as db:
        db.get(Panel, ids["b"]).status = "up"

    real = panels.users

    def flaky(c, **kw):
        if c["id"] == ids["b"]:
            raise panels.PanelError("HTTP 500")

        return real(c, **kw)

    monkeypatch.setattr(panels, "users", lambda c, search="", offset=0, limit=40, status="", sort="": flaky(c, search=search, offset=offset, limit=limit, status=status, sort=sort))
    out = accounts.search("ali")
    assert out["panels"][1]["ok"] is False and "HTTP 500" in out["panels"][1]["error"] and len(out["accounts"]) == 1


def test_link_rules(two_panels):
    ids = two_panels
    a, b = {"panel_id": ids["a"], "username": "sara"}, {"panel_id": ids["b"], "username": "sara_pg"}
    ln = accounts.make_link([a, b])
    assert ln["name"] == "sara"                                                 # the first username when no name is given
    with pytest.raises(ValueError, match="already belongs"):
        accounts.make_link([a, {"panel_id": ids["b"], "username": "zed"}])
    with pytest.raises(ValueError, match="does not exist"):
        accounts.make_link([{"panel_id": ids["a"], "username": "nobody"}, {"panel_id": ids["b"], "username": "zed"}])
    accounts.drop_link(ln["id"])
    assert accounts.links() == []
    with pytest.raises(ValueError):
        accounts.drop_link(ln["id"])


def test_the_api(client, two_panels):
    ids = two_panels
    j = client.get("/api/accounts?search=ali").json()
    assert j["ok"] is True and j["accounts"][0]["panels"] == 2 and len(j["panels"]) == 2
    r = client.post("/api/accounts/links", json={"members": [{"panel_id": ids["a"], "username": "sara"}, {"panel_id": ids["b"], "username": "sara_pg"}], "name": "Sara"})
    assert r.status_code == 200, r.text
    lid = r.json()["link"]["id"]
    assert client.get("/api/accounts/links").json()["links"][0]["id"] == lid
    assert client.post("/api/accounts/links", json={"members": [{"panel_id": ids["a"], "username": "ali"}]}).status_code == 422
    assert client.post("/api/accounts/links", json={"members": [{"panel_id": ids["a"], "username": "sara"}, {"panel_id": ids["b"], "username": "zed"}]}).status_code == 400
    assert client.delete(f"/api/accounts/links/{lid}").json()["ok"] is True
    assert client.delete(f"/api/accounts/links/{lid}").status_code == 404

# ------------------------------------------------------------------ #85: the limits add up
def test_group_adds_up_the_limits_and_says_when_one_panel_has_none():
    rows = [{**_row(1, "alpha", "ali", used=10), "limit": 100}, {**_row(2, "beta", "ali", used=5), "limit": 50}, {**_row(3, "gamma", "bo", used=1), "limit": 100}, _row(4, "delta", "bo")]
    by = {a["name"]: a for a in accounts.group(rows, [])}
    assert by["ali"]["limit"] == 150 and by["ali"]["used"] == 15
    assert by["bo"]["limit"] == 0                                               # 0 = at least one panel has no limit


# ------------------------------------------------------------------ #82: one action on every panel
def test_member_list_rules_for_an_action():
    with pytest.raises(ValueError):
        accounts.clean_members([])
    with pytest.raises(ValueError):
        accounts.clean_members([{"panel_id": 1, "username": "a"}, {"panel_id": 1, "username": "b"}])
    assert accounts.clean_members([{"panel_id": 1, "username": " a "}])[0]["username"] == "a"          # one member is enough for an action (not for a link)


def test_an_action_is_tried_on_every_panel_and_each_answer_is_reported(two_panels, monkeypatch):
    ids = two_panels
    done = []

    def fake(c, username, action, value=None):
        if c["id"] == ids["b"]:
            raise panels.PanelError("HTTP 500")

        done.append((c["id"], username, action, value))

    monkeypatch.setattr(panels, "user_action", fake)
    out = accounts.act([{"panel_id": ids["a"], "username": "sara"}, {"panel_id": ids["b"], "username": "sara_pg"}], "renew", 30)
    assert out["done"] == 1 and out["failed"] == 1 and done == [(ids["a"], "sara", "renew", 30)]
    by = {r["panel"]: r for r in out["results"]}
    assert by["alpha"]["ok"] is True and by["beta"]["ok"] is False and "HTTP 500" in by["beta"]["error"]      # the panel that is down does not stop the other
    with pytest.raises(ValueError, match="unknown action"):
        accounts.act([{"panel_id": ids["a"], "username": "sara"}], "explode")


def test_the_action_api_and_the_delete_confirmation(client, two_panels, monkeypatch):
    ids = two_panels
    calls = []
    monkeypatch.setattr(panels, "user_action", lambda c, username, action, value=None: calls.append((c["id"], action)))
    body = {"members": [{"panel_id": ids["a"], "username": "ali"}, {"panel_id": ids["b"], "username": "Ali"}], "action": "disable"}
    j = client.post("/api/accounts/action", json=body).json()
    assert j["ok"] is True and j["done"] == 2 and j["failed"] == 0 and len(calls) == 2
    r = client.post("/api/accounts/action", json={**body, "action": "delete"})
    assert r.status_code == 400 and len(calls) == 2                             # nothing was deleted without confirm
    assert client.post("/api/accounts/action", json={**body, "action": "delete", "confirm": True}).json()["done"] == 2
    assert client.post("/api/accounts/action", json={**body, "action": "nope"}).status_code == 400


# ------------------------------------------------------------------ #83: one subscription link
def test_merge_links_keeps_each_link_once_in_panel_order():
    a = base64.b64encode(b"vless://one#a\nvmess://two#a").decode()
    b = "vless://one#a\ntrojan://three#b"                                       # a panel may answer with the plain lines
    merged = base64.b64decode(accounts.merge_links([a, b, "", "garbage"])).decode().split("\n")
    assert merged == ["vless://one#a", "vmess://two#a", "trojan://three#b"]
    assert accounts.sub_links("not a subscription") == []


def test_userinfo_adds_up_and_takes_the_earliest_end():
    one = "upload=1; download=2; total=100; expire=2000"
    two = "upload=3; download=4; total=200; expire=1000"
    assert accounts.sum_userinfo([one, two]) == "upload=4; download=6; total=300; expire=1000"
    assert accounts.sum_userinfo([one, "upload=0; download=0; total=0; expire=0"]) == "upload=1; download=2; total=0; expire=2000"      # a panel with no limit: the whole is unlimited
    assert accounts.sum_userinfo(["", "junk"]) == "upload=0; download=0; total=0; expire=0"


def test_a_link_token_is_signed_and_a_reset_kills_the_old_ones(two_panels):
    ids = two_panels
    ms = [{"panel_id": ids["a"], "username": "sara"}, {"panel_id": ids["b"], "username": "sara_pg"}]
    tok = accounts.make_token(ms)
    assert accounts.read_token(tok) == ms
    body, sig = tok.split(".")
    assert accounts.read_token(body + "." + "0" * len(sig)) is None             # a wrong signature
    other = base64.urlsafe_b64encode(b'[[1,"someone-else"]]').decode().rstrip("=")
    assert accounts.read_token(other + "." + sig) is None                       # the same signature on other members
    assert accounts.read_token("") is None and accounts.read_token("x.y") is None and accounts.read_token("a" * 3000) is None
    accounts.reset_secret()
    assert accounts.read_token(tok) is None
    with pytest.raises(ValueError):
        accounts.make_token([])


def test_the_merged_subscription_is_public_but_nothing_else_is(client, two_panels, monkeypatch):
    from nikatel.services import gate

    ids = two_panels
    feed = {ids["a"]: (base64.b64encode(b"vless://a1#a\nvless://shared#x").decode(), "upload=1; download=2; total=100; expire=2000"),
            ids["b"]: ("vless://shared#x\ntrojan://b1#b", "upload=3; download=4; total=200; expire=1000")}
    asked = []
    monkeypatch.setattr(accounts, "_fetch_sub", lambda m: asked.append(m["panel_id"]) or feed[m["panel_id"]])
    accounts._SUB_CACHE.clear()
    tok = client.post("/api/accounts/sub-link", json={"members": [{"panel_id": ids["a"], "username": "sara"}, {"panel_id": ids["b"], "username": "sara_pg"}]}).json()["token"]
    gate.set_entry_path("secret-door")                                          # the panel is hidden behind a secret path ...
    client.cookies.clear()                                                      # ... and this client has no cookie and no login, like a phone app
    assert client.get("/api/panels").status_code == 404                         # the panel itself is invisible
    r = client.get("/sub/" + tok)
    assert r.status_code == 200, r.text                                         # ... but the signed link works
    assert base64.b64decode(r.text).decode().split("\n") == ["vless://a1#a", "vless://shared#x", "trojan://b1#b"]
    assert r.headers["subscription-userinfo"] == "upload=4; download=6; total=300; expire=1000" and r.headers["x-panels"] == "2/2"
    client.get("/sub/" + tok)
    assert sorted(asked) == sorted(ids.values())                                # the second ask came from the cache: the panels were asked once
    assert client.get("/sub/" + tok[:-2] + "00").status_code == 404             # a damaged token sees the missing page
    assert client.get("/sub/nonsense").status_code == 404


def test_a_link_with_no_answering_panel_is_a_502_and_not_cached(client, two_panels, monkeypatch):
    ids = two_panels
    accounts._SUB_CACHE.clear()
    monkeypatch.setattr(accounts, "_fetch_sub", lambda m: None)
    tok = accounts.make_token([{"panel_id": ids["a"], "username": "sara"}])
    r = client.get("/sub/" + tok)
    assert r.status_code == 502 and tok not in accounts._SUB_CACHE


def test_fetching_one_users_subscription_from_its_panel(two_panels, monkeypatch):
    ids = two_panels

    class Resp:
        def __init__(self, code, text, hdr):
            self.status_code, self.text, self.headers = code, text, hdr

    seen = []

    def fake_get(url, headers=None, **kw):
        seen.append((url, (headers or {}).get("User-Agent")))
        return Resp(200, "vless://x#a", {"subscription-userinfo": "upload=1; download=1; total=5; expire=0"})

    monkeypatch.setattr(accounts.httpx, "get", fake_get)
    monkeypatch.setattr(accounts, "_find", lambda c, name: {"username": name, "sub": "/sub/abc"})
    got = accounts._fetch_sub({"panel_id": ids["a"], "username": "sara"})
    assert got == ("vless://x#a", "upload=1; download=1; total=5; expire=0")
    assert seen == [("https://panel-a.example.test/sub/abc", accounts.SUB_UA)]           # a relative path is put after the panel address, as a v2ray client
    monkeypatch.setattr(accounts, "_find", lambda c, name: None)
    assert accounts._fetch_sub({"panel_id": ids["a"], "username": "gone"}) is None
    monkeypatch.setattr(accounts, "_find", lambda c, name: {"username": name, "sub": "https://sub.example.test/sub/zzz"})
    monkeypatch.setattr(accounts.httpx, "get", lambda url, **kw: Resp(503, "", {}))
    assert accounts._fetch_sub({"panel_id": ids["a"], "username": "sara"}) is None
    assert accounts._fetch_sub({"panel_id": 9999, "username": "sara"}) is None            # a panel that does not exist

# ------------------------------------------------------------------ #82: a new account on several panels
IB = [{"protocol": "vless", "tag": "VLESS TCP", "port": 443}, {"protocol": "vmess", "tag": "VMESS WS", "port": 80}, {"protocol": "wireguard", "tag": "wg", "port": 51820}]


def test_create_body_for_marzban_and_pasarguard():
    m = panels.create_body("marzban", "ali_1", 10, 30, "x", IB, [], now=1_000_000)
    assert m["proxies"] == {"vless": {}, "vmess": {}} and m["inbounds"] == {"vless": ["VLESS TCP"], "vmess": ["VMESS WS"]}      # all the inbounds of the protocols a client can use (no wireguard)
    assert m["data_limit"] == 10 * 1024 ** 3 and m["expire"] == 1_000_000 + 30 * 86400 and m["status"] == "active"
    m = panels.create_body("marzban", "ali_1", 0, 0, "", IB, [], inbounds={"vless": ["VLESS TCP"]})
    assert m["inbounds"] == {"vless": ["VLESS TCP"]} and m["expire"] == 0 and m["data_limit"] == 0                          # no end, no limit
    p = panels.create_body("pasarguard", "ali_1", 5, 1, "", [], [{"id": 3, "name": "main"}], now=1_000_000)
    assert p["group_ids"] == [3] and p["expire"] == "1970-01-13T13:46:40Z"                  # PasarGuard wants an ISO date
    assert panels.create_body("pasarguard", "ali_1", 0, 0, "", [], [{"id": 3}])["expire"] is None


def test_create_body_refuses_what_it_cannot_decide():
    with pytest.raises(ValueError, match="username"):
        panels.create_body("marzban", "a b", 0, 0, "", IB, [])
    with pytest.raises(ValueError, match="username"):
        panels.create_body("marzban", "ab", 0, 0, "", IB, [])
    with pytest.raises(ValueError, match="several groups"):
        panels.create_body("pasarguard", "ali_1", 0, 0, "", [], [{"id": 1}, {"id": 2}])             # it does not guess which groups a person gets
    assert panels.create_body("pasarguard", "ali_1", 0, 0, "", [], [{"id": 1}, {"id": 2}], group_ids=[2])["group_ids"] == [2]
    with pytest.raises(ValueError, match="no group 9"):
        panels.create_body("pasarguard", "ali_1", 0, 0, "", [], [{"id": 1}], group_ids=[9])
    with pytest.raises(ValueError, match="no inbound"):
        panels.create_body("marzban", "ali_1", 0, 0, "", [], [])
    with pytest.raises(ValueError, match="no trojan"):
        panels.create_body("marzban", "ali_1", 0, 0, "", IB, [], inbounds={"trojan": ["t"]})
    with pytest.raises(ValueError, match="between"):
        panels.create_body("marzban", "ali_1", 200000, 0, "", IB, [])
    with pytest.raises(ValueError, match="days"):
        panels.create_body("marzban", "ali_1", 0, 5000, "", IB, [])


def test_user_create_refuses_a_name_that_is_taken(monkeypatch):
    c = {"id": 1, "type": "marzban"}
    monkeypatch.setattr(panels, "options", lambda c: {"inbounds": IB, "groups": []})
    calls = []

    def call(c, method, path, **kw):
        calls.append((method, path))

        if method == "GET":
            if "free" in path:
                raise panels.PanelError("not found", 404)

            return {"username": "taken_one"}

        return {"username": "free_one", "status": "active", "subscription_url": "/sub/zzz"}

    monkeypatch.setattr(panels, "call", call)
    assert panels.user_create(c, "free_one", 1, 1)["sub"] == "/sub/zzz" and calls[-1] == ("POST", "/api/user")
    calls.clear()
    with pytest.raises(ValueError, match="already exists"):
        panels.user_create(c, "taken_one", 1, 1)
    assert ("POST", "/api/user") not in calls                                                    # nothing was written over a name that exists


def test_create_on_several_panels_reports_each(two_panels, monkeypatch):
    ids = two_panels
    made = []

    def fake(c, username, limit_gb=0, days=0, note="", inbounds=None, group_ids=None):
        if c["id"] == ids["b"]:
            raise ValueError("this username already exists on the panel")

        made.append((c["id"], username, limit_gb, days))
        return {"username": username, "sub": "/sub/x"}

    monkeypatch.setattr(panels, "user_create", fake)
    out = accounts.create("newbie", [{"panel_id": ids["a"]}, {"panel_id": ids["b"]}], 20, 30)
    assert out["done"] == 1 and out["failed"] == 1 and made == [(ids["a"], "newbie", 20, 30)]
    assert {r["panel"]: r["ok"] for r in out["results"]} == {"alpha": True, "beta": False}
    assert accounts.read_token(out["token"]) == [{"panel_id": ids["a"], "username": "newbie"}] and out["path"] == "/sub/" + out["token"]     # the link holds only the panels that did it
    monkeypatch.setattr(panels, "user_create", lambda *a, **k: (_ for _ in ()).throw(panels.PanelError("down")))
    out = accounts.create("newbie", [{"panel_id": ids["a"]}])
    assert out["done"] == 0 and out["token"] == "" and out["path"] == ""
    with pytest.raises(ValueError):
        accounts.clean_targets([])
    with pytest.raises(ValueError, match="twice"):
        accounts.clean_targets([{"panel_id": 1}, {"panel_id": 1}])


def test_the_create_api(client, two_panels, monkeypatch):
    ids = two_panels
    monkeypatch.setattr(panels, "user_create", lambda c, username, limit_gb=0, days=0, note="", inbounds=None, group_ids=None: {"username": username, "sub": ""})
    j = client.post("/api/accounts/create", json={"username": "newbie", "panels": [{"panel_id": ids["a"]}, {"panel_id": ids["b"], "group_ids": [2]}], "limit_gb": 10, "days": 30}).json()
    assert j["ok"] is True and j["done"] == 2 and j["path"].startswith("/sub/")
    assert client.post("/api/accounts/create", json={"username": "ab", "panels": [{"panel_id": ids["a"]}]}).status_code == 422          # too short
    assert client.post("/api/accounts/create", json={"username": "newbie", "panels": []}).status_code == 422
    assert client.post("/api/accounts/create", json={"username": "newbie", "panels": [{"panel_id": ids["a"]}, {"panel_id": ids["a"]}]}).status_code == 400

"""One account over many panels: grouping by name and by hand-made link, a down panel, the API."""
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

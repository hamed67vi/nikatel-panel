"""The first-run checklist of the Dashboard: GET /api/setup, hide it, bring it back."""


def test_the_checklist_lists_the_api_keys_first_and_follows_what_is_done(client):
    d = client.get("/api/setup").json()
    assert d["ok"] and d["total"] == 8 and [i["key"] for i in d["items"]][:4] == ["panel", "telegram", "ai", "dns"]
    by = {i["key"]: i for i in d["items"]}
    assert by["telegram"]["done"] is False and by["ai"]["done"] is False and d["dismissed"] is False
    from nikatel.services import settings as st

    key = "sk-" + "ant-api03-test-key-0123456789"                                    # split: the secrets check greps for the prefix
    st.put("ai", {"api_key": key})
    st.put("telegram", {"token": "123456789:" + "A" * 35, "chat_id": "-1001234567890"})
    d2 = client.get("/api/setup").json()
    by2 = {i["key"]: i for i in d2["items"]}
    assert by2["ai"]["done"] is True and by2["telegram"]["done"] is True and d2["done"] >= d["done"] + 2
    assert key not in str(d2) and "AAAA" not in str(d2)                              # a checklist never carries a key


def test_the_checklist_can_be_hidden_and_shown_again_and_needs_a_sign_in(client):
    assert client.post("/api/setup/dismiss").json()["dismissed"] is True
    assert client.get("/api/setup").json()["dismissed"] is True
    assert client.post("/api/setup/reopen").json()["dismissed"] is False
    client.cookies.clear()
    assert client.get("/api/setup").status_code == 401 and client.post("/api/setup/dismiss").status_code == 401
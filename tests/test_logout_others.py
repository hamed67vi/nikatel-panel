"""Sign out of other devices (Settings > Security): the password is asked, every other session ends, this one stays, the password does not change."""
from nikatel import auth


def test_logout_others_keeps_this_session_and_ends_the_others(client):
    old = client.cookies.get(auth.COOKIE)
    assert client.get("/api/servers").status_code == 200
    assert client.post("/api/auth/logout-others", json={"password": "wrong-password"}).status_code == 400
    assert client.get("/api/servers").status_code == 200                     # a wrong password changes nothing
    r = client.post("/api/auth/logout-others", json={"password": "test-password-1"})
    assert r.status_code == 200 and r.json()["ok"] is True
    assert client.get("/api/servers").status_code == 200                     # this browser got a new cookie
    new = client.cookies.get(auth.COOKIE)
    assert new and new != old
    client.cookies.set(auth.COOKIE, old)
    assert client.get("/api/servers").status_code == 401                     # the old session is over
    client.cookies.set(auth.COOKIE, new)
    assert auth.verify("admin", "test-password-1")                           # the password is the same
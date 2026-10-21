"""The rename of a server whose tunnel is in the alert mutes (the owner, 2026-10-21: "I wanted to rename this server, it gave an error": the PUT waited 30 s and failed with "database is locked" - the mute list
was written by `settings.put`, a second connection, while the renaming session held the write lock), and the rule that a window tells its errors in the window."""
from pathlib import Path

from nikatel.db import session
from nikatel.models import Server, Tunnel
from nikatel.services import builder
from nikatel.services import settings as st

WEB = Path(__file__).resolve().parents[1] / "nikatel" / "web"


def _world(tag, n):
    with session() as db:
        ir, fo = Server(name=f"ir-{tag}", host=f"203.0.113.{n}", role="iran", online=True), Server(name=f"fo-{tag}", host=f"198.51.100.{n}", role="foreign", online=True)
        db.add_all([ir, fo])
        db.flush()
        old = builder.pretty_name(fo.name, ir.name, set())
        t = Tunnel(name=old, type="backhaul", transport="tcpmux", iran_server_id=ir.id, foreign_server_id=fo.id, state="up", enabled=True, forwarded_ports=[8075])
        db.add(t)
        db.flush()
        return {"fo": fo.id, "t": t.id, "old": old}


def test_renaming_a_server_whose_tunnel_is_muted_does_not_lock_the_database():
    w = _world("rl", 221)
    st.put("alerts", {"muted": [w["old"], "another"]})

    with session() as db:
        s = db.get(Server, w["fo"])
        s.name = "fo-rl2"
        db.flush()
        lines = builder.follow_server_rename(db, s, "fo-rl")

    assert len(lines) == 1 and lines[0].startswith(w["old"] + " → ")
    new = lines[0].split(" → ", 1)[1]
    muted = st.get("alerts")["muted"]
    assert new in muted and w["old"] not in muted and "another" in muted

    with session() as db:
        assert db.get(Tunnel, w["t"]).name == new


def test_renaming_a_tunnel_that_is_muted_does_not_lock_the_database_either():
    w = _world("rm", 222)
    st.put("alerts", {"muted": [w["old"]]})
    assert builder.rename_tunnel(w["t"], "my-name") == "my-name"
    assert st.get("alerts")["muted"] == ["my-name"]


def test_the_other_fields_of_the_alert_settings_are_kept_by_the_rename():
    w = _world("rk", 223)
    st.put("alerts", {"muted": [w["old"]]})
    before = {k: v for k, v in st.get("alerts").items() if k != "muted"}
    builder.rename_tunnel(w["t"], "kept-ok")
    a = st.get("alerts")
    assert a["muted"] == ["kept-ok"] and {k: v for k, v in a.items() if k != "muted"} == before


def test_an_error_goes_to_the_window_only_never_to_the_status_bar_behind_it():
    js = (WEB / "app.js").read_text(encoding="utf-8")
    toast = js.split("function toast(text, cls, undo) {", 1)[1].split("/* the log box", 1)[0]
    assert 'if (!hm0 || (undo && cls !== "bad")) {' in toast and toast.index("if (hm) {") > toast.index("if (!hm0")
    css = (WEB / "lab.css").read_text(encoding="utf-8")
    rule = css.split("html body:has(#modal .modal) #statusbar .sb-text", 1)[1].split("}", 1)[0]
    assert "visibility: hidden" in rule

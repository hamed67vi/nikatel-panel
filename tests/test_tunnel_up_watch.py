"""A restarted tunnel that answered "down" is followed by the status bar until it is up (the owner, 2026-10-31: "it came up and it did not log it" - the bar kept the "<tunnel>: down - no answer from the foreign end over the GRE link" of the restart).

The restart answers a few seconds after the units were started, often before the GRE link is back; the tunnel is up half a minute later, which the History of the panel records ("works again") but the status bar never said."""
from pathlib import Path

APP = (Path(__file__).resolve().parent.parent / "nikatel" / "web" / "app.js").read_text(encoding="utf-8")


def _fn(start, end):
    return APP[APP.index(start): APP.index(end, APP.index(start))]


def test_a_restart_that_is_not_up_starts_the_watch_and_an_up_restart_does_not():
    restart = _fn("window.restartTunnel = async", "window.restartAll")
    assert 'if (r.state !== "up") tunUpWatch(id, name);' in restart
    assert 'r.state === "up" ? "ok" : "bad"' in restart                                   # (the answer of the restart itself is still shown as it was)


def test_the_watch_looks_every_four_seconds_for_two_minutes_and_says_works_again():
    watch = _fn("const tunUpWatch = (id, name) =>", "window.restartTunnel = async")
    assert "api(`/api/tunnels/${id}`)" in watch and 't.state === "up"' in watch
    assert "toast(`✓ ${name} works again`, \"ok\")" in watch
    assert "setTimeout(look, 4000)" in watch and "120000" in watch
    assert "clearTimeout(tunUpWatch.t)" in watch                                           # (a second restart replaces the first watch: one watch at a time)
    assert "catch (e)" in watch                                                            # (a read that failed - the panel is busy - is looked at again)

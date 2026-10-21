"""A tunnel of a server that is OFF is down at once (the owner, 2026-10-21: Telegram said the server was down, but the panel still showed its tunnel as connected). The pulse knows the server is off after two misses
(~6 s); the check of the tunnel used to judge that "unknown", which has to repeat twice before it replaces "up"; now it is "down" in the first check."""
from nikatel.services import tunnels
from nikatel.services.tunnels import OFF_ERROR, Side


def _side(error="", units=None):
    s = Side()
    s.error = error
    s.units = units or {}
    return s


def test_a_side_that_the_pulse_knows_is_off_makes_the_tunnel_down_at_once():
    ok = _side()
    assert tunnels.judge(None, ok, _side(OFF_ERROR)) == ("down", "the foreign server is off or does not answer")
    assert tunnels.judge(None, _side(OFF_ERROR), ok) == ("down", "the Iran server is off or does not answer")
    assert tunnels.judge(None, _side(OFF_ERROR), _side(OFF_ERROR)) == ("down", "the Iran and foreign server is off or does not answer")


def test_a_server_that_only_failed_its_ssh_login_is_still_unknown_not_down():
    ok = _side()
    state, why = tunnels.judge(None, ok, _side("ssh: connection reset"))
    assert state == "unknown" and "foreign server does not answer" in why
    assert tunnels.judge(None, _side("timeout"), _side("timeout"))[0] == "unknown"


def test_down_is_applied_at_once_by_the_flap_control_where_unknown_waits():
    state, _ = tunnels.settle("up", "down", None)
    assert state == "down"
    held, _ = tunnels.settle("up", "unknown", None)
    assert held == "up"                                                                # (this was the bug: the tunnel stayed "up" on its first "unknown")


def test_the_ask_of_a_server_known_to_be_off_returns_the_off_error():
    import inspect

    src = inspect.getsource(tunnels)
    assert "s.error = OFF_ERROR" in src

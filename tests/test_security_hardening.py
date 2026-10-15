"""The security review of 2026-10-16: an option-looking ssh user or host never reaches ssh / scp, the parameters of a script match whole, what an API token may not reach, what the demo never serves, what the log masks."""
import pytest

from nikatel import log, ssh
from nikatel.services import nodes, tokens
from nikatel.ssh import Node, SSHError


def test_target_refuses_a_user_or_host_that_looks_like_an_option():
    for bad in (Node("203.0.113.5", "-oProxyCommand=x"), Node("-oX", "root"), Node("203.0.113.5", "root ; id"), Node("203.0.113.5", ""), Node("203.0.113.5 -oX", "root")):
        with pytest.raises(SSHError):
            ssh.target(bad)

    assert ssh.target(Node("203.0.113.5", "root")) == "root@203.0.113.5"
    assert ssh.target(Node("2001:db8::1", "deploy_1")) == "deploy_1@2001:db8::1"


def test_the_command_line_ends_its_options_before_the_server():
    cmd = ssh._ssh_base(Node("203.0.113.5", "root"))
    assert cmd[-2:] == ["--", "root@203.0.113.5"]
    assert "--" in ssh._proxy_option(Node("203.0.113.6", "root"))[1]


def test_add_many_refuses_a_user_that_starts_with_a_dash(client):
    with pytest.raises(ValueError):          # the only line is refused, so nothing is added
        nodes.add_many("iran", "n1 203.0.113.7 -oProxyCommand=id", ssh_user="root")
    r = client.post("/api/servers/add-many", json={"role": "iran", "lines": "n2 203.0.113.8", "ssh_user": "-oX"})
    assert r.status_code == 422


def test_a_script_parameter_must_match_whole_not_a_line():
    with pytest.raises(ValueError):
        ssh.render_action("remove_backhaul_unit", {"UNIT": "backhaul-iran-tcp-1.service\n"})

    assert "backhaul-iran-tcp-1.service" in ssh.render_action("remove_backhaul_unit", {"UNIT": "backhaul-iran-tcp-1.service"})


def test_the_sql_of_the_bot_takes_no_single_quote_inside():
    with pytest.raises(ValueError):
        ssh.render_action("bot_sql", {"ENV": "/opt/bot/db.env", "SQL": "'UPDATE t SET a=1'x'"})


def test_an_api_token_never_reaches_the_admin_routes():
    for scope in ("read", "act"):
        for method, path in (("POST", "/api/backup/restore-upload"), ("GET", "/api/backup/download/x.enc"), ("GET", "/api/nodes/user-key"), ("POST", "/api/servers/add-many"),
                             ("POST", "/api/servers/7/console"), ("POST", "/api/servers/7/connect"), ("GET", "/api/servers/7/launcher"), ("GET", "/api/shopbot/settings")):
            assert not tokens.allowed(scope, method, path), (scope, method, path)

    assert tokens.allowed("read", "GET", "/api/servers") and tokens.allowed("act", "POST", "/api/tunnels/3/check") and tokens.allowed("admin", "POST", "/api/servers/7/console")


def test_the_demo_does_not_serve_backups_tokens_or_the_security_page():
    from nikatel import main

    for p in ("/api/backup/download/x", "/api/tokens", "/api/auth/security", "/api/servers/where", "/api/shopbot/settings"):
        assert any(b in p for b in main._DEMO_BLOCK), p


def test_the_log_masks_a_pasarguard_key_and_a_panel_token():
    out = log.mask("key pg_key_0123456789abcdef and token nkt_ABCDEFGHIJKLMNOPQRSTUVWX1234567890abcd here")
    assert "pg_key_" not in out and "nkt_" not in out and "<api-key>" in out and "<token>" in out
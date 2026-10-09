import pytest

from nikatel.log import mask
from nikatel.ssh import Node, SSHError, run_raw, run_script


def test_read_only_refuses_raw_commands():
    with pytest.raises(SSHError):
        run_raw(Node("127.0.0.1"), "echo hi")


def test_scripts_validate_parameters():
    with pytest.raises(ValueError):
        run_script(Node("127.0.0.1"), "tunnel_side", {"UNITS": "x; rm -rf /", "IFACE": "", "PEER": "", "PORTS": ""})

    with pytest.raises(ValueError):
        run_script(Node("127.0.0.1"), "tunnel_side", {"UNITS": "a.service", "IFACE": "", "PEER": "10.0.0.1; id", "PORTS": ""})

    with pytest.raises(ValueError):
        run_script(Node("127.0.0.1"), "no_such_script")

    with pytest.raises(ValueError):
        run_script(Node("127.0.0.1"), "facts", {"EXTRA": "x"})


def test_mask_hides_secrets():
    assert "<bot-token>" in mask("token 123456789:AAHdqTcvCH1vGWJxfSeofSAs0K5PALDsawABCDE sent")
    assert "<api-key>" in mask("key sk-ant-api03-abcdefghijklmnop")
    assert "<hidden>" in mask("password=hunter22") and "hunter22" not in mask("password=hunter22")
    assert mask("plain text 12345") == "plain text 12345"



def test_ssh_shares_one_background_connection_per_server(monkeypatch):
    from nikatel import ssh
    from nikatel.config import get_settings

    s = get_settings()
    node = ssh.Node("203.0.113.9", "root", 22)
    cmd = ssh._ssh_base(node)
    assert "ControlMaster=auto" in cmd and "ControlPersist=120" in cmd and f"ControlPath={s.data_dir / 'cm' / '%C'}" in cmd and cmd[-1] == "root@203.0.113.9"
    monkeypatch.setattr(s, "ssh_multiplex", False)
    assert not any("Control" in a for a in ssh._ssh_base(node))

def test_a_lost_connection_without_a_message_is_an_ssh_error_not_a_crash(monkeypatch):
    import subprocess

    import pytest

    from nikatel import ssh

    monkeypatch.setattr(subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(a, 255, stdout=b""))

    with pytest.raises(ssh.SSHError) as e:
        ssh._exec(ssh.Node("203.0.113.9"), "true", 5)

    assert "ssh connection lost (network)" in str(e.value) and e.value.code == 255
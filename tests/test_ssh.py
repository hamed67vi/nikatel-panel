

def test_ssh_shares_one_background_connection_per_server(monkeypatch, tmp_path):
    import subprocess

    from nikatel import ssh
    from nikatel.config import get_settings

    s = get_settings()
    node = ssh.Node("203.0.113.9", "root", 22)
    use, master, plain = ssh._ssh_base(node), ssh._ssh_base(node, "master"), ssh._ssh_base(node, "")
    cp = f"ControlPath={s.data_dir / 'cm' / '%C'}"
    assert cp in use and "ControlMaster=no" in use and "ControlPersist=120" not in use
    assert cp in master and "ControlMaster=yes" in master and "ControlPersist=120" in master and "-fN" in master
    assert not any("Control" in a for a in plain) and use[-1] == "root@203.0.113.9"
    calls = []

    def fake(cmd, **k):
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 255 if "-O" in cmd else 0, stdout=b"")

    monkeypatch.setattr(subprocess, "run", fake)
    ssh._ensure_master(node)
    assert any("-O" in c and "check" in c for c in calls) and any("-fN" in c for c in calls)      # not alive: started, with the output thrown away
    calls.clear()
    monkeypatch.setattr(subprocess, "run", lambda cmd, **k: calls.append(cmd) or subprocess.CompletedProcess(cmd, 0, stdout=b""))
    ssh._ensure_master(node)
    assert len(calls) == 1 and "-O" in calls[0]                                                    # alive: only asked, nothing started
    monkeypatch.setattr(s, "ssh_multiplex", False)
    calls.clear()
    ssh._ensure_master(node)
    assert calls == [] and not any("Control" in a for a in ssh._ssh_base(node))

def test_a_lost_connection_without_a_message_is_an_ssh_error_not_a_crash(monkeypatch):
    import subprocess

    import pytest

    from nikatel import ssh

    monkeypatch.setattr(ssh, "_ensure_master", lambda node: None)
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(a, 255, stdout=b""))

    with pytest.raises(ssh.SSHError) as e:
        ssh._exec(ssh.Node("203.0.113.9"), "true", 5)

    assert "ssh connection lost (network)" in str(e.value) and e.value.code == 255
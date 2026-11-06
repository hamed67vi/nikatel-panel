"""engines.cache_file: the panel's cache of a core - the x86 file, the ARM file beside it; downloaded and checked when missing, not again when it is there, the versioned copy kept
(found by the 2026-11-06 QA coverage run: no test ever ran it directly, and 1.69.235 put the ARM cores on the same path)."""
import types

import pytest

from nikatel.services import engines


def _setup(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(engines, "get_settings", lambda: types.SimpleNamespace(data_dir=tmp_path / "data"))

    def download(engine, arch="amd64"):
        calls.append((engine, arch))
        return "v1.2.3", (b"x" if arch == "amd64" else b"a") * 1_200_000

    monkeypatch.setattr(engines, "_download", download)
    return calls


def test_the_core_is_downloaded_once_and_kept_with_its_version(monkeypatch, tmp_path):
    calls = _setup(monkeypatch, tmp_path)
    f = engines.cache_file("chisel")
    assert f == tmp_path / "engines" / "chisel" and f.stat().st_size == 1_200_000 and f.stat().st_mode & 0o111
    assert (tmp_path / "engines" / "chisel-v1.2.3").read_bytes() == f.read_bytes()
    assert engines.cache_file("chisel") == f and calls == [("chisel", "amd64")]                # (the second ask is the file that is there)


def test_the_arm_core_lives_beside_the_x86_one_and_is_a_different_file(monkeypatch, tmp_path):
    calls = _setup(monkeypatch, tmp_path)
    x86, arm = engines.cache_file("xray"), engines.cache_file("xray", "arm64")
    assert x86.name == "xray" and arm.name == "xray-arm64" and x86.read_bytes() != arm.read_bytes()
    assert (tmp_path / "engines" / "xray-arm64-v1.2.3").exists() and calls == [("xray", "amd64"), ("xray", "arm64")]


def test_a_file_too_small_to_be_a_core_is_downloaded_again(monkeypatch, tmp_path):
    calls = _setup(monkeypatch, tmp_path)
    (tmp_path / "engines").mkdir(parents=True)
    (tmp_path / "engines" / "paqet").write_bytes(b"cut short")
    assert engines.cache_file("paqet").stat().st_size == 1_200_000 and calls == [("paqet", "amd64")]


def test_an_engine_the_panel_does_not_know_is_refused(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)

    with pytest.raises(ValueError):
        engines.cache_file("nope")


def test_forget_hostkey_without_ssh_keygen_says_so_not_a_500(client, monkeypatch):
    from nikatel.api import servers as api_servers

    def no_keygen(host, port):
        raise FileNotFoundError("ssh-keygen")

    sid = client.post("/api/servers", json={"name": "fk1", "host": "203.0.113.77", "role": "foreign"}).json()["id"]
    monkeypatch.setattr(api_servers, "drop_known_host", no_keygen)
    r = client.post(f"/api/servers/{sid}/forget-hostkey")
    assert r.status_code == 503 and "ssh-keygen" in r.text

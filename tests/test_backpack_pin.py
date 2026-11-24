"""A transport that the newest BackPack answers badly stays on the older release (the owner, 2026-11-24: «wssmux worked before the BackPack 2 update»). Measured on a test tunnel with the same toml and only the binary of
the unit switched: v2.0.0 serves ~740 of 15 000 new connections on tcpmux, wsmux and wssmux, v1.8.5 all but 1-2. Tested: which transports are pinned, the unit that runs the older binary, the cache of the older release, putting
it beside the newest one on a server (once, the right build for the machine, no unit restarted) and a build that does all of it on both servers."""
import types

import pytest

from nikatel.services import build_other as bo
from nikatel.services import engines
from nikatel.ssh import Node


def test_the_mux_transports_of_backpack_are_pinned_and_nothing_else_is():
    for t in ("tcpmux", "wsmux", "wssmux"):
        p = engines.pin_for("backpack", t)
        assert p and p["version"] == "v1.8.5" and p["bin"] == "/usr/local/bin/backpack-v1" and p["bin"] != engines.ENGINES["backpack"]["bin"]

    assert all(engines.pin_for("backpack", t) is None for t in ("tcp", "ws", "wss", "stealth", "pck", "kcp", "udp", "quic", "xdi", ""))
    assert engines.pin_for("backhaul", "tcpmux") is None and engines.pin_for("chisel", "wssmux") is None


def test_the_unit_runs_the_binary_it_is_given_and_the_newest_one_otherwise():
    assert "ExecStart=/usr/local/bin/backpack -c /etc/backpack/a.toml\n" in bo.bp_unit("a", "/etc/backpack/a.toml", "Iran")
    assert "ExecStart=/usr/local/bin/backpack-v1 -c /etc/backpack/a.toml\n" in bo.bp_unit("a", "/etc/backpack/a.toml", "Iran", binary="/usr/local/bin/backpack-v1")
    assert "ExecStartPre=/usr/local/sbin/fw up\nExecStart=/usr/local/bin/backpack-v1 -c" in bo.bp_unit("a", "/etc/backpack/a.toml", "Iran", "fw", "/usr/local/bin/backpack-v1")


def test_the_older_release_comes_from_the_cache_and_is_downloaded_from_that_release_when_it_is_not_there(monkeypatch, tmp_path):
    monkeypatch.setattr(engines, "get_settings", lambda: types.SimpleNamespace(data_dir=tmp_path / "data"))
    asked = []
    monkeypatch.setattr(engines, "_download", lambda engine, arch="amd64", tag="": asked.append((engine, arch, tag)) or (tag or "v2.0.0", b"o" * 1_200_000))
    f = engines.pin_file("backpack", "amd64", "v1.8.5")
    assert f == tmp_path / "engines" / "backpack-v1.8.5" and f.stat().st_size == 1_200_000 and asked == [("backpack", "amd64", "v1.8.5")]
    assert engines.pin_file("backpack", "amd64", "v1.8.5") == f and len(asked) == 1                          # (cached: no second download)
    assert engines.pin_file("backpack", "arm64", "v1.8.5") == tmp_path / "engines" / "backpack-arm64-v1.8.5" and asked[-1] == ("backpack", "arm64", "v1.8.5")
    monkeypatch.setattr(engines, "_download", lambda engine, arch="amd64", tag="": ("v2.0.0", b"o" * 1_200_000))

    with pytest.raises(RuntimeError, match="release v9.9.9 of BackPack was asked for, v2.0.0 came"):
        engines.pin_file("backpack", "amd64", "v9.9.9")


def test_the_download_asks_the_release_page_of_the_tag_not_the_newest_one(monkeypatch):
    urls = []
    monkeypatch.setattr(engines.httpx, "get", lambda url, **kw: urls.append(url) or types.SimpleNamespace(raise_for_status=lambda: None, json=lambda: {"tag_name": "v1.8.5", "html_url": "p", "assets": [
        {"name": "backpack_linux_amd64.tar.gz", "browser_download_url": "https://dl.example/b.tar.gz", "digest": ""}]}))
    assert engines.latest("backpack", "v1.8.5")["version"] == "v1.8.5" and urls[-1].endswith("/repos/AminMGMT/BackPack/releases/tags/v1.8.5")
    engines.latest("backpack")
    assert urls[-1].endswith("/repos/AminMGMT/BackPack/releases/latest")


def _node_env(monkeypatch, tmp_path, answer):
    files, calls = {}, []

    def pin_file(engine, arch, version):
        f = tmp_path / f"{arch}-{version}"
        f.write_bytes(b"z" * 1_200_000)
        files[arch] = f
        return f

    monkeypatch.setattr(engines, "pin_file", pin_file)
    monkeypatch.setattr(engines, "run_script", lambda node, name, params=None, timeout=0: calls.append((name, dict(params or {}))) or answer)
    monkeypatch.setattr(engines, "run_action", lambda node, name, params=None, timeout=0: (calls.append((name, dict(params or {}))), "SWAPPED units=")[1])
    monkeypatch.setattr(engines, "_copy_to_node", lambda node, engine, f, bin_path="": calls.append(("copy", f.name, bin_path)))
    return files, calls


def test_the_older_release_is_put_beside_the_newest_one_without_restarting_any_unit(monkeypatch, tmp_path):
    pin = engines.pin_for("backpack", "wssmux")
    _files, calls = _node_env(monkeypatch, tmp_path, "SHA=none\nARCH=x86_64\n")
    assert engines.ensure_pinned_on_node(Node("203.0.113.9", "root", 22), "backpack", pin) is True
    assert calls[0] == ("engine_sha", {"BIN": "/usr/local/bin/backpack-v1"}) and ("copy", "amd64-v1.8.5", "/usr/local/bin/backpack-v1") in calls
    swap = next(c for c in calls if c[0] == "engine_swap")
    assert swap[1]["BIN"] == "/usr/local/bin/backpack-v1" and swap[1]["UNITS"] == "nikatel-none.service" and swap[1]["WATCH"] == "0"              # (the newest release's file and units are never named)
    assert all(c[1].get("BIN") != "/usr/local/bin/backpack" for c in calls if isinstance(c[1], dict))


def test_a_server_that_has_the_older_release_is_left_alone_an_arm_server_gets_the_arm_build_and_a_strange_machine_is_refused(monkeypatch, tmp_path):
    import hashlib

    pin = engines.pin_for("backpack", "tcpmux")
    files, calls = _node_env(monkeypatch, tmp_path, "SHA=none\nARCH=aarch64\n")
    assert engines.ensure_pinned_on_node(Node("203.0.113.9", "root", 22), "backpack", pin) is True and list(files) == ["arm64"] and ("copy", "arm64-v1.8.5", "/usr/local/bin/backpack-v1") in calls
    sha = hashlib.sha256(b"z" * 1_200_000).hexdigest()
    files, calls = _node_env(monkeypatch, tmp_path, f"SHA={sha}\nARCH=x86_64\n")
    assert engines.ensure_pinned_on_node(Node("203.0.113.9", "root", 22), "backpack", pin) is False and [c[0] for c in calls] == ["engine_sha"]          # (already there: nothing copied)
    files, calls = _node_env(monkeypatch, tmp_path, "SHA=none\nARCH=armv7l\n")

    with pytest.raises(RuntimeError, match="armv7l is not supported"):
        engines.ensure_pinned_on_node(Node("203.0.113.9", "root", 22), "backpack", pin)

    assert files == {} and [c[0] for c in calls] == ["engine_sha"]


def _fake_build(monkeypatch, transport):
    """A BackPack build with every remote call recorded: (calls, the story)."""
    from nikatel.db import session
    from nikatel.models import Server

    with session() as db:
        ir, fr = Server(name="ir1", host="203.0.113.7", role="iran"), Server(name="fr1", host="198.51.100.7", role="foreign")
        db.add_all([ir, fr])
        db.flush()
        db.expunge(ir)
        db.expunge(fr)

    calls, story = [], []
    facts = {"units": [], "tcp": set(), "udp": set(), "ufw": True, "root": True, "python": True, "arch": "x86_64", "ips": []}
    monkeypatch.setattr(bo, "_node", lambda s: s.host)
    monkeypatch.setattr(bo, "_preflight", lambda b, i, f: (dict(facts), dict(facts)))
    monkeypatch.setattr(bo.engines, "ensure_on_node", lambda n, e: False)
    monkeypatch.setattr(bo.engines, "ensure_pinned_on_node", lambda n, e, pin, say=None: calls.append(("pin", n, pin["version"])) or True)
    monkeypatch.setattr(bo, "_install_sink", lambda b, f, k, bind="": b.created.append(f) or calls.append(("sink", f)))
    monkeypatch.setattr(bo, "run_action", lambda n, a, p=None, timeout=0: calls.append((a, n, dict(p or {}))) or "")
    monkeypatch.setattr(bo, "run_script", lambda n, s, p=None, timeout=0: "")
    monkeypatch.setattr(bo, "_put", lambda n, path, text, mode: calls.append(("put", n, path, text)))
    monkeypatch.setattr(bo, "_start", lambda n, label, unit, b: calls.append(("start", n, label, unit)))
    monkeypatch.setattr(bo, "_wait_for_data", lambda b, i, k, hint, log_node, log_unit, why=None: calls.append(("wait", log_node, log_unit, hint)))
    monkeypatch.setattr(bo, "_measure", lambda b, i, k: None)
    monkeypatch.setattr(bo, "check_tunnel", lambda tid: None)
    monkeypatch.setattr(bo, "_say", lambda aid, line, done=None, ok=None: story.append(line))
    b = bo.Build(aid=1, kind="backpack", iran=ir, foreign=fr, ports=bo.parse_ports("8075=8072"), transport=transport, name="")
    bo._build(b)
    return calls, story


@pytest.mark.parametrize("transport", ["wssmux", "tcpmux", "wsmux"])
def test_a_build_of_a_pinned_transport_puts_the_older_release_on_both_servers_and_runs_both_units_on_it(monkeypatch, transport):
    calls, story = _fake_build(monkeypatch, transport)
    assert sorted(c[1:] for c in calls if c[0] == "pin") == [("198.51.100.7", "v1.8.5"), ("203.0.113.7", "v1.8.5")]
    units = {c[1]: c[3] for c in calls if c[0] == "put" and c[2].startswith("/etc/systemd/system/backpack-")}
    assert set(units) == {"198.51.100.7", "203.0.113.7"} and all("ExecStart=/usr/local/bin/backpack-v1 -c" in t for t in units.values())
    assert any("runs on BackPack v1.8.5" in line for line in story)


@pytest.mark.parametrize("transport", ["wss", "tcp", "stealth", "ws"])
def test_a_build_of_any_other_transport_is_what_it_was(monkeypatch, transport):
    calls, story = _fake_build(monkeypatch, transport)
    assert not [c for c in calls if c[0] == "pin"]
    units = [c[3] for c in calls if c[0] == "put" and c[2].startswith("/etc/systemd/system/backpack-")]
    assert len(units) == 2 and all("ExecStart=/usr/local/bin/backpack -c" in t and "backpack-v1" not in t for t in units)
    assert not any("runs on BackPack" in line for line in story)

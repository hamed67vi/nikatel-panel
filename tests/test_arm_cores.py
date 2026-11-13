"""ARM servers (the owner, 2026-11-06: "replace DE2 with this IP ... build the tunnel" - the box is aarch64 and every tunnel type refused it as "x86_64 only").

The cores (BackPack, Backhaul, Chisel, Paqet, Xray) all publish an arm64 build of the same release. A server now gets the build of the machine it is: the panel keeps the ARM build beside the x86 one in its cache, asks the
server what it is (`uname -m`) before it copies, updates both kinds in one run, and only a machine that is neither x86_64 nor aarch64 is still refused."""
import gzip
import io
import re
import subprocess
import tarfile
import types
import zipfile
from pathlib import Path

import pytest

from nikatel.services import build_other as bo
from nikatel.services import builder, engines
from nikatel.services import tunnel_test as tt
from nikatel.ssh import SCRIPTS, Node

# the real names of the files on each release page (2026-11-06)
REAL = {
    "backhaul": ("backhaul_linux_amd64.tar.gz", "backhaul_linux_arm64.tar.gz"),
    "backpack": ("backpack_linux_amd64.tar.gz", "backpack_linux_arm64.tar.gz"),
    "chisel": ("chisel_1.12.0_linux_amd64.gz", "chisel_1.12.0_linux_arm64.gz"),
    "paqet": ("paqet-linux-amd64-v1.0.0-alpha.21.tar.gz", "paqet-linux-arm64-v1.0.0-alpha.21.tar.gz"),
    "xray": ("Xray-linux-64.zip", "Xray-linux-arm64-v8a.zip"),
}


def test_the_machine_of_a_server_picks_its_build():
    assert [engines.arch_key(m) for m in ("x86_64", "amd64", "aarch64", "arm64", " AARCH64\n")] == ["amd64", "amd64", "arm64", "arm64", "arm64"]
    assert [engines.arch_key(m) for m in ("armv7l", "i686", "riscv64", "", None)] == ["", "", "", "", ""]
    assert engines.parse_arch("SHA=none\nARCH=aarch64\n") == "aarch64" and engines.parse_arch("ENGINE xray NONE\nARCH x86_64\n") == "x86_64"
    assert engines.parse_arch("SHA=abc\n") == "" and engines.parse_arch("") == ""


def test_every_core_names_its_arm_build_and_the_names_match_the_real_release_files():
    for name, (amd, arm) in REAL.items():
        e = engines.ENGINES[name]
        assert re.search(e["asset"], amd) and not re.search(e["asset"], arm)
        assert re.search(e["arm64"]["asset"], arm) and not re.search(e["arm64"]["asset"], amd) and e["arm64"]["member"]


def test_the_cache_keeps_the_arm_build_beside_the_x86_one(monkeypatch, tmp_path):
    monkeypatch.setattr(engines, "get_settings", lambda: types.SimpleNamespace(data_dir=tmp_path / "data"))
    cache = tmp_path / "engines"
    assert engines.cache_path("xray") == cache / "xray" and engines.cache_path("xray", "arm64") == cache / "xray-arm64"
    assert engines.cache_path("chisel", "amd64", "v1.12.0") == cache / "chisel-v1.12.0" and engines.cache_path("chisel", "arm64", "v1.12.0") == cache / "chisel-arm64-v1.12.0"


def _release(monkeypatch, files: dict[str, bytes], tag="v1.2.3"):
    """A release page with these files; httpx.get answers the API and each download."""
    assets = [{"name": n, "browser_download_url": "https://dl.example/" + n, "digest": ""} for n in files]

    def get(url, **kw):
        if "api.github.com" in url:
            return types.SimpleNamespace(raise_for_status=lambda: None, json=lambda: {"tag_name": tag, "html_url": "p", "assets": assets})

        return types.SimpleNamespace(content=files[url.rsplit("/", 1)[1]])

    monkeypatch.setattr(engines.httpx, "get", get)


def _tgz(member: str, size=1_100_000) -> bytes:
    buf = io.BytesIO()

    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        info = tarfile.TarInfo(member)
        info.size = size
        tf.addfile(info, io.BytesIO(b"\x7fELF" + b"a" * (size - 4)))

    return buf.getvalue()


def test_the_download_takes_the_arm_file_of_the_release_and_the_member_inside_it(monkeypatch):
    _release(monkeypatch, {"paqet-linux-amd64-v1.0.0-alpha.21.tar.gz": _tgz("paqet_linux_amd64"), "paqet-linux-arm64-v1.0.0-alpha.21.tar.gz": _tgz("paqet_linux_arm64", 1_200_000)})
    lt = engines.latest("paqet")
    assert lt["url"].endswith("amd64-v1.0.0-alpha.21.tar.gz") and lt["arm64"]["url"].endswith("arm64-v1.0.0-alpha.21.tar.gz")
    version, binary = engines._download("paqet")
    assert version == "v1.2.3" and len(binary) == 1_100_000
    version, binary = engines._download("paqet", "arm64")
    assert len(binary) == 1_200_000                                                       # (the member of the ARM archive is paqet_linux_arm64)


def test_the_arm_build_of_each_kind_of_archive_unpacks(monkeypatch):
    big = b"\x7fELF" + b"b" * 1_100_000
    zbuf = io.BytesIO()

    with zipfile.ZipFile(zbuf, "w") as z:
        z.writestr("xray", big)

    _release(monkeypatch, {"Xray-linux-64.zip": zbuf.getvalue(), "Xray-linux-arm64-v8a.zip": zbuf.getvalue()})
    assert engines._download("xray", "arm64")[1] == big
    _release(monkeypatch, {"chisel_1.2.3_linux_amd64.gz": gzip.compress(big), "chisel_1.2.3_linux_arm64.gz": gzip.compress(big)})
    assert engines._download("chisel", "arm64")[1] == big


def test_a_release_without_an_arm_build_says_so_and_still_serves_x86(monkeypatch):
    _release(monkeypatch, {"backhaul_linux_amd64.tar.gz": _tgz("backhaul")})
    assert "arm64" not in engines.latest("backhaul") and len(engines._download("backhaul")[1]) == 1_100_000
    with pytest.raises(RuntimeError, match="no linux arm64 build"):
        engines._download("backhaul", "arm64")


# ------------------------------------------------------------------ the install asks the server what it is
def _node_env(monkeypatch, tmp_path, answer: str):
    cached, calls = [], []
    x86, arm = tmp_path / "x86", tmp_path / "arm"
    x86.write_bytes(b"x" * 1_200_000)
    arm.write_bytes(b"y" * 1_300_000)

    def cache_file(engine, arch="amd64"):
        cached.append((engine, arch))
        return x86 if arch == "amd64" else arm

    monkeypatch.setattr(engines, "cache_file", cache_file)
    monkeypatch.setattr(engines, "run_script", lambda node, name, params=None, timeout=0: answer)
    monkeypatch.setattr(engines, "run_action", lambda node, name, params=None, timeout=0: (calls.append(name), "SWAPPED units=")[1])
    monkeypatch.setattr(engines, "_copy_to_node", lambda node, engine, f: calls.append(("copy", f.name)))
    return cached, calls


def test_an_arm_server_gets_the_arm_build_an_x86_server_the_x86_one(monkeypatch, tmp_path):
    node = Node("203.0.113.9", "root", 22)
    cached, calls = _node_env(monkeypatch, tmp_path, "SHA=none\nARCH=aarch64\n")
    assert engines.ensure_on_node(node, "backpack") is True and cached == [("backpack", "arm64")] and ("copy", "arm") in calls
    cached, calls = _node_env(monkeypatch, tmp_path, "SHA=none\nARCH=x86_64\n")
    assert engines.ensure_on_node(node, "backpack") is True and cached == [("backpack", "amd64")] and ("copy", "x86") in calls
    cached, calls = _node_env(monkeypatch, tmp_path, "SHA=none\n")                                      # (an answer without the machine is x86, as before)
    assert engines.ensure_on_node(node, "backpack") is True and cached == [("backpack", "amd64")]


def test_a_machine_with_no_build_is_refused_before_anything_is_copied(monkeypatch, tmp_path):
    cached, calls = _node_env(monkeypatch, tmp_path, "SHA=none\nARCH=armv7l\n")

    with pytest.raises(RuntimeError, match="armv7l is not supported"):
        engines.ensure_on_node(Node("203.0.113.9", "root", 22), "chisel")

    assert cached == [] and calls == []


def test_the_scripts_say_what_the_machine_is():
    assert 'echo "ARCH=$(uname -m)"' in SCRIPTS["engine_sha"][0] and 'echo "ARCH $(uname -m)"' in SCRIPTS["engine_versions"][0] and 'echo "ARCH=$(uname -m)"' in SCRIPTS["build_facts"][0]
    assert builder.parse_facts("BACKHAUL=none\nARCH=aarch64\n")["arch"] == "aarch64" and builder.parse_facts("BACKHAUL=none\n")["arch"] == ""


# ------------------------------------------------------------------ the update of a mixed fleet
def _fleet(client, monkeypatch, tmp_path, rows, arm_download=True):
    for i, (n, _a) in enumerate(rows):
        assert client.post("/api/servers", json={"name": n, "host": f"203.0.113.{170 + i}", "role": "foreign"}).status_code == 201

    monkeypatch.setattr(engines, "get_settings", lambda: types.SimpleNamespace(data_dir=tmp_path / "data", read_only=False))
    asked = []

    def download(engine, arch="amd64"):
        asked.append(arch)

        if arch != "amd64" and not arm_download:
            raise RuntimeError(f"the release has no linux {arch} build")

        return "v9.9.9", (b"x" if arch == "amd64" else b"a") * 1_200_000

    monkeypatch.setattr(engines, "_download", download)
    engines._STATE["result"] = {"xray": {"nodes": [{"name": n, "state": "outdated", **({"arch": a} if a else {})} for n, a in rows]}}
    engines._STATE["job"] = None
    return asked


def test_one_update_puts_each_build_on_the_machine_it_fits(client, monkeypatch, tmp_path):
    asked = _fleet(client, monkeypatch, tmp_path, [("mx1", "amd64"), ("ma1", "arm64"), ("mx2", "")])
    copied, swapped = [], []
    monkeypatch.setattr(engines, "_copy_to_node", lambda node, engine, f: copied.append((node.host, f.name)))
    monkeypatch.setattr(engines, "run_action", lambda node, name, params=None, timeout=0: (swapped.append((node.host, params["SHA"][:4])), "SWAPPED units=\n")[1])
    job = engines.update("xray")
    assert sorted(asked) == ["amd64", "arm64"] and all(i["state"] == "ok" for i in job["items"]) and len(job["items"]) == 3
    assert sorted(copied) == [("203.0.113.170", "xray"), ("203.0.113.171", "xray-arm64"), ("203.0.113.172", "xray")]
    import hashlib

    assert dict(swapped)["203.0.113.171"] == hashlib.sha256(b"a" * 1_200_000).hexdigest()[:4] and dict(swapped)["203.0.113.170"] == hashlib.sha256(b"x" * 1_200_000).hexdigest()[:4]
    assert engines.cache_path("xray", "arm64").exists() and engines.cache_path("xray", "arm64", "v9.9.9").exists()


def test_no_arm_server_among_the_targets_means_no_arm_download(client, monkeypatch, tmp_path):
    asked = _fleet(client, monkeypatch, tmp_path, [("nx1", "amd64")])
    monkeypatch.setattr(engines, "_copy_to_node", lambda node, engine, f: None)
    monkeypatch.setattr(engines, "run_action", lambda node, name, params=None, timeout=0: "SWAPPED units=\n")
    assert engines.update("xray")["items"][0]["state"] == "ok" and asked == ["amd64"]


def test_an_arm_build_that_cannot_be_had_fails_that_server_only(client, monkeypatch, tmp_path):
    _fleet(client, monkeypatch, tmp_path, [("kx1", "amd64"), ("ka1", "arm64"), ("ke1", "armv7l")], arm_download=False)
    monkeypatch.setattr(engines, "_copy_to_node", lambda node, engine, f: None)
    monkeypatch.setattr(engines, "run_action", lambda node, name, params=None, timeout=0: "SWAPPED units=\n")
    job = engines.update("xray")
    by = {i["name"]: i for i in job["items"]}
    assert by["kx1"]["state"] == "ok" and by["ka1"]["state"] == "failed" and "no linux arm64 build" in by["ka1"]["detail"]
    assert by["ke1"]["state"] == "failed" and "not supported" in by["ke1"]["detail"] and not job.get("halt")


def test_the_check_names_the_machine_of_each_server(client, monkeypatch):
    assert client.post("/api/servers", json={"name": "chk-arm", "host": "203.0.113.190", "role": "foreign"}).status_code == 201
    monkeypatch.setattr(engines, "latest", lambda e: {"version": "v1.0.0", "url": "u", "page": "p", "digest": ""})
    monkeypatch.setattr(engines, "run_script", lambda node, name, params=None, timeout=25: "ENGINE xray v1.0.0\nWGTOOL 0\nARCH aarch64\n")
    res = engines.check()["engines"]["xray"]
    row = next(n for n in res["nodes"] if n["name"] == "chk-arm")
    assert row["arch"] == "arm64" and row["state"] == "current"


# ------------------------------------------------------------------ the builds
def test_only_a_machine_with_no_build_is_still_unfit():
    ok = {"root": True, "python": True}
    assert bo.unfit_reason({**ok, "arch": "x86_64"}) == "" and bo.unfit_reason({**ok, "arch": "aarch64"}) == ""
    assert "armv7l" in bo.unfit_reason({**ok, "arch": "armv7l"}) and "(x86_64 and aarch64 only)" in bo.unfit_reason({**ok, "arch": "armv7l"})
    assert tt.server_unfit(["server fr: architecture armv7l is not supported (x86_64 and aarch64 only)"]) and tt.server_unfit(["has architecture i686 (x86_64 only)"])      # (a story saved before ARM still reads as it did)
    assert not tt.server_unfit(["Preflight: both servers inspected (root, python3, x86_64 or aarch64, ports free)"])


def test_the_preflight_lets_an_arm_server_through_and_stops_an_armv7_one(monkeypatch):
    def facts(arch):
        return f"LISTEN=22\nLISTENU=\nUNITS=\nIPS=203.0.113.5/24\nARCH={arch}\nID=0\nUFW=0\nPY=1\n"

    said = []
    monkeypatch.setattr(bo, "_say", lambda aid, line, done=None, ok=None: said.append(line))

    def build(foreign_arch):
        monkeypatch.setattr(bo, "run_script", lambda node, name, timeout=0: facts("x86_64" if node == "iran-node" else foreign_arch))
        b = bo.Build(aid=1, kind="backpack", iran=types.SimpleNamespace(name="iran-a"), foreign=types.SimpleNamespace(name="fr-a"), ports=[], transport="tcpmux", name="")
        return bo._preflight(b, "iran-node", "fr-node")

    _fi, ff = build("aarch64")
    assert ff["arch"] == "aarch64" and "x86_64 or aarch64" in said[-1]

    with pytest.raises(bo.PreflightError, match=r"fr-a: architecture armv7l is not supported \(x86_64 and aarch64 only\)"):
        build("armv7l")


def test_backhaul_for_an_arm_server_comes_from_the_panels_arm_build_not_from_the_copy_of_another_machine(monkeypatch, tmp_path):
    cache = tmp_path / "engines"
    cache.mkdir()
    (cache / "backhaul").write_bytes(b"x" * 5000)
    arm = tmp_path / "arm-backhaul"
    arm.write_bytes(b"y" * 5000)
    asked, scp = [], []
    monkeypatch.setattr(builder, "get_settings", lambda: types.SimpleNamespace(data_dir=tmp_path / "data", ssh_key="k", known_hosts="h"))
    monkeypatch.setattr(engines, "cache_file", lambda e, arch="amd64": (asked.append((e, arch)), arm if arch == "arm64" else cache / "backhaul")[1])
    monkeypatch.setattr(builder, "_say", lambda aid, line: None)
    monkeypatch.setattr(builder, "run_action", lambda *a, **k: "")
    monkeypatch.setattr(builder, "_put", lambda *a, **k: None)
    monkeypatch.setattr(builder, "_unit", lambda *a, **k: "active")
    monkeypatch.setattr(subprocess, "run", lambda cmd, **k: (scp.append(cmd), types.SimpleNamespace(returncode=0, stderr=""))[1])
    iran, foreign = Node("203.0.113.1", "root", 22), Node("203.0.113.2", "root", 22)
    builder.ensure_backhaul(iran, foreign, {"backhaul": True, "arch": "x86_64"}, {"backhaul": False, "arch": "aarch64"}, 1)
    assert asked == [("backhaul", "arm64")] and len(scp) == 1 and str(arm) in scp[0] and str(cache / "backhaul") not in scp[0]          # (the ARM file went to the ARM server; the x86 copy in the cache stayed where it was)
    asked.clear()
    scp.clear()
    builder.ensure_backhaul(iran, foreign, {"backhaul": True, "arch": "aarch64"}, {"backhaul": False, "arch": "x86_64"}, 1)           # (the ARM side has it, the x86 side lacks it: the x86 build goes, not the ARM one)
    assert asked == [("backhaul", "amd64")] and str(cache / "backhaul") in scp[0]

    with pytest.raises(RuntimeError, match="armv7l is not supported"):
        builder.ensure_backhaul(iran, foreign, {"backhaul": True, "arch": "x86_64"}, {"backhaul": False, "arch": "armv7l"}, 1)


def test_two_x86_servers_still_copy_from_the_one_that_has_it(monkeypatch, tmp_path):
    cache = tmp_path / "engines"
    cache.mkdir()
    asked, scp = [], []
    monkeypatch.setattr(builder, "get_settings", lambda: types.SimpleNamespace(data_dir=tmp_path / "data", ssh_key="k", known_hosts="h"))
    monkeypatch.setattr(engines, "cache_file", lambda *a, **k: asked.append(a) or None)

    def run(cmd, **k):
        if "203.0.113.1:/usr/local/bin/backhaul" in " ".join(map(str, cmd)):
            (cache / "backhaul").write_bytes(b"x" * 5000)

        scp.append(cmd)
        return types.SimpleNamespace(returncode=0, stderr="")

    monkeypatch.setattr(builder, "_say", lambda aid, line: None)
    monkeypatch.setattr(builder, "run_action", lambda *a, **k: "")
    monkeypatch.setattr(builder, "_put", lambda *a, **k: None)
    monkeypatch.setattr(builder, "_unit", lambda *a, **k: "active")
    monkeypatch.setattr(subprocess, "run", run)
    builder.ensure_backhaul(Node("203.0.113.1", "root", 22), Node("203.0.113.2", "root", 22), {"backhaul": True, "arch": "x86_64"}, {"backhaul": False, "arch": "x86_64"}, 1)
    assert asked == [] and len(scp) == 2                                                  # (fetched from the server that has it, put on the one that has not: the panel's cache of cores is not asked)


def test_the_source_says_nothing_is_x86_only_any_more():
    src = (Path(bo.__file__)).read_text(encoding="utf-8")
    assert '("x86_64", "amd64")' not in src and "(x86_64 only)" not in src

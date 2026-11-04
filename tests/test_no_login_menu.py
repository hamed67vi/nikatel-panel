"""A server the panel has no SSH login to (the owner, 2026-11-04): the sampler marks it, the mark survives the pulse's rewrite, the row keeps "No login", and the menu shows what needs SSH disabled."""
import json
import re
import shutil
import subprocess
import time
import types
from pathlib import Path

import pytest

from nikatel.db import session
from nikatel.models import Server
from nikatel.services import facts, nodes
from nikatel.services.pulse import IRAN_ONLY
from nikatel.ssh import SSHError

WEB = Path(__file__).resolve().parents[1] / "nikatel" / "web"
NODE = shutil.which("node")
SAMPLE = "CPU=1000 800 1100 870\nNET=1000000 2000000 1500000 2600000\nMEM=2000000 1200000 1000000 900000\nDISK=40000000 8000000\nLOAD=0.50 0.40 0.30 4\nUP=86400\nCONN=42\nHOST=ir5 6.8.0-31-generic\n"
KEY_REFUSED = "the panel's key is not accepted (authorized_keys)"


def _add(client, name, host):
    return client.post("/api/servers", json={"name": name, "host": host, "role": "iran"}).json()["id"]


def _extra(client, sid):
    return client.get(f"/api/servers/{sid}").json().get("extra") or {}


def test_a_refused_key_marks_the_server_and_the_pulse_rewrite_does_not_lose_the_mark(client, monkeypatch):
    sid = _add(client, "nl1", "203.0.113.20")
    monkeypatch.setattr(facts, "run_script", lambda *a, **k: (_ for _ in ()).throw(SSHError(KEY_REFUSED)))
    facts.sample_server(sid)
    assert _extra(client, sid).get("nologin")

    with session() as db:          # the pulse: "on, but cut from the panel's host" - it rewrites online and last_error
        srv = db.get(Server, sid)
        srv.online, srv.last_error = True, IRAN_ONLY + " (seen from Iran by iran-b)"

    assert _extra(client, sid).get("nologin")


def test_a_time_out_is_not_a_refused_key(client, monkeypatch):
    sid = _add(client, "nl2", "203.0.113.21")
    monkeypatch.setattr(facts, "run_script", lambda *a, **k: (_ for _ in ()).throw(SSHError("timeout after 35s")))
    facts.sample_server(sid)
    assert not _extra(client, sid).get("nologin")


def test_the_first_good_sample_clears_the_mark(client, monkeypatch):
    sid = _add(client, "nl3", "203.0.113.22")
    monkeypatch.setattr(facts, "run_script", lambda *a, **k: (_ for _ in ()).throw(SSHError(KEY_REFUSED)))
    facts.sample_server(sid)
    assert _extra(client, sid).get("nologin")
    monkeypatch.setattr(facts, "run_script", lambda *a, **k: SAMPLE)
    facts.sample_server(sid)
    assert not _extra(client, sid).get("nologin")


def test_connect_with_a_password_clears_the_mark(client, monkeypatch):
    sid = _add(client, "nl4", "203.0.113.23")

    with session() as db:
        db.get(Server, sid).extra = {"nologin": int(time.time())}

    real = nodes.get_settings()
    monkeypatch.setattr(nodes, "get_settings", lambda: types.SimpleNamespace(read_only=False, known_hosts=real.known_hosts, ssh_key=real.ssh_key))
    monkeypatch.setattr(nodes, "_pubkey", lambda: "ssh-ed25519 AAAA test")
    monkeypatch.setattr(nodes, "run_script", lambda *a, **k: "HOST=box-23")
    monkeypatch.setattr(nodes.subprocess, "run", lambda cmd, *a, **k: subprocess.CompletedProcess(cmd, 0, stdout="KEY_INSTALLED\n"))
    assert client.post(f"/api/servers/{sid}/connect", json={"password": "secret-pass"}).status_code == 200
    assert not _extra(client, sid).get("nologin") and _extra(client, sid).get("hostname") == "box-23"


# ---------------------------------------------------------------- the browser side, run with node
needs_node = pytest.mark.skipif(NODE is None, reason="node is not installed")


def _run(code: str):
    out = subprocess.run([NODE, "-e", code], check=False, capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout.strip().splitlines()[-1])


def _no_login_line() -> str:
    return re.search(r"^const noLogin = .*$", (WEB / "app.js").read_text(encoding="utf-8"), re.MULTILINE).group(0)


@needs_node
def test_no_login_is_the_samplers_mark_or_the_old_text_check():
    got = _run(_no_login_line() + """
const on = {enabled: true, online: true, last_error: "the panel's host cannot connect to it, but it is on (seen from Iran by iran-b)", extra: {nologin: 1}};
console.log(JSON.stringify([noLogin(on), noLogin({...on, extra: {}}), noLogin({...on, enabled: false}),
  noLogin({enabled: true, online: false, last_error: "SSH login failed: wrong user or password", extra: {}}), noLogin({enabled: true, online: true, last_error: "", extra: {}})]));
""")
    assert got == [True, False, False, True, False]          # the mark (even while the pulse says it is on), nothing without it, a disabled server never, the old text check still, a healthy server no


def _menu(server: dict, p=True) -> dict:
    lab = (WEB / "lab.js").read_text(encoding="utf-8")
    fn = lab[lab.index("function labServerItems(p, s, ctx) {"):]
    fn = fn[:fn.index("\n}\n") + 2]
    got = _run(f"""
const labItem = (t, fn, o = {{}}) => ({{t, fn, ...o}}), labSep = {{sep: 1}}, labGrp = g => ({{grp: g}}), labSubItem = (t, k, sub, o) => ({{t, sub, ...o}});
const labSvg = k => "", labIt = (t, k, fn, o) => labItem(t, fn, {{...o}});
{_no_login_line()}
const labRemove = () => {{}}, labAdopt = () => {{}}, labOpen = () => {{}}, labCheckOne = () => {{}}, labMoveItems = () => [], labCanMakeNode = () => true, labMakeNode = () => {{}}, labNodeParts = () => null, pgSrvOn = () => [];
globalThis.state = {{}}; const $ = () => null;
{fn}
const out = labServerItems({"{id: 7}" if p else "null"}, {json.dumps(server)}, "{"row" if p else "win"}");
const flat = {{}};
for (const it of out) {{ if (it.sub) {{ flat[it.t] = !!it.off; for (const s of it.sub) flat[it.t + " > " + s.t] = !!s.off; }} else if (it.t) flat[it.t] = !!it.off; }}
console.log(JSON.stringify(flat));
""")
    return got


HEALTHY = {"id": 1, "name": "ir-a", "role": "iran", "enabled": True, "online": True, "last_error": "", "extra": {}, "facts": {}}
NO_LOGIN = {**HEALTHY, "last_error": "the panel's host cannot connect to it, but it is on (seen from Iran by iran-b)", "extra": {"nologin": 1}}
OFFLINE = {**HEALTHY, "online": False, "last_error": "ssh connection lost (network)"}
NEEDS_SSH = ["Console", "Live traffic…", "Reboot…", "Tests", "Install", "Access > Change SSH password…", "Access > Open in an SSH program…", "Make a PasarGuard node…"]
STAYS = ["Overview", "Check now", "Edit…", "Access", "Access > Connect SSH…", "Access > Forget host key…", "Move to profile", "Off the page", "Delete server…"]


@needs_node
def test_a_server_without_a_login_shows_what_needs_ssh_as_disabled_and_keeps_the_rest():
    m = _menu(NO_LOGIN)
    assert all(m[k] is True for k in NEEDS_SSH), {k: m.get(k) for k in NEEDS_SSH}
    assert all(m[k] is False for k in STAYS), {k: m.get(k) for k in STAYS}          # Connect SSH… is the way out: it must stay open


@needs_node
def test_a_healthy_server_has_nothing_disabled_and_an_offline_one_is_as_before():
    assert not any(_menu(HEALTHY).values())
    off = _menu(OFFLINE)
    assert all(off[k] is True for k in ["Console", "Live traffic…", "Reboot…", "Tests", "Install", "Make a PasarGuard node…"])
    assert off["Access > Change SSH password…"] is False and off["Access > Open in an SSH program…"] is False          # (an off server is not a server without a login: those stay as they were)


@needs_node
def test_the_window_menu_of_a_server_without_a_login_is_the_same_list():
    m = _menu(NO_LOGIN, p=False)
    assert all(m[k] is True for k in ["Console", "Live traffic…", "Reboot…", "Tests", "Install", "Access > Change SSH password…"]) and m["Access > Connect SSH…"] is False


def test_the_row_tag_tells_the_reason_and_the_menu_gives_the_way_out():
    lab, app = (WEB / "lab.js").read_text(encoding="utf-8"), (WEB / "app.js").read_text(encoding="utf-8")
    assert "x.nologin ?" in lab and "Access → Connect SSH… puts it on with the SSH password" in lab          # the tag's tip no longer shows the pulse's note
    assert "Access → Connect SSH… first" in lab and "(s.extra || {}).nologin" in app

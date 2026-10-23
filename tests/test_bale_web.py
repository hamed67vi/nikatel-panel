"""The Bale box of Settings → Api & Key (nikatel/web/bale.js): it is loaded, wired into the page, calls only routes that exist and defines every handler its markup names."""
import re
import shutil
import subprocess
from pathlib import Path

import pytest

WEB = Path(__file__).resolve().parent.parent / "nikatel" / "web"
JS = (WEB / "bale.js").read_text(encoding="utf-8")


def test_the_page_loads_the_file_after_app_js_and_stamps_it(client):
    index = (WEB / "index.html").read_text(encoding="utf-8")
    assert index.index("/static/app.js") < index.index("/static/bale.js") < index.index("/static/pg_common.js")
    served = client.get("/").text
    assert re.search(r"/static/bale\.js\?v=\d+", served)


def test_settings_draws_the_box_in_the_api_list_and_loads_it():
    app = (WEB / "app.js").read_text(encoding="utf-8")
    assert "${tgBox}${window.baleBoxHtml ? baleBoxHtml() : \"\"}${probeBox}" in app          # right after the Telegram bot: the two are the same kind of connection
    assert '  if ($("#bale-box") && window.baleLoad) baleLoad();' in app
    assert "window.baleBoxHtml = " in JS and "window.baleLoad = " in JS and 'id="bale-box"' in JS
    assert 'name="support"' in JS and 'support: f.get("support") === "on"' in JS


def test_every_route_the_box_calls_is_a_route_of_the_app(client):
    paths = set(client.get("/api/openapi.json").json()["paths"])
    used = {re.sub(r"\?.*", "", m.group(1)) for m in re.finditer(r"""["'`](/api/[A-Za-z0-9/_?=${}.-]+)""", JS)}
    assert used == {"/api/bale", "/api/bale/status", "/api/bale/send", "/api/bale/remove", "/api/bale/install", "/api/servers"}, used
    assert used <= paths


def test_every_handler_the_markup_names_is_defined():
    named = set(re.findall(r"""on(?:click|submit)=["']return\s+(\w+)\(|on(?:click|submit)=["'](\w+)\(""", JS))
    names = {a or b for a, b in named}
    defined = set(re.findall(r"window\.(\w+)\s*=", JS))
    assert names and names <= defined, names - defined


def test_the_text_that_comes_from_the_server_is_put_in_the_page_through_esc():
    """The server name, the bot name, the kind of a notice, the hosts and the texts come from the server (and the bot name from Bale): none of them is put in markup raw."""
    for raw in ("${s.server}", "${bot}", "${b.kind}", "${bl.err}", "${hosts}", "${(d.texts || {})[k] || \"\"}", "${d.defaults[k]", "${d.hosts", "${d.admins"):
        assert raw not in JS, raw

    for esc_ in ("esc(s.server)", "esc(bot)", "esc(bl.err)", "esc(hosts)", "esc((d.texts || {})[k] || \"\")", "esc(d.defaults[k] || \"\")", "esc(d.admins.join"):
        assert esc_ in JS, esc_


def test_the_file_is_valid_javascript():
    node = shutil.which("node")

    if not node:
        pytest.skip("node is not installed here")

    r = subprocess.run([node, "--check", str(WEB / "bale.js")], capture_output=True, text=True, timeout=60, check=False)
    assert r.returncode == 0, r.stderr

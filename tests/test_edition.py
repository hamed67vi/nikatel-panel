"""The limited edition (the owner, 2026-11-13: "make me a limited version on GitHub, with only these active - Servers, Network: Tunnel manager / Tunnel Doctor / CDN, Settings: Security, Backups, Api & Key,
Appearance, About & update - and in my settings only the sections that belong to them").

`NIKATEL_EDITION` = auto (the default: full on the owner's own installs, lite on every other one) / lite / full. The server tells the page (`data-edition="lite"` on <html>); the page draws only the sections
of the edition. The page code and the docs are read as text, like the other web tests; the question and the attribute are run."""
import re
from pathlib import Path

import pytest

from nikatel.config import get_settings
from nikatel.services import license as lic

ROOT = Path(__file__).resolve().parent.parent
WEB = ROOT / "nikatel" / "web"
APP = (WEB / "app.js").read_text(encoding="utf-8")


def setup(monkeypatch, edition="auto", license=""):
    monkeypatch.setattr(get_settings(), "edition", edition)
    monkeypatch.setattr(get_settings(), "license", license)
    lic._CACHE.update(token=None, at=0.0, res=None)


def tag(client) -> str:
    return re.search(r"<html[^>]*>", client.get("/").text).group(0)


# ------------------------------------------------------------------ the question
@pytest.mark.parametrize(("edition", "license", "lite"), [
    ("auto", "", True), ("auto", "owner", False),                       # the default: the owner's panel is full, every other one is lite
    ("full", "", False), ("full", "owner", False),                      # full by hand
    ("lite", "", True), ("lite", "owner", True),                        # lite by hand, also on the owner's own panel (to see what the others see)
    ("", "", True), ("", "owner", False),                               # an empty setting is auto
    ("  LITE ", "owner", True), ("Full", "", False),                    # case and spaces do not matter
    ("nonsense", "owner", False), ("nonsense", "", True),               # a word nobody knows is auto
])
def test_which_installs_are_lite(monkeypatch, edition, license, lite):
    setup(monkeypatch, edition, license)
    assert lic.lite() is lite


def test_the_tests_run_like_the_owner_s_panel_full_and_the_setting_is_auto_by_default():
    assert get_settings().edition == "auto"
    assert lic.status()["mode"] == "owner" and lic.lite() is False


# ------------------------------------------------------------------ the page attribute
def test_the_server_marks_the_page_of_a_lite_install(client, monkeypatch):
    assert "data-edition" not in tag(client)                                    # the owner's panel: no mark, the page draws everything
    setup(monkeypatch)                                                          # an install that is not the owner's: lite
    assert 'data-edition="lite"' in tag(client) and 'data-sections="' in tag(client)
    setup(monkeypatch, "full")
    assert "data-edition" not in tag(client)
    setup(monkeypatch, "lite", "owner")
    assert 'data-edition="lite"' in tag(client)


# ------------------------------------------------------------------ what the page does with it
def test_the_page_knows_the_edition_and_the_pages_it_has_not():
    assert 'const LITE = document.documentElement.dataset.edition === "lite";' in APP
    assert 'const LITE_OFF = new Set(["dashboard", "pgpanel", "configs", "costs", "bot"]);' in APP
    assert 'const HOME = LITE ? "nodes" : "dashboard";' in APP
    assert '(p => p && !(LITE && LITE_OFF.has(p)) ? p : HOME)(localStorage.getItem("nk.page"))' in APP        # a saved last page of the other sections is not opened
    assert 'page = "dashboard"' not in APP                                      # no page falls back to the Dashboard by name any more: HOME says where


def test_the_pages_of_the_other_sections_open_servers_in_go_and_in_an_address():
    assert 'if (LITE && LITE_OFF.has(page)) { page = HOME; tab = undefined; }' in APP
    assert "page = HOME;" in APP
    assert '(alias === "costs" && !COSTS_UI) || (LITE && LITE_OFF.has(alias))) return false;' in APP           # #/dashboard, #/pgpanel ... are put right


def test_the_menu_of_the_lite_edition():
    assert """document.querySelectorAll('#nav a[data-page="pgpanel"]').forEach(a => { a.hidden = LITE; });""" in APP            # no Panel item
    assert """document.querySelectorAll('#nav a[data-page="dashboard"]').forEach(a => { if (LITE) { a.classList.add("nav-off");""" in APP      # the Dashboard: grey and shut
    assert 'pgpanel: LITE ? [] : of("pgpanel"),' in APP                         # and no group of its tabs under the missing item
    sub = APP.split("const SUB = {", 1)[1].split("\n};", 1)[0]
    assert '...(LITE ? [] : [["field", "📡 Field test", ""]])' in sub and sub.count('"📡 Field test"') == 1
    assert 'const L2TP_UI = SECTIONS.has("l2tp") && !LITE;' in APP              # L2TP is not in the limited edition, token or not
    for kept in ('["list", "⇄ Tunnel manager"', '["repairs", "🩺 Tunnel Doctor"', '["dns", "🌐 CDN"', '["lab", "All"'):
        assert kept in sub


def test_the_five_settings_tabs_stay_and_their_boxes_of_the_other_sections_go():
    tabs = APP.split("const SET_TABS = {", 1)[1].split("\n};", 1)[0]
    for k in ("account", "backup", "ai", "look", "update"):
        assert f"  {k}: [" in tabs
    assert '${probeBox}${instBox}${dnsBox}${LITE ? "" : panelsBox}</div>' in APP                  # the PasarGuard / Marzban panels box
    assert '${!LITE && window.pgUpdBody ? pgUpdBody() : ""}</div>' in APP                          # the update card of the panels and their nodes
    assert '${tgBox}${!LITE && window.baleBoxHtml ? baleBoxHtml() : ""}${probeBox}' in APP         # the Bale box: the notices to the customers of the shop
    for kept in ("${tgBox}", "${aiBox}", "${probeBox}", "${dnsBox}", "${tokBox}"):             # Telegram, the AI, the probe, the DNS accounts and the tokens belong to the tunnels and the Doctor
        assert kept in APP
    assert "the probe and the DNS accounts, each in its own box." in APP and "the tunnel cores on your servers, and what the service runs with." in APP      # the words that named the panels are gone


# ------------------------------------------------------------------ the docs
def test_the_setting_and_the_edition_are_in_the_docs():
    assert "`NIKATEL_EDITION`" in (ROOT / "docs" / "operations.md").read_text(encoding="utf-8")
    lic_doc = (ROOT / "docs" / "licensing.md").read_text(encoding="utf-8")
    assert "## Editions" in lic_doc and "NIKATEL_EDITION" in lic_doc


# ------------------------------------------------------------------ the Settings page of an install that has no shop-bot license
def test_the_settings_page_opens_where_the_shop_bot_is_not_licensed():
    """Found on the walk of the lite panel: /api/settings/shopbot answers 402 where the shop-bot feature is not licensed, and the one read of all the tabs of Settings (Promise.all) failed with it: "This page
    could not be loaded". The read of the Shop bot's settings (a tab that is off) now falls back to an empty one."""
    read = APP.split("async function setData() {", 1)[1].split("\n}", 1)[0]
    assert 'api("/api/settings/shopbot").catch(() => ({host: ""' in read
    assert read.count("api(") - read.count(".catch(") == 10                    # (the other ten reads must answer: only the shop bot and the two lists that are optional fall back)

"""Two sections of the panel that are not there for everyone (the owner, 2026-11-11: "I want the version that is on GitHub to be usable by others - the L2TP parts and Finance disabled for the others",
and: "disable the Finance section for us too, do not show it at all"):

  L2TP     the license feature `l2tp`: the owner's own panel (NIKATEL_LICENSE=owner) has every feature, so nothing changes there; any other install has it only with a token that carries it. No feature: the tab is not
           drawn, every /api/l2tp route answers 402, the L2TP part of the Backups card is not listed, not scheduled and not made by hand.
  Finance  switched off for EVERYONE, the owner too (NIKATEL_FINANCE=true brings it back; its data was not touched): no menu item, no page, /api/costs answers 404, the two jobs are not registered.

The server tells the page which optional sections this install has (`data-sections` on <html> of index.html); the page draws only those."""
import base64
import json
import re
import time
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi import HTTPException
from fastapi.testclient import TestClient

from nikatel.api import l2tp as l2tp_api
from nikatel.config import get_settings
from nikatel.services import backup
from nikatel.services import license as lic

WEB = Path(__file__).resolve().parent.parent / "nikatel" / "web"
NIK = Path(__file__).resolve().parent.parent / "nikatel"
APP = (WEB / "app.js").read_text(encoding="utf-8")


def free(monkeypatch):
    """An install that is not the owner's and has no token: the free tier."""
    monkeypatch.setattr(get_settings(), "license", "")
    lic._CACHE.update(token=None, at=0.0, res=None)


def sections_of(client) -> str:
    return re.search(r'<html lang="en" data-sections="([^"]*)">', client.get("/").text).group(1)


# ------------------------------------------------------------------ L2TP is a license feature
def test_l2tp_is_a_feature_the_owner_has_and_a_free_install_does_not(monkeypatch):
    assert "l2tp" in lic.FEATURES
    assert lic.has("l2tp") and lic.status()["mode"] == "owner"                    # the tests run in owner mode, like the owner's panel
    free(monkeypatch)
    assert not lic.has("l2tp") and lic.status()["mode"] == "free"

    with pytest.raises(ValueError, match="license"):
        lic.require("l2tp")


def test_a_token_that_carries_l2tp_turns_it_on(monkeypatch):
    priv = Ed25519PrivateKey.generate()
    monkeypatch.setattr(lic, "VENDOR_PUBKEY_HEX", priv.public_key().public_bytes_raw().hex())
    free(monkeypatch)

    def token(feat):
        body = json.dumps({"lic": "L-2", "to": "A customer", "iat": int(time.time()), "exp": int(time.time()) + 86400, "feat": feat, "inst": ""}).encode()
        def b64(b):
            return base64.urlsafe_b64encode(b).decode().rstrip("=")

        return "NK1." + b64(body) + "." + b64(priv.sign(body))

    assert lic.parse(token(["tunnels", "l2tp"]))["feat"] == ["l2tp", "tunnels"]
    assert lic.parse(token(["tunnels"]))["feat"] == ["tunnels"]


def test_every_l2tp_route_needs_the_feature_and_asks_the_sign_in_first(client, monkeypatch):
    free(monkeypatch)
    r = client.get("/api/l2tp")
    assert r.status_code == 402 and "license" in r.json()["error"] and r.json()["ok"] is False
    assert client.post("/api/l2tp/accounts", json={}).status_code == 402          # (before the body is looked at: nothing of it runs)
    assert client.delete("/api/l2tp/accounts/someone").status_code == 402
    assert TestClient(client.app).get("/api/l2tp").status_code == 401             # a stranger is asked to sign in, not told about licenses
    routes = [x for x in l2tp_api.router.routes if getattr(x, "path", "").startswith("/l2tp")]
    assert routes and all(any(d.call is l2tp_api._l2tp_licensed for d in x.dependant.dependencies) for x in routes)      # no route of the router is left out


def test_the_owner_s_panel_passes_the_l2tp_guard():
    assert l2tp_api._l2tp_licensed() is None


def test_a_free_install_is_refused_by_the_guard(monkeypatch):
    free(monkeypatch)

    with pytest.raises(HTTPException) as e:
        l2tp_api._l2tp_licensed()

    assert e.value.status_code == 402


# ------------------------------------------------------------------ the L2TP backup
def test_the_l2tp_backup_part_is_there_only_where_l2tp_is_licensed(client, monkeypatch):
    assert set(client.get("/api/settings/backup").json()["parts"]) == {"panel", "bot", "l2tp"}
    free(monkeypatch)
    got = client.get("/api/settings/backup").json()
    assert set(got["parts"]) == {"panel", "bot"} and all(b["part"] != "l2tp" for b in got["backups"])
    assert client.post("/api/settings/backup/part/l2tp", json={"enabled": True}).status_code == 404
    assert client.delete("/api/backup/topic/l2tp").status_code == 404
    assert client.post("/api/settings/backup/part/panel", json={"enabled": True}).status_code == 200          # the other parts are as they were
    assert backup.part_on("panel") and backup.part_on("bot") and not backup.part_on("l2tp") and not backup.part_ready("l2tp")


def test_nothing_makes_an_l2tp_backup_where_it_is_not_licensed(monkeypatch):
    made = []
    monkeypatch.setattr(backup, "has_passphrase", lambda: True)
    monkeypatch.setattr(backup, "make_l2tp_archive", lambda: made.append(1) or b"")
    free(monkeypatch)
    assert backup.run(send=False, part="l2tp") == {"ok": False, "error": "unknown part"}
    backup._scheduled_parts()                                                    # (the hourly job passes the L2TP part by)
    assert made == []


# ------------------------------------------------------------------ Finance is off for everyone
def test_finance_is_switched_off_by_default_and_its_api_answers_not_found(client, monkeypatch):
    assert get_settings().finance is False
    assert client.get("/api/costs").status_code == 404 and client.get("/api/costs/summary").status_code == 404
    assert client.post("/api/costs/rates/fetch").status_code == 404 and client.put("/api/costs/settings", json={}).status_code == 404
    assert TestClient(client.app).get("/api/costs").status_code == 401           # (a stranger is still asked to sign in first)
    monkeypatch.setattr(get_settings(), "finance", True)                         # NIKATEL_FINANCE=true brings it back
    assert client.get("/api/costs").status_code == 200


def test_the_two_finance_jobs_are_registered_only_when_finance_is_on():
    main = (NIK / "main.py").read_text(encoding="utf-8")
    block = main.split("    if get_settings().finance:", 1)[1].split("\n    jobs.register(\"backup\"", 1)[0]
    assert 'jobs.register("cost-rates", 600, costs.run_rates)' in block and 'jobs.register("cost-warm", 120, costs.run_warm)' in block
    assert main.count('jobs.register("cost-rates"') == 1 and main.count('jobs.register("cost-warm"') == 1


# ------------------------------------------------------------------ the page: what the server says, what the page draws
def test_the_server_tells_the_page_which_optional_sections_this_install_has(client, monkeypatch):
    assert sections_of(client) == "l2tp"                                         # the owner's panel: L2TP yes, Finance no
    monkeypatch.setattr(get_settings(), "finance", True)
    assert sections_of(client) == "l2tp,finance"
    free(monkeypatch)
    assert sections_of(client) == "finance"
    monkeypatch.setattr(get_settings(), "finance", False)
    assert sections_of(client) == ""                                             # any other install: neither


def test_the_page_draws_the_l2tp_tab_and_the_finance_item_only_for_the_sections_it_was_given():
    assert 'const SECTIONS = new Set(String(document.documentElement.dataset.sections || "").split(",").filter(Boolean));' in APP
    assert 'const L2TP_UI = SECTIONS.has("l2tp");' in APP and 'const COSTS_UI = SECTIONS.has("finance");' in APP
    sub = APP.split("const SUB = {", 1)[1].split("\n};", 1)[0]
    assert '...(L2TP_UI ? [["l2tp", "🔐 L2TP"' in sub and sub.count('["l2tp", "🔐 L2TP"') == 1       # the tab is in the list only when the section is on
    assert 'page === "costs" && !COSTS_UI' in APP and 'alias === "costs" && !COSTS_UI' in APP              # a saved page or an address of the hidden page opens the Dashboard / is put right
    assert "'#nav a[data-page=\"costs\"]').forEach(a => { a.hidden = !COSTS_UI; })" in APP                    # the menu item is hidden, not greyed
    assert 'document.querySelectorAll("#nav a:not(.nav-off):not([hidden]):not([data-sub])")' in APP        # and the small search of the Dashboard does not offer it
    assert "nav#nav a[hidden] { display: none !important; }" in (WEB / "app.css").read_text(encoding="utf-8")

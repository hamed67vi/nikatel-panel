"""The license: a signed token switches features on; without one the free tier (Backhaul TCP Mux) is what runs; the owner mode has everything."""
import base64
import json
import time

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from nikatel.config import get_settings
from nikatel.services import license as lic
from nikatel.services import settings as st


def _b64(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).decode().rstrip("=")


def make_token(priv: Ed25519PrivateKey, **payload) -> str:
    body = json.dumps({"lic": "L-1", "to": "Test customer", "iat": int(time.time()), "exp": int(time.time()) + 86400, "feat": ["tunnels", "ai"], "inst": "", **payload}).encode()
    return "NK1." + _b64(body) + "." + _b64(priv.sign(body))


@pytest.fixture
def keys(monkeypatch):
    priv = Ed25519PrivateKey.generate()
    pub = priv.public_key().public_bytes_raw().hex()
    monkeypatch.setattr(lic, "VENDOR_PUBKEY_HEX", pub)
    monkeypatch.setattr(get_settings(), "license", "")
    lic._CACHE.update(token=None, at=0.0, res=None)
    return priv, pub


def test_the_free_tier_without_a_token(keys):
    s = lic.status()
    assert s["mode"] == "free" and s["features"] == [] and not s["has_token"]
    assert lic.tunnel_kind_allowed("tcpmux") and not lic.tunnel_kind_allowed("bp:tcpmux")

    with pytest.raises(ValueError, match="license"):
        lic.require("ai")


def test_a_signed_token_switches_features_on(keys):
    priv, _ = keys
    s = lic.set_token(make_token(priv))
    assert s["mode"] == "licensed" and s["features"] == ["ai", "tunnels"] and s["holder"] == "Test customer" and s["error"] == ""
    assert lic.has("ai") and lic.tunnel_kind_allowed("bp:tcpmux") and not lic.has("failover")
    assert st.public("license")["token"] == "" and st.public("license")["has_token"] is True      # the token itself never comes back through the API
    lic.set_token("")
    assert lic.status()["mode"] == "free"


def test_a_tampered_expired_or_foreign_token_is_refused(keys):
    priv, _ = keys
    good = make_token(priv)
    head, body, sig = good.split(".")
    tampered = json.loads(base64.urlsafe_b64decode(body + "=="))
    tampered["feat"] = ["tunnels", "ai", "failover", "shopbot"]
    forged = head + "." + _b64(json.dumps(tampered).encode()) + "." + sig
    assert "signature" in lic.parse(forged, inst="x")["error"]
    assert "expired" in lic.parse(make_token(priv, exp=int(time.time()) - 10))["error"]
    assert "another install" in lic.parse(make_token(priv, inst="other-install"), inst=lic.install_id())["error"]
    assert "not a Nikatel" in lic.parse("hello")["error"]
    other = Ed25519PrivateKey.generate()
    assert "signature" in lic.parse(make_token(other))["error"]

    with pytest.raises(ValueError):
        lic.set_token(make_token(other))


def test_the_owner_mode_has_everything(keys, monkeypatch):
    monkeypatch.setattr(get_settings(), "license", "owner")
    s = lic.status()
    assert s["mode"] == "owner" and set(s["features"]) == set(lic.FEATURES)


def test_the_free_tier_is_enforced_where_it_matters(keys, client, monkeypatch):
    from nikatel.services import ai, build_other, builder, tunnel_test

    monkeypatch.setattr(get_settings(), "read_only", False)

    with pytest.raises(ValueError, match="license"):
        builder.start_build(1, 2, "8072", "wssmux")

    with pytest.raises(ValueError, match="license"):
        build_other.start_build(1, 2, "8072", "backpack", "tcpmux")

    with pytest.raises(ValueError, match="license"):
        tunnel_test.compare_start(1, 2, ["bp:tcpmux", "chisel"])

    with pytest.raises(RuntimeError, match="license"):
        ai.ask("x", "y")

    r = client.get("/api/license")
    assert r.status_code == 200 and r.json()["mode"] == "free" and r.json()["free_kinds"] == ["tcpmux"]
    assert client.get("/api/shopbot").status_code == 402                                # the shop bot pages are licensed
    assert client.put("/api/license", json={"token": "nonsense"}).status_code == 400

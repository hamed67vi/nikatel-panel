"""The license: which paid features this install may use.

The panel is open source, so a check inside it can only keep honest users honest - it can always be patched out. That is why
the design puts the valuable parts BEHIND the license server (docs/licensing.md): the AI runs on the vendor's key through a
proxy that meters credits per license, and updates / support come with a valid license. What this module does is the honest
part: it reads a signed token, tells the rest of the panel which features are on, and refuses the rest with a plain sentence.

Token: `NK1.<base64url(JSON payload)>.<base64url(Ed25519 signature)>`, signed by the vendor's private key; the public key is
embedded here. Payload: {"lic": id, "to": holder, "iat": unix, "exp": unix, "feat": [...], "inst": install id or ""}.
A token bound to an install id ("inst") is valid on that install only (the id is derived from the panel's own secret key and
never leaves the server). Modes: owner (NIKATEL_LICENSE=owner on the vendor's own installs: everything on, no token), licensed
(a valid token), free (no token: the free tier - Backhaul TCP Mux tunnels, no AI, no other tunnel engines)."""
from __future__ import annotations

import base64
import hashlib
import json
import time

from ..config import get_settings
from ..log import get_logger
from . import settings as st

log = get_logger("license")
FEATURES = {"tunnels": "every tunnel engine and transport (BackPack, Paqet, Chisel, the other Backhaul transports)",
            "ai": "the AI assistant (Fix with AI, Explain, the Tunnel Doctor's advice)",
            "failover": "the ArvanCloud DNS failover and IP switch",
            "shopbot": "the shop bot pages and the Mini App"}
FREE_TUNNEL_KINDS = {"tcpmux"}          # the free tier: Backhaul TCP Mux over GRE
VENDOR_PUBKEY_HEX = ""                   # the vendor's Ed25519 public key (32 bytes, hex); empty = no token can be valid, only the owner mode or the free tier
_CACHE: dict = {"token": None, "at": 0.0, "res": None}


def install_id() -> str:
    """A stable id of this install, derived from the panel's own secret key (never the key itself)."""
    return hashlib.sha256(b"nikatel-install:" + get_settings().secret_key()).hexdigest()[:16]


def _b64d(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def parse(token: str, pubkey_hex: str = "", now: float | None = None, inst: str | None = None) -> dict:
    """Pure: the payload of a valid token, or {"error": sentence}. The signature is checked with `cryptography` (Ed25519);
    without a public key no token is valid."""
    t = str(token or "").strip()

    if not t:
        return {"error": "no token"}

    parts = t.split(".")

    if len(parts) != 3 or parts[0] != "NK1":
        return {"error": "this is not a Nikatel license token"}

    try:
        payload, sig = _b64d(parts[1]), _b64d(parts[2])
        data = json.loads(payload)
    except (ValueError, TypeError):
        return {"error": "the token is damaged"}

    key = (pubkey_hex or VENDOR_PUBKEY_HEX).strip()

    if not key:
        return {"error": "this panel has no vendor key: tokens cannot be checked"}

    try:
        from cryptography.exceptions import InvalidSignature
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

        Ed25519PublicKey.from_public_bytes(bytes.fromhex(key)).verify(sig, payload)
    except ImportError:
        return {"error": "the cryptography package is missing on this server"}
    except (InvalidSignature, ValueError):
        return {"error": "the signature is wrong: the token was not issued for this product or was changed"}

    if not isinstance(data, dict) or not isinstance(data.get("feat"), list):
        return {"error": "the token is damaged"}

    ts = now if now is not None else time.time()

    if int(data.get("exp", 0)) and ts > int(data["exp"]):
        return {"error": "the license expired on " + time.strftime("%Y-%m-%d", time.gmtime(int(data["exp"])))}

    if data.get("inst") and inst is not None and data["inst"] != inst:
        return {"error": "the license is bound to another install"}

    return {"lic": str(data.get("lic", "")), "to": str(data.get("to", "")), "exp": int(data.get("exp", 0)), "feat": sorted(str(f) for f in data["feat"] if f in FEATURES), "inst": str(data.get("inst", ""))}


def mode() -> str:
    return "owner" if str(get_settings().license or "").strip().lower() == "owner" else "licensed" if not status().get("error") and st.get("license").get("token") else "free"


def status() -> dict:
    """The parsed token (cached a minute), the mode and the features that are on."""
    tok = str(st.get("license").get("token") or "")

    if _CACHE["token"] != tok or time.time() - _CACHE["at"] > 60:
        _CACHE.update(token=tok, at=time.time(), res=parse(tok, inst=install_id()) if tok else {"error": "no token"})

    res = dict(_CACHE["res"] or {})
    owner = str(get_settings().license or "").strip().lower() == "owner"
    feats = set(FEATURES) if owner else set(res.get("feat") or []) if not res.get("error") else set()
    return {"mode": "owner" if owner else ("licensed" if not res.get("error") else "free"), "features": sorted(feats), "install_id": install_id(),
            "holder": "" if owner else res.get("to", ""), "expires": 0 if owner else res.get("exp", 0), "error": "" if owner or not tok else res.get("error", ""), "has_token": bool(tok)}


def features() -> set[str]:
    return set(status()["features"])


def has(feature: str) -> bool:
    return feature in features()


def require(feature: str) -> None:
    """Raise ValueError with a plain sentence when the feature is not licensed."""
    if not has(feature):
        raise ValueError(f"{FEATURES.get(feature, feature)} needs a license (Settings → About → License)")


def tunnel_kind_allowed(kind: str) -> bool:
    return has("tunnels") or kind in FREE_TUNNEL_KINDS


def set_token(token: str) -> dict:
    """Keep a token after checking it; an empty token removes the license (back to the free tier)."""
    tok = str(token or "").strip()

    if tok:
        res = parse(tok, inst=install_id())

        if res.get("error"):
            raise ValueError(res["error"])

    st.put("license", {"token": tok or None})       # None, not "": an empty string would keep the old secret (settings.put)
    _CACHE["at"] = 0.0
    return status()

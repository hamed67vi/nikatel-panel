"""Cloudflare DNS: the client the DNS page uses for an account of that kind (an API TOKEN with `Zone: Read` and `DNS: Edit`).

API v4 · https://api.cloudflare.com/client/v4 · header `Authorization: Bearer <token>`. A Cloudflare A record holds ONE address (several
addresses = several records); `ttl` 1 means automatic; `proxied` is the orange cloud. Only what the DNS page needs is here; the token lives
in the panel's settings and never comes back through the API."""
from __future__ import annotations

import re

import httpx

from ..log import get_logger

log = get_logger("cloudflare")
BASE = "https://api.cloudflare.com/client/v4"


def req(token: str, method: str, path: str, body: dict | None = None, params: dict | None = None) -> dict:
    t = re.sub(r"[\s\u200b\u200c\u200d\ufeff]+", "", str(token or "").strip().removeprefix("Bearer ")).strip("\"'")      # a pasted token may carry a line break, a space or a quote

    if not t:
        raise RuntimeError("no Cloudflare API token saved")

    if not t.isascii() or not re.fullmatch(r"[A-Za-z0-9_\-]+", t):
        raise RuntimeError("the token has a character that is not a letter, a digit, - or _ (a Persian letter or a symbol from the copy?): copy it again from Cloudflare")

    try:
        r = httpx.request(method, BASE + path, params=params, json=body, timeout=20, headers={"Authorization": "Bearer " + t, "Accept": "application/json"})
    except httpx.HTTPError as e:
        raise RuntimeError("Cloudflare does not answer: " + str(e)[:120])

    try:
        j = r.json()
    except ValueError:
        j = {}

    if r.status_code == 400 and "header" in r.text.lower():      # what Cloudflare says to something that is not an API token (the Global API Key, an email, a half copy)
        raise RuntimeError(f"Cloudflare did not accept this as an API token ({len(t)} characters; a token has 40). Make one: Cloudflare dashboard → My Profile → API Tokens → Create Token → the Edit zone DNS template. The Global API Key does not work here.")

    if r.status_code in (401, 403):
        raise RuntimeError("Cloudflare refused the token (" + str(r.status_code) + "): it needs Zone: Read and DNS: Edit")

    if r.status_code >= 400 or (isinstance(j, dict) and j.get("success") is False):
        errs = (j.get("errors") if isinstance(j, dict) else None) or []
        msg = "; ".join(str(e.get("message", e)) for e in errs[:2] if isinstance(e, dict)) or r.text[:120]
        raise RuntimeError(f"Cloudflare answered {r.status_code}: {msg}")

    return j if isinstance(j, dict) else {}


def _pages(token: str, path: str, params: dict | None = None) -> list[dict]:
    out = []

    for page in range(1, 40):
        j = req(token, "GET", path, params={**(params or {}), "page": page, "per_page": 100})
        rows = j.get("result") or []
        out.extend(x for x in rows if isinstance(x, dict))
        total = int(((j.get("result_info") or {}).get("total_pages")) or 1)

        if page >= total or not rows:
            break

    return out


def zones(token: str) -> list[dict]:
    return [{"id": z.get("id"), "name": str(z.get("name") or "")} for z in _pages(token, "/zones") if z.get("id")]


def records(token: str, zone_id: str) -> list[dict]:
    return _pages(token, f"/zones/{zone_id}/dns_records")

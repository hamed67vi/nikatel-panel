"""The provider under the name of a server (the owner, 2026-10-30, with a picture of the Servers list: "why do some servers not show their provider?"). The line comes from the provider typed in the server's form;
a server added with that field empty showed nothing although the panel's own hourly geo look had found it - and a look that got no answer (the geo services answer nothing or "too many requests" when many servers
are asked together) was never repeated, because it was stamped as done. The row falls back to the geo provider, and an empty look is repeated after an hour."""
import json
import re
import shutil
import subprocess
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from nikatel.db import session
from nikatel.models import Server
from nikatel.services import nodes

LAB = (Path(__file__).resolve().parents[1] / "nikatel" / "web" / "lab.js").read_text(encoding="utf-8")
NODE = shutil.which("node")
GEO = {"provider": "Hetzner Online GmbH", "domain": "hetzner.com", "asn": "24940", "country_code": "DE", "country": "Germany", "city": "Falkenstein", "sources": 2, "total": 2}


def _srv(geo=None):
    return SimpleNamespace(extra={"geo": geo} if geo is not None else {})


def test_a_provider_that_was_not_found_is_asked_again_after_an_hour_not_before_and_a_found_one_never():
    now = 1_800_000_000
    assert nodes._geo_due(_srv(), now)                                                                      # never asked
    assert not nodes._geo_due(_srv({"provider": "Aruba", "provider_at": now - 10 * nodes.GEO_RETRY}), now)   # found: not asked again by the job
    assert not nodes._geo_due(_srv({"domain": "x.net", "provider_at": now - 10 * nodes.GEO_RETRY}), now)     # (a domain is an answer too)
    assert not nodes._geo_due(_srv({"provider": "", "provider_at": now - 60}), now)                           # asked a minute ago, nothing came back: wait
    assert nodes._geo_due(_srv({"provider": "", "provider_at": now - nodes.GEO_RETRY}), now)                  # an hour ago: again


def test_the_hourly_job_asks_only_the_servers_whose_provider_is_still_unknown(client, monkeypatch):
    now = int(time.time())
    rows = {"never": {}, "found": {"geo": {"provider": "Aruba", "provider_at": now - 99999}}, "just-asked": {"geo": {"provider": "", "provider_at": now - 30}}, "old-empty": {"geo": {"provider": "", "provider_at": now - 99999}}}

    with session() as db:
        for i, (name, extra) in enumerate(rows.items()):
            db.add(Server(name=name, host=f"203.0.113.{i + 1}", role="foreign", country="DE", extra=extra))

    asked = []
    monkeypatch.setattr(nodes, "lookup", lambda host, want_provider=True: asked.append(host) or dict(GEO))
    nodes.geo_missing_job()

    with session() as db:
        by = {s.name: s for s in db.scalars(select(Server))}

    assert sorted(asked) == ["203.0.113.1", "203.0.113.4"]                                                  # "never" and "old-empty" - not the found one, not the one asked 30 s ago
    assert by["never"].extra["geo"]["provider"] == "Hetzner Online GmbH" and by["old-empty"].extra["geo"]["provider"] == "Hetzner Online GmbH"
    assert by["found"].extra["geo"]["provider"] == "Aruba" and by["just-asked"].extra["geo"]["provider"] == ""


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_the_row_shows_the_typed_provider_else_the_one_the_geo_look_found():
    line = re.search(r"^const labProv = .*$", LAB, re.MULTILINE).group(0)
    prog = line + """
console.log(JSON.stringify([
  labProv({provider: "YottaSrc", extra: {geo: {provider: "Other"}}}), labProv({provider: "", extra: {geo: {provider: "Hetzner Online GmbH"}}}), labProv({provider: "", extra: {}}), labProv({provider: "", extra: null}), labProv({}),
]));"""
    out = subprocess.run([NODE, "-e", prog], check=False, capture_output=True, text=True, timeout=30)
    assert out.returncode == 0, out.stderr
    assert json.loads(out.stdout) == ["YottaSrc", "Hetzner Online GmbH", "", "", ""]      # (what is typed wins; nothing known = no line)
    assert "const pv = labProv(s); return b ?" in LAB and "${esc(pv)}</small>" in LAB
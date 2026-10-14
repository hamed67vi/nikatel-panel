"""The write of a PasarGuard core config sends the query parameter the panel requires (PasarGuard 5.x answers 422 without `restart_nodes`)."""
from __future__ import annotations

from nikatel.services import core

CFG = {"inbounds": [{"tag": "in1", "protocol": "vless"}], "outbounds": [{"tag": "direct", "protocol": "freedom"}], "routing": {"rules": []}}


def test_a_pasarguard_core_is_written_with_restart_nodes(monkeypatch):
    seen = []

    def fake(c, method, path, **kw):
        seen.append((method, path))

        if path == "/api/cores":
            return {"cores": [{"id": 4, "name": "Main"}]}

        if method == "GET" and path == "/api/core/4":
            return {"name": "Main", "type": "xray", "config": CFG, "exclude_inbound_tags": [], "fallbacks_inbound_tags": []}

        return kw.get("body")

    monkeypatch.setattr(core, "call", fake)
    c = {"id": 1, "url": "https://panel.example.test", "type": "pasarguard"}
    cur = core.get(c)
    assert cur["kind"] == "pasarguard" and cur["core_id"] == 4
    core._put(c, cur, CFG)
    puts = [p for m, p in seen if m == "PUT"]
    assert puts == ["/api/core/4?restart_nodes=true&modify_users=false"]


def test_a_marzban_core_is_written_to_its_one_config(monkeypatch):
    seen = []

    def fake(c, method, path, **kw):
        seen.append((method, path))

        if path == "/api/cores":
            raise core.PanelError("HTTP 404", 404)

        return CFG if method == "GET" else kw.get("body")

    monkeypatch.setattr(core, "call", fake)
    c = {"id": 1, "url": "https://panel.example.test", "type": "marzban"}
    cur = core.get(c)
    core._put(c, cur, CFG)
    assert ("PUT", "/api/core/config") in seen

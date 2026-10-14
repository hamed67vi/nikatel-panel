"""The log of a node: Marzban answers with one body; PasarGuard with an endless stream that must be read for a few seconds only."""
from __future__ import annotations

from nikatel.services import panels, pg_nodes


def test_a_marzban_node_log_is_one_answer(monkeypatch):
    monkeypatch.setattr(panels, "call", lambda c, m, p, **kw: {"logs": ["line one", "line two"]})
    assert panels.node_logs({"id": 1, "type": "marzban"}, 3) == "line one\nline two"


def test_a_pasarguard_node_log_goes_through_the_bounded_stream_reader(monkeypatch):
    def never_ends(*a, **kw):
        raise AssertionError("panels.call would wait for a body that never ends")

    monkeypatch.setattr(panels, "call", never_ends)
    monkeypatch.setattr(pg_nodes, "node_logs", lambda c, nid, lines=150: {"node_id": nid, "lines": ["first", "second"]})
    assert panels.node_logs({"id": 2, "type": "pasarguard"}, 5) == "first\nsecond"

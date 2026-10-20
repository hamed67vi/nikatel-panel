"""A build whose link does not pass tries the other link models (the owner, 2026-10-20: "there are several GRE models - when the build finds that one does not answer it must test the others").

What is tested: the order of the models, that a tunnel the owner builds from Create tunnel follows them and one the Doctor or a test builds does not, that what the failed model made is removed before the next
is tried, that a failure that is not about the link (a Backhaul service that does not start) is never retried, and the words that said GRE for every link."""
from types import SimpleNamespace

import pytest

from nikatel.services import builder, linkmodels
from nikatel.services.builder import LinkFailed


def make_build(**kw):
    srv = lambda i: SimpleNamespace(id=i, name=f"s{i}", host=f"203.0.113.{i}")
    opts = kw.pop("opts", {})
    return builder.Build(9001, srv(1), srv(2), [], "tcpmux", opts=opts, name="t-fb", **kw)


@pytest.fixture
def walk(monkeypatch):
    """Drive `_build_worker` with a `_build` that fails for the links in `bad` (LinkFailed) and passes for the others."""
    def run(b, bad, other_error=None):
        log = {"built": [], "said": [], "rolled": 0, "final": None}

        def fake_build(bb):
            link = str(bb.opts.get("link") or "gre")
            log["built"].append(link)
            bb.created.append(("gre", None))

            if link in bad:
                raise LinkFailed(f"the {link} link is up but the inner addresses cannot ping each other")

            if other_error:
                raise RuntimeError(other_error)

        monkeypatch.setattr(builder, "_build", fake_build)
        monkeypatch.setattr(builder, "_say", lambda aid, line, done=None, ok=None: log["said"].append((line, done, ok)))
        monkeypatch.setattr(builder, "_rollback", lambda bb: log.__setitem__("rolled", log["rolled"] + 1))
        monkeypatch.setattr(builder, "_wg_ready", lambda bb: False)                                               # (no server to ask: WireGuard is tried only where the owner allowed it; test_wireguard_complete.py has the other case)
        builder._BUILDING.add("pair-fb")
        builder._build_worker(b, "pair-fb")
        log["final"] = next((s for s in reversed(log["said"]) if s[1]), None)
        return log

    return run


def test_the_models_come_in_one_order_and_wireguard_only_where_it_is_allowed():
    assert builder.next_link([]) == "gre" and builder.next_link(["gre"]) == "fou"
    order, tried = [], []
    while (k := builder.next_link(tried)):
        order.append(k)
        tried.append(k)
    assert order == ["gre", "fou", "gue", "ipsecnat", "ipipfou", "ipipgue", "ipip", "ipsec"]                  # (the ones in UDP first; no wg: a build never installs a package for a fall-back)
    assert builder.next_link(order) == "" and builder.next_link(order, wg_ok=True) == "wg"
    assert set(builder.FALLBACK_LINKS) == set(linkmodels.ALL_LINKS)                                                  # (no model is forgotten, none is made up)


def test_a_link_that_does_not_pass_is_followed_by_the_next_model_until_one_passes(walk):
    b = make_build(link_fallback=True)
    log = walk(b, bad={"gre", "fou"})
    assert log["built"] == ["gre", "fou", "gue"] and b.opts["link"] == "gue" and log["rolled"] == 2            # what each failed model made was removed before the next
    assert b.created == [("gre", None)]                                                                       # (only what the last attempt made is kept for a later rollback)
    texts = [s[0] for s in log["said"]]
    assert any("GRE did not pass between these two servers" in t for t in texts) and any("Trying the next link model: GRE over UDP (GUE)" in t for t in texts)
    assert log["final"] is None                                                                               # (the build itself ends the story; the worker says nothing more when it passed)


def test_a_build_that_is_not_the_owners_does_not_walk_the_models(walk):
    for kw in ({}, {"link_fallback": True, "standby_of": "t1"}):
        b = make_build(**kw)
        log = walk(b, bad={"gre"})
        assert log["built"] == ["gre"] and log["rolled"] == 1                                                  # (the worker's own rollback of a stopped build)
        assert log["final"][2] is False and "Stopped:" in log["final"][0] and "Everything created was removed again" in log["final"][0]


def test_when_no_model_passes_the_story_says_which_were_tried(walk):
    b = make_build(link_fallback=True)
    log = walk(b, bad=set(linkmodels.ALL_LINKS))
    assert log["built"] == ["gre", "fou", "gue", "ipsecnat", "ipipfou", "ipipgue", "ipip", "ipsec"]
    assert any("None of the link models passed" in s[0] and "tried: GRE," in s[0] and "IPsec (ESP)" in s[0] for s in log["said"])
    assert log["final"][2] is False and "Stopped:" in log["final"][0]


def test_wireguard_is_tried_last_only_when_the_owner_allowed_its_tool(walk):
    b = make_build(link_fallback=True, opts={"install_wg": True})
    log = walk(b, bad={k for k in linkmodels.ALL_LINKS if k != "wgchiselfwd"})
    assert log["built"][-1] == "wgchiselfwd" and len(log["built"]) == len(linkmodels.ALL_LINKS)          # (WireGuard in Chisel is the very last resort: after WireGuard itself)


def test_an_error_that_is_not_about_the_link_is_never_retried(walk):
    b = make_build(link_fallback=True)
    log = walk(b, bad=set(), other_error="the backhaul service on the Iran server ended failed")
    assert log["built"] == ["gre"] and log["final"][2] is False                                                # (a model change would not help: Backhaul itself broke)


def test_the_option_is_kept_by_the_check_and_taken_out_before_the_record():
    assert builder.clean_bh_opts({"link_fallback": True, "link": "fou"}) == {"link": "fou", "link_fallback": True}
    assert builder.clean_bh_opts({"link_fallback": False}) == {}


def test_the_api_asks_for_the_fall_back_for_a_build_of_the_owner(client, monkeypatch):
    from nikatel.config import get_settings

    monkeypatch.setattr(get_settings(), "read_only", False)
    seen = []
    monkeypatch.setattr(builder, "start_build", lambda iran_id, foreign_id, ports, transport, mtu=0, keepalive=75, forced_name="", nodelay=True, opts=None: seen.append(dict(opts or {})) or 7)
    body = {"iran_id": 1, "foreign_id": 2, "ports": "8075", "kind": "backhaul"}
    assert client.post("/api/tunnels", json={**body, "transport": "tcpmux"}).status_code == 202
    assert client.post("/api/tunnels", json={**body, "transport": "fou:tcpmux"}).status_code == 202
    assert seen == [{"link_fallback": True}, {"link": "fou", "link_fallback": True}]


def test_the_words_of_the_story_say_the_link_that_ran():
    import inspect

    src = inspect.getsource(builder._build)
    assert "established over {title}" in src and "ms over {title}" in src and "established over GRE" not in src

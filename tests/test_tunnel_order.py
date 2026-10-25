"""The order of the tunnel list (1.69.62; since 1.69.144 a problem moves nothing - the owner, 2026-10-26: "when there is a problem the cards must not change their place, the places are fixed"): every location together, then alphabetical. The comparison runs through node."""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

APP = (Path(__file__).resolve().parent.parent / "nikatel" / "web" / "app.js").read_text(encoding="utf-8")
NODE = shutil.which("node")


def _order(tunnels, servers, cache=None):
    start, end = APP.index("const tunKeyRow = t =>"), APP.index("\n", APP.index("const tunCmp = (x, y) =>"))
    prog = ("const state = {serversCache: " + json.dumps(servers) + ", tunRowCache: new Map(" + json.dumps(list((cache or {}).items())) + ")}; const tunSev = t => t.sev || '';\n" + APP[start:end]
            + "\nconst rows = " + json.dumps(tunnels) + "; console.log(JSON.stringify([...rows].sort(tunCmp).map(t => t.name)));")
    out = subprocess.run([NODE, "-e", prog], check=False, capture_output=True, text=True, timeout=30)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


SERVERS = [{"name": "DE2", "country": "de"}, {"name": "DE3", "country": "DE"}, {"name": "FI", "country": "fi"}, {"name": "US", "country": "us"}, {"name": "UK", "country": "gb"}, {"name": "CA", "country": "ca"}, {"name": "ODD", "country": ""}]


def _t(name, foreign, sev="", id_=1):
    return {"id": id_, "name": name, "foreign_name": foreign, "sev": sev}


@pytest.mark.skipif(NODE is None, reason="node is not installed")

def test_a_problem_moves_nothing_every_location_together_alphabetically_and_an_unknown_one_last():
    rows = [_t("us-a", "US"), _t("fi-a", "FI"), _t("de3-a", "DE3"), _t("uk-a", "UK"), _t("ca-a", "CA"), _t("de2-a", "DE2"), _t("odd-a", "ODD"), _t("nowhere-a", "GONE"),
            _t("uk-slow", "UK", "sev-yellow"), _t("fi-cut", "FI", "sev-red"), _t("us-warn", "US", "sev-yellow")]
    got = _order(rows, SERVERS)
    assert got == ["ca-a", "fi-a", "fi-cut", "de2-a", "de3-a", "uk-a", "uk-slow", "us-a", "us-warn", "nowhere-a", "odd-a"]      # Canada, Finland, Germany (DE2 and DE3 together), United Kingdom, United States - the cut and the yellow ones stand where their name puts them; the servers with no known country last, by name
    healthy = [{**r, "sev": ""} for r in rows]
    assert _order(healthy, SERVERS) == got      # (the same list whether a tunnel has a problem or not)


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_the_tunnels_of_one_location_stand_together_by_name_and_the_case_of_a_name_does_not_matter():
    rows = [_t("de2-pnt", "DE2"), _t("fi-x", "FI"), _t("de-b", "DE3"), _t("DE-a", "DE2")]
    assert _order(rows, SERVERS) == ["fi-x", "DE-a", "de-b", "de2-pnt"]


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_a_tunnel_that_is_being_built_again_keeps_the_place_of_the_row_it_replaces():
    real = _t("us-a", "US", "sev-red")                                                       # it was a problem row
    rows = [{**_t("us-a", "US", "", id_=-1)}, _t("ca-a", "CA"), _t("de2-a", "DE2")]
    assert _order(rows, SERVERS, {"us-a": real}) == ["ca-a", "de2-a", "us-a"]                # it stands where its name puts it, rebuilt or not
    assert _order(rows, SERVERS) == ["ca-a", "de2-a", "us-a"]


def test_the_list_is_drawn_in_groups_by_type_each_group_in_the_order():          # (1.69.62 took the grouping away, the owner asked for it again on 2026-10-22: tests/test_tunnel_groups.py)
    assert "tunGroups(rows).flatMap(g => g.a).map(row)" in APP and "[...rows].sort(tunCmp).forEach(t =>" in APP and "tunGroupHead" not in APP and "TUN_TYPES.indexOf(sortType" not in APP and "const sortType =" not in APP
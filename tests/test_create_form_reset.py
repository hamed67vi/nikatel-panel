"""Create tunnel: picking another server wipes what the port check said about the old pair, and what was ticked for it (the owner, 2026-10-21: "I changed the foreign server and the check box did not go - if the server
changed, the box of the old server must go, even if I ticked it").

The form keeps the state of every checkbox it ever showed (`bk.chk`), so a tick made for a replace of the OLD pair would have come back, ticked, under the new one - and the answer of the check for an older pair was put
in `bk.ck` before it was compared with the pair on the screen, so the Build button could be judged by the old servers' ports. Tested: the two pure helpers are run for real with node, and the shape of `bkCheck`."""
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

APP = (Path(__file__).resolve().parent.parent / "nikatel" / "web" / "app.js").read_text(encoding="utf-8")


def helper(name: str) -> str:
    m = re.search(rf"^const {name} = .*?;[^\n]*$", APP, re.MULTILINE)
    assert m, name
    return m.group(0)


def bk_check_source() -> str:
    return APP.split("window.bkCheck = async (form, force) => {", 1)[1].split("\nfunction bkCheckRender", 1)[0]


@pytest.mark.skipif(shutil.which("node") is None, reason="no node")
def test_the_pair_of_a_key_and_the_ticks_that_belong_to_one_pair_are_read_right():
    js = helper("bkPairOf") + "\n" + helper("bkTickKeys") + """
const chk = {replace_5: true, replace_9: false, stale_nikatel_x: true, dns_follow: true, dns_set: true, nodelay: true, tune: false, accept_udp: true};
console.log(JSON.stringify({a: bkPairOf("3|7|8072"), b: bkPairOf("3|7|9000"), c: bkPairOf("3|8|8072"), d: bkPairOf(""), e: bkPairOf(undefined), ticks: bkTickKeys(chk).sort()}));
"""
    out = json.loads(subprocess.run(["node", "-e", js], capture_output=True, text=True, check=True).stdout)
    assert out["a"] == out["b"] == "3|7" and out["c"] == "3|8" and out["d"] == "" and out["e"] == ""                  # (the ports are not part of the pair: another port keeps the ticks of the same pair)
    assert out["ticks"] == ["dns_follow", "replace_5", "replace_9", "stale_nikatel_x"]                                # (what belongs to the pair; the options of the tunnel - no-delay, tune, UDP, the DNS box - are not)


def test_a_new_pair_clears_the_box_and_the_ticks_at_once_and_an_answer_for_an_older_pair_is_dropped():
    src = bk_check_source()
    reset = "if (bk.ckKey && bkPairOf(bk.ckKey) !== bkPairOf(key)) { bkTickKeys(bk.chk).forEach(n => delete bk.chk[n]); box.innerHTML = \"\"; bk.ck = null; bkBtn(form); }"
    assert reset in src and src.index(reset) < src.index("bk.ckKey = key;")                                              # (before the new answer is asked for: the old warning is gone while the new one is on its way)
    assert "bk.ck = await" not in src and "if (key !== bk.ckKey) return;" in src and src.index("if (key !== bk.ckKey) return;") < src.index("bk.ck = res; bkCheckRender(form);")
    assert src.index("res = await api(") < src.index("if (key !== bk.ckKey) return;")


def test_emptying_the_foreign_server_or_the_ports_also_forgets_the_ticks_of_the_old_pair_and_the_old_answer():
    first = next(ln for ln in bk_check_source().split("\n") if 'if (!+f.get("foreign_id") || !ports)' in ln)
    assert "bkTickKeys(bk.chk).forEach(n => delete bk.chk[n])" in first and "bk.ck = null" in first and 'bk.ckKey = ""' in first


def test_the_check_still_runs_when_a_server_is_picked_and_a_tick_of_the_same_pair_is_kept():
    assert "onchange=\"bkCheck(this.closest('form'))\"" in APP                                                          # (the foreign list asks again at once)
    assert 'oninput="bkKeep(this)"' in APP and "bk.chk[cb.name] = cb.checked" in APP                                    # (a tick the owner makes for this pair is still remembered across a redraw of the form)

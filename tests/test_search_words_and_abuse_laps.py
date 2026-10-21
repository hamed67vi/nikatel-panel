"""Two things the owner saw on 2026-10-21: (1) in the tunnel list "tr" also found `de2-ira1` - its Iran server's provider is "Acme Data Transmission company", and the search read the provider as ordinary text;
(2) the Abuse-Defender install on a new server took 78 s and nothing said which step was slow. The provider, domain and country are a second text (`data-qx`): a word of 3 letters or more is found in it as part of
a word, a shorter one only as a WHOLE word (the country code "tr" finds the Turkish servers, not "Transmission"). The install script puts the seconds of each step in its line and refreshes the package list once."""
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from nikatel.ssh import render_action

APP = (Path(__file__).resolve().parents[1] / "nikatel" / "web" / "app.js").read_text(encoding="utf-8")
PARAMS = {"URL": "https://raw.githubusercontent.com/example/lists/main/abuse.txt", "WL": "1.1.1.1"}


def _hits(cases):
    compact = next(ln for ln in APP.split("\n") if ln.startswith("const nkCompact"))
    hit = next(ln for ln in APP.split("\n") if ln.startswith("const nkHit"))
    prog = compact + "\n" + hit + "\nconsole.log(JSON.stringify(" + json.dumps(cases) + ".map(([h, q, x]) => nkHit(h, q, x))));"
    return json.loads(subprocess.run(["node", "-e", prog], capture_output=True, text=True, timeout=30, check=True).stdout)


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_a_short_word_is_not_found_inside_a_provider_name_but_a_whole_word_and_a_longer_one_are():
    de2 = "de2-ira1 ira1 de2 backhaul tcpmux up 203.0.113.11 198.51.100.12 8072"
    provider = "alpha beta gamma company pjsc acme data transmission company acme.ir hostco hostco.com de"
    cases = [
        [de2, "tr", provider],                         # the owner's case: not found
        [de2, "transmission", provider],               # a longer word is found in the provider
        [de2, "acm", provider],
        [de2, "host", provider],
        [de2, "de", provider],                         # the country code, a whole word
        ["tr3-rb4 rb4 tr3 backhaul", "tr", ""],           # the name still matches
        ["it-rb2 rb2 it", "tr", ""],                      # (the compact form "itrb2" has always matched, the old behaviour)
        [de2, "ira", ""],
        [de2, "ira1 de2", provider],                 # several words
    ]
    assert _hits(cases) == [False, True, True, True, True, True, True, True, True]


def test_the_row_carries_the_provider_text_apart_and_the_filter_passes_it():
    assert 'data-qx="${esc([t.iran_name, t.foreign_name].map(' in APP and "nkHit(tr.dataset.q, q, tr.dataset.qx)" in APP
    row = next(ln for ln in APP.split("\n") if 'data-qx="${esc(' in ln and "t.forwarded_ports" in ln)
    q_text = row.split('data-q="${esc([', 1)[1].split('data-qx=', 1)[0]
    assert "sv.provider" not in q_text and "sv.country" not in q_text                    # (not in the main text any more)


def test_the_abuse_install_says_the_seconds_of_each_step_and_refreshes_the_package_list_once():
    s = render_action("abuse_install", PARAMS)

    for n in range(1, 6):
        line = next(ln for ln in s.splitlines() if ln.startswith(f'lap; echo "STEP {n}/5'))
        assert '${LAPS} s)' in line, n

    assert s.count("apt-get update -y") == 1 and s.count("upd;") == 3 and "UPDATED=1" in s
    assert s.index("lap() {") < s.index("command -v iptables")


def test_the_lap_helper_works_in_a_real_shell(tmp_path):
    bash = shutil.which("bash")

    if bash is None:
        pytest.skip("bash is not installed here")

    s = render_action("abuse_install", PARAMS)
    head = s.split("command -v iptables", 1)[0]
    r = subprocess.run([bash, "-c", head + "lap; echo \"took ${LAPS} s\"; upd() { echo called; }; upd"], capture_output=True, text=True, timeout=30, check=False)
    assert r.returncode == 0 and re.search(r"took \d+ s", r.stdout), r.stderr[-300:]
def test_close_and_esc_put_a_window_that_still_works_in_the_bar_and_the_bar_keeps_following_it():
    assert 'onclick="closeOrDock()" aria-label="Close"' in APP and 'onclick="closeModal()" aria-label="Close"' not in APP
    fn = APP.split("window.closeOrDock = () => {", 1)[1].split("};", 1)[0]
    assert 'querySelector(".fx-line.run")' in fn and "minModal();" in fn and fn.index("minModal();") < fn.index("closeModal();")
    assert 'closeOrDock();' in APP.split('document.addEventListener("keydown", e => { if (e.key === "Escape"', 1)[1].split("\n", 1)[0]
    follow = APP.split("async function followStory(aid, box, maxPolls) {", 1)[1].split("document.addEventListener", 1)[0]
    assert follow.count('b0.closest(".docked")') == 1 and follow.count('b.closest(".docked")') == 1


def test_adding_a_server_with_an_install_shows_the_steps_in_a_window_not_only_a_toast():
    flow = APP.split("if (items.length && connected) {", 1)[1].split("} else if (items.length) toast", 1)[0]
    assert 'showModal(`<b>⬇ Extras on ${esc(body.name)}</b>`' in flow and 'followStory(r.id, $("#ap-story") || document.createElement("div"), 1800)' in flow
    assert "the steps show on its row" not in flow

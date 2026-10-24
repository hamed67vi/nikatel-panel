"""The Reviewer tab of the Tunnel Doctor (web/review.js, drawn by app.js; the service is tests/test_reviewer.py): the real functions of review.js run in node over invented answers of /api/reviewer.

  what it shows   the switch of the AI and whether it is ready, every review (the verdict, the findings, "Copy report", "Story"), the lessons (Approve / Dismiss while proposed, Revoke while in force, nothing once dismissed or over)
  what it does    every button calls the API the way the service expects; a read-only panel can read and not change; a copy the browser refuses opens the report in a box
  what it never does   put a word of a tunnel name, a summary or a finding into the page as it is
"""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

WEB = Path(__file__).resolve().parent.parent / "nikatel" / "web"
REVIEW = (WEB / "review.js").read_text(encoding="utf-8")
APP = (WEB / "app.js").read_text(encoding="utf-8")
CSS = (WEB / "lab.css").read_text(encoding="utf-8")


def test_the_tab_is_in_the_doctors_box_the_script_is_loaded_and_the_css_is_there():
    index = (WEB / "index.html").read_text(encoding="utf-8")
    main = (Path(__file__).resolve().parent.parent / "nikatel" / "main.py").read_text(encoding="utf-8")
    assert '<script src="/static/review.js"></script>' in index and index.index("knowledge.js") < index.index("review.js") and '"review.js"' in main          # (the links carry their change time: main.py lists the file)
    assert 'apTab = ["done", "moves", "ana", "know", "rev"].includes(v)' in APP and '["now", "done", "moves", "ana", "know", "rev"].forEach(p =>' in APP and 'if (k === "rev") rvLoad();' in APP and 'if (apTab === "rev") rvLoad();' in APP
    assert '${tabBtn("rev", "chat", "Reviewer", (state.rv && state.rv.stats.proposed) || 0,' in APP and 'id="ap-t-rev"><div id="rv-box">${(window.rvHtml && rvHtml()) ||' in APP          # (the count on the tab is the lessons that wait for the owner)
    for rule in ("html body #page .rv-row {", "html body #page .rv-less {", "html body #page .rv-fl {", 'html[data-mode="light"] body #page .rv-row'):
        assert rule in CSS


@pytest.fixture()
def run(tmp_path):
    node = shutil.which("node")

    if node is None:
        pytest.skip("node is not installed here")

    def go(script, rv=None):
        driver = tmp_path / "rv.js"
        driver.write_text(
            "globalThis.window = globalThis;\n"
            "const esc = s => String(s).replace(/[&<>\"']/g, c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '\"': '&quot;', \"'\": '&#39;'}[c]));\n"
            "const when = t => 'WHEN' + t, ago = t => 'AGO' + t, toasts = [], calls = [], modals = [];\n"
            "const state = {rv: " + json.dumps(rv) + "};\n"
            "const toast = (t, k) => toasts.push([t, k]), showModal = (title, body, cls) => modals.push({title, body, cls}), storyHtml = a => 'STORY:' + a.id;\n"
            "let box = null; const $ = sel => sel === '#rv-box' ? box : null;\n"
            "const SERVE = " + json.dumps(rv) + ";\n"
            "globalThis.setTimeout = () => 0;          // (rvRun looks again after 4, 15 and 45 s: a timer would keep node waiting)\n"
            "const api = async (url, o) => { calls.push([url, (o && o.method) || 'GET', (o && o.body) || '']); return url === '/api/reviewer' && !o ? SERVE : url.startsWith('/api/actions/') ? {id: 4, ref: 'tun-a'} : url.endsWith('/report') ? {ok: true, report: 'Doctor review - tunnel tun-a'} : {ok: true}; };\n"
            "globalThis.navigator = {clipboard: {writeText: async t => { if (globalThis.denyCopy) throw new Error('denied'); globalThis.copied = t; }}};\n"
            + REVIEW + "\n(async () => { const res = {};\n" + script + "\nprocess.stdout.write(JSON.stringify(res)); })().catch(e => { console.error(e); process.exit(1); });\n", encoding="utf-8")
        r = subprocess.run([node, str(driver)], capture_output=True, text=True, encoding="utf-8", timeout=60, check=False)
        assert r.returncode == 0, r.stderr[-800:]
        return json.loads(r.stdout)

    return go


REVIEW_ROW = {"id": 11, "ts": 1, "at": 1000, "tunnel": "tun-a", "iran": "ir-1", "foreign": "fo-1", "source": "auto", "ok": False, "secs": 1090, "severity": "bad", "verdict": "wasteful", "summary": "It rebuilt a tunnel whose server was off.",
              "mistake": "Wait for the server.", "ai": True, "ai_tried": True, "ai_note": "", "lesson_id": 3, "has_report": True,
              "findings": [{"code": "server_off_during", "sev": "bad", "text": "The Iran server went off 36 s into the repair."}, {"code": "many_types", "sev": "note", "text": "26 tunnel types were tried."}]}
LESSONS = [{"id": 3, "kind": "skip_step", "state": "proposed", "text": "Do not try the step “rebuild” on the tunnel tun-a for 5 days", "why": "it never healed this tunnel", "evidence": [11, 12], "days_left": 0},
           {"id": 2, "kind": "avoid_kind", "state": "approved", "text": "Do not try the tunnel type paqet between ir-1 ⇄ fo-1 for 3 days", "why": "w", "evidence": [9], "days_left": 2},
           {"id": 5, "kind": "note", "state": "approved", "text": "For the developer: a rule it lacks", "why": "w", "evidence": [], "days_left": 0},
           {"id": 1, "kind": "skip_step", "state": "dismissed", "text": "Do not try relink", "why": "w", "evidence": [], "days_left": 0},
           {"id": 4, "kind": "skip_step", "state": "expired", "text": "Do not try rebuild", "why": "w", "evidence": [], "days_left": 0}]


def view(**over):
    base = {"ok": True, "read_only": False, "ai": {"on": True, "ready": True, "who": "Claude", "per_hour": 6}, "reviews": [REVIEW_ROW], "lessons": LESSONS, "stats": {"reviews": 1, "by_ai": 1, "bad": 1, "proposed": 1}}
    base.update(over)
    return base


def test_the_tab_shows_the_reviews_and_the_lessons_with_the_buttons_each_one_needs(run):
    res = run("res.html = window.rvHtml();", view())
    h = res["html"]
    assert 'class="kn-bar rv-bar"' in h and 'type="checkbox" checked' in h.split("Let the AI write the reviews")[0] and "AI ready · Claude" in h and "1 review · 1 serious" in h and "at most 6 an hour" in h
    assert '<h4 class="kn-h">Lessons for the Doctor · 1 waiting for you</h4>' in h and "Repairs that went badly or took long" in h
    row = h.split('<div class="rv-row bad">')[1].split("</ul>")[0]
    assert "AGO1000 ago" in row and "<b>tun-a</b>" in row and "ir-1 ⇄ fo-1" in row and '<span class="pill err">serious</span>' in row and "wasted work" in row and "not healed · 18 min 10 s" in row
    assert 'onclick="rvStory(11)"' in row and 'onclick="rvReport(11)"' in row and "It rebuilt a tunnel whose server was off." in row and "<b>It should have:</b> Wait for the server." in row
    assert '<li class="bad">The Iran server went off 36 s into the repair.</li>' in row and '<li class="note">26 tunnel types were tried.</li>' in row
    assert "A lesson was proposed (#3)" in h
    parts = {x: h.split(f'<div class="rv-less {x}">')[1].split('<span class="rv-why')[0] for x in ("proposed", "approved", "dismissed", "expired")}
    assert "rvLesson(3, 'approved')" in parts["proposed"] and "rvLesson(3, 'dismissed')" in parts["proposed"] and ">Approve<" in parts["proposed"] and "in force" not in parts["proposed"] and "proposed" in parts["proposed"]
    assert "2 more days" in parts["approved"] and "rvLesson(2, 'dismissed')" in parts["approved"] and ">Revoke<" in parts["approved"]
    assert "<button" not in parts["dismissed"] and "<button" not in parts["expired"] and "ended" in parts["expired"]
    note = h.split("For the developer: a rule it lacks")[1].split('<span class="rv-why')[0]
    assert "<button" not in note                                                                                                  # (a note that is approved has nothing to revoke: it changes nothing)
    assert "from 2 reviews" in h and "from 1 review<" in h


def test_the_note_lesson_is_acknowledged_not_approved_and_a_quiet_panel_says_what_it_waits_for(run):
    one = [{"id": 6, "kind": "note", "state": "proposed", "text": "For the developer: x", "why": "x", "evidence": [1], "days_left": 0}]
    h = run("res.html = window.rvHtml();", view(lessons=one, stats={"reviews": 1, "by_ai": 1, "bad": 1, "proposed": 1}))["html"]
    assert ">Noted<" in h and ">Approve<" not in h
    quiet = run("res.html = window.rvHtml();", view(reviews=[], lessons=[], ai={"on": True, "ready": False, "who": "", "per_hour": 6}, stats={"reviews": 0, "by_ai": 0, "bad": 0, "proposed": 0}))["html"]
    assert "No review yet" in quiet and "Lessons for the Doctor" not in quiet and "no AI key - the findings still work" in quiet and "0 reviews · 0 serious" in quiet
    off = run("res.html = window.rvHtml();", view(ai={"on": False, "ready": True, "who": "Claude", "per_hour": 6}))["html"]
    assert 'class="pill warn"' in off.split("AI ready")[0].rsplit("<span", 1)[1] and 'type="checkbox" checked' not in off.split("Let the AI write the reviews")[0]          # (switched off: the label is not a green "ready")


def test_a_read_only_panel_shows_everything_and_can_press_nothing(run):
    h = run("res.html = window.rvHtml();", view(read_only=True))["html"]
    assert 'onchange="rvSet({ai: this.checked})"' in h and h.count("disabled") >= 5                                               # (the switch, "Review the last repair", Approve, Dismiss, Revoke)
    assert 'onclick="rvReport(11)"' in h and 'onclick="rvStory(11)"' in h                                                          # (reading is allowed)
    free = run("res.html = window.rvHtml();", view())["html"]
    assert "disabled" not in free


def test_nothing_written_by_a_log_or_an_ai_reaches_the_page_as_it_is(run):
    evil = {**REVIEW_ROW, "tunnel": "<img src=x onerror=alert(1)>", "summary": "<script>alert(2)</script>", "mistake": "\"><b>x</b>", "findings": [{"sev": "bad\" onmouseover=\"x", "text": "<i>t</i>"}], "ai_note": "<u>n</u>"}
    lesson = {**LESSONS[0], "text": "<b>t</b>", "why": "<s>w</s>"}
    h = run("res.html = window.rvHtml();", view(reviews=[evil], lessons=[lesson]))["html"]
    for bad in ("<img", "<script", "<b>x</b>", "<i>t</i>", "<u>n</u>", "<b>t</b>", "<s>w</s>", 'onmouseover="x'):
        assert bad not in h
    assert "&lt;img src=x onerror=alert(1)&gt;" in h and "&lt;script&gt;alert(2)&lt;/script&gt;" in h


def test_every_button_calls_the_api_the_way_the_service_expects(run):
    res = run(
        "await rvSet({ai: false}); await rvRun(); await rvLesson(3, 'approved'); await rvLesson(3, 'dismissed'); await rvStory(11);\n"
        "await rvReport(11); res.copied = globalThis.copied; res.toast = toasts.slice(-1)[0][0];\n"
        "globalThis.denyCopy = true; await rvReport(11); res.box = modals[modals.length - 1];\n"
        "res.calls = calls; res.story = modals[0];\n", view())
    assert res["calls"] == [["/api/reviewer", "PUT", '{"ai":false}'], ["/api/reviewer/run", "POST", ""], ["/api/reviewer/lessons/3/approved", "POST", ""], ["/api/reviewer/lessons/3/dismissed", "POST", ""], ["/api/actions/11", "GET", ""], ["/api/reviewer/reviews/11/report", "GET", ""], ["/api/reviewer/reviews/11/report", "GET", ""]]
    assert res["copied"] == "Doctor review - tunnel tun-a" and "Report copied" in res["toast"]
    assert res["story"]["title"].startswith("<b>Story of repair #11</b>") and "tun-a" in res["story"]["title"] and "STORY:4" in res["story"]["body"]
    assert res["box"]["title"] == "<b>Report of repair #11</b>" and '<textarea class="rv-report"' in res["box"]["body"] and "Doctor review - tunnel tun-a" in res["box"]["body"] and "by hand" in res["box"]["body"]          # (a copy the browser refuses: the report is in a box to copy by hand)


def test_a_page_that_has_not_changed_is_not_drawn_again(run):
    res = run(
        "box = {innerHTML: '', querySelector: sel => sel === '.rv-bar' && box.innerHTML ? {} : null, closest: () => ({scrollTop: 7})};\n"
        "let drawn = 0; Object.defineProperty(box, 'innerHTML', {get() { return this._h || ''; }, set(v) { this._h = v; drawn++; }});\n"
        "state.rv = null;\n"
        "await rvLoad(); res.first = drawn; await rvLoad(); res.second = drawn; res.kept = window.rvHtml() !== null;\n", view())
    assert res["first"] == 1 and res["second"] == 1 and res["kept"] is True                                                   # (read twice, the same: drawn once - a redraw is what makes a page jump)

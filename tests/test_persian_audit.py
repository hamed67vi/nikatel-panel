"""The Persian audit (1.69.49 + 1): every English text a person can read in the panel has a Persian text, the numbers and times are said in Persian, and the dictionaries keep one word for one thing.
What the audit measured is kept here:
 * the translation layer (i18n.js with the four dictionaries) is run with node over the texts the source of the pages and of the server hands to the screen;
 * the engine: a plural ending / a count / a "name - remark" text must not swallow other text;
 * the dictionaries: one spelling per word, no kasra / hamza, a half space where the language wants it, the holes of a text and of its Persian fit.
A text that is new in the code and has no Persian fails here: add it to nikatel/web/i18n.fa.ui2.js (the block "Persian audit")."""
import ast
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "nikatel" / "web"
NODE = shutil.which("node")
SKIP_NODE = pytest.mark.skipif(NODE is None, reason="node is not installed")

DICTS = ["i18n.fa.ui.js", "i18n.fa.msg.js", "i18n.fa.doctor.js", "i18n.fa.ui2.js"]

# node: the translation layer with the dictionaries, a stand-in for the page; reads the strings to look up from stdin
LAYER = r"""
const fs = require("fs"), vm = require("vm"), path = require("path");
const dir = process.argv[1];
class MO { observe() {} disconnect() {} takeRecords() { return []; } }
const sb = {localStorage: {getItem: k => k === "nk.lang" ? "fa" : null, setItem() {}}, document: {addEventListener() {}, documentElement: {}, querySelectorAll: () => [], body: {}}, MutationObserver: MO, NodeFilter: {}, queueMicrotask, console, setTimeout, clearTimeout};
sb.window = sb; vm.createContext(sb);
vm.runInContext(fs.readFileSync(path.join(dir, "i18n.js"), "utf8"), sb, {filename: "i18n.js"});
const dump = {}; let cur = "";
const a0 = sb.I18N.add;
sb.I18N.add = map => { for (const [k, v] of Object.entries(map)) (dump[cur] = dump[cur] || []).push([k, v]); return a0(map); };
for (const f of ["i18n.fa.ui.js", "i18n.fa.msg.js", "i18n.fa.doctor.js", "i18n.fa.ui2.js"]) { cur = f; vm.runInContext(fs.readFileSync(path.join(dir, f), "utf8"), sb, {filename: f}); }
const input = JSON.parse(fs.readFileSync(0, "utf8"));
if (input === "dump") console.log(JSON.stringify(dump));
else console.log(JSON.stringify(input.map(s => sb.I18N.tr(s))));
"""


def _run(payload) -> object:
    out = subprocess.run([NODE, "-e", LAYER, str(WEB)], input=json.dumps(payload), check=False, capture_output=True, text=True, timeout=120)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


def _translate(texts: list[str]) -> list[str]:
    return _run(texts)


def _dictionaries() -> dict[str, list[list[str]]]:
    return _run("dump")


# what stays Latin in a Persian sentence: names of protocols, engines, products, units and keys of settings
TECH = re.compile(
    r"\b(?:GRE|SSH|DNS|CDN|Mbit/s|Mb/s|Xray|PasarGuard|Marzban|Marzneshin|Backhaul|BackPack|Backpack|Paqet|Chisel|WireGuard|IPIP|HTTP|HTTPS|API|URL|IPv4|IPv6|IP|TCP|UDP|TLS|SNI|VPN|OK|ID|JSON|CSV|QR|Telegram|GitHub|Cloudflare|ArvanCloud"
    r"|CPU|RAM|MTU|ICMP|ESP|NAT|UUID|GB|MB|KB|TB|ms|BBR|fq|Mux|QUIC|KCP|DPI|HWID|TTL|UTC|RPS|PHP|root|raw|Stealth|WebSocket|Docker|REALITY|gRPC|REST|Shadowsocks|Trojan|VLESS|VMess|WSS|WS|PCK|YAML|IPsec|ZIP|zip|apt"
    r"|NIKATEL|READ|ONLY|false|true|JWT|AES|GCM|FOU|GUE|xDi|FEC|Mini|App|User|Agent|Mozilla|Discord|Clash|Outline|sing|box|NowPayments|Hetzner|Heclalava|blockpublictorrent|iptables|tcpdump|ping|wireguard|tools|pasarguard|nikatel|systemctl|hello|Host|Hysteria|ALPN|SpiderX|Flow|Alter|Short|Seed|Sanaei|Alireza|nodejs|node|Anthropic|OpenAI|Gemini|Grok|Claude|ChatGPT|DeepSeek|Fable|Opus|Haiku|Sonnet|Llama|Max|Plus|Turbo|GPT|GLM|Air|Sonar|Codestral|Flash|Lite|Pro|mini|nano|Kimi|Qwen|Mistral|Groq|OpenRouter|Together|Perplexity)\b"
)


def _left(translated: str) -> list[str]:
    """English words (3+ letters) that are still in a translated text after the technical names, numbers and the stand-in values are taken out."""
    t = re.sub(r"https?://\S+|[\w.+-]+@[\w.-]+|[\w-]+(?:\.[\w-]+)+|[\w:/-]*_[\w:/-]*|\b[a-z][a-z0-9]*-[a-z0-9-]+\b|\{[^}]*\}|`[^`]*`|\$[A-Z_]+|--?[a-z]+", " ", translated.replace("alpha", " "))
    return re.findall(r"[A-Za-z]{3,}", TECH.sub(" ", re.sub(r"\d+", " ", t)))


def _fill(text: str) -> str:
    """`${x}` of a template literal -> what it may be: the longest string of a condition (`${n === 1 ? " has" : "s have"}` -> "s have"), else a number."""
    def one(m: re.Match) -> str:
        lits = [a or b for a, b in re.findall(r"""(?:"([^"]*)"|'([^']*)')""", m.group(0)) if a or b]
        return max(lits, key=len) if lits else "7"

    return re.sub(r"\$\{[^{}]*\}", one, text)

# ---------------------------------------------------------------------------------------------- the engine
@SKIP_NODE
def test_a_count_a_plural_ending_and_a_name_with_a_remark_do_not_swallow_other_text():
    """`{} server{}` was a template whose lazy holes took every text that has "server" in it and threw the plural away ("... these two servers today" lost its end); `{s}` is only a plural ending, `{n}` only a number."""
    out = _translate(["3 servers", "1 server", "2 tunnels down", "1 tunnel down", "3 users match this choice. Nothing has been changed yet.", "these two servers today", "the server is up", "username", "user name", "Edit template",
                      "3 templates", "1 template", "Edit group", "2 groups · 5 inbounds", "Automatic · Best of the day — the type that connects best between these two servers today", "ae-edge-1 — 203.0.113.71 (offline)"])
    assert out[:2] == ["3 سرور", "1 سرور"] and out[2] == "2 تانل قطع" and out[3] == "1 تانل قطع"
    assert out[4].startswith("3 کاربر با این انتخاب") and out[5] == "these two servers today" and out[6] == "the server is up" and out[7] == "username"
    assert out[8] == "نام کاربر" and out[9] == "ویرایش قالب" and out[10] == "3 قالب" and out[11] == "1 قالب" and out[12] == "ویرایش گروه" and out[13] == "2 گروه · 5 اینباند"
    # the options of a drop-down: "name · type - remark": each side by itself
    assert out[14] == "خودکار · بهترین نوع امروز — نوعی که امروز بین این دو سرور بهتر از همه وصل می‌شود"
    assert out[15] == "ae-edge-1 — 203.0.113.71 (آفلاین)"


def test_the_engine_knows_the_plural_hole_the_number_hole_and_the_dash_split():
    src = (WEB / "i18n.js").read_text(encoding="utf-8")
    assert '.replace(/\\{s\\}/g, "(s|es|ies|y|)(?![a-z])")' in src and '.replace(/\\{n\\}/g, "(\\\\d[\\\\d.,]*)")' in src
    assert 'if (c.includes(" — "))' in src      # "name — remark"
    assert r"\d ?[smhdw]\b" in src      # a text of one letter after a number (5m, 3h, 2d) is a text too


@SKIP_NODE
def test_times_and_counts_are_said_in_persian_and_a_word_that_ends_alike_is_left_alone():
    out = _translate(["5s", "5m", "3h", "2d", "5m ago", "2 h 3 min ago", "3h ago", "up 3d", "for 5 min", "every 2 h", "~5 min", "just now", "1 tunnel", "3 of 5 configs", "No cut in 7 days", "7 days left", "Most users"])
    assert out[:4] == ["5 ثانیه", "5 دقیقه", "3 ساعت", "2 روز"]
    assert out[4] == "5 دقیقه پیش" and out[5] == "2 ساعت و 3 دقیقه پیش" and out[6] == "3 ساعت پیش" and out[7] == "بالا 3 روز" and out[8] == "به مدت 5 دقیقه" and out[9] == "هر 2 ساعت" and out[10] == "حدود 5 دقیقه"
    assert out[11] == "همین حالا" and out[12] == "1 تانل" and out[13] == "3 از 5 کانفیگ"
    assert all(re.search(r"[\u0600-\u06FF]", t) for t in out[14:16]), out[14:16]
    # a text that merely ends in the same word is not a count
    assert _translate(["Demo panel", "My account", "Open the front door", "Remove everything", "Mbit/s"]) == ["Demo panel", "My account", "Open the front door", "Remove everything", "Mbit/s"]


# ---------------------------------------------------------------------------------------------- the pages
def _unescape(s: str) -> str:
    """The value of the text of a JS string literal: backslash-n is a line break, a doubled backslash one backslash, a backslash before a quote the quote."""
    return re.sub(r"\\(.)", lambda m: {"n": "\n", "t": "\t"}.get(m.group(1), m.group(1)), s)


def _literals(src: str, callee: str) -> list[str]:
    """The text of every callee("...") / callee(`...`) of a source whose first argument is one string (a template literal with `${}` keeps a stand-in value)."""
    out = []

    for m in re.finditer(callee + r"""\(\s*(?:"((?:[^"\\\n]|\\.)*)"|'((?:[^'\\\n]|\\.)*)'|`((?:[^`\\]|\\.)*)`)\s*[,)]""", src):
        t = m.group(1) if m.group(1) is not None else m.group(2) if m.group(2) is not None else m.group(3)

        if t.count("${") != len(re.findall(r"\$\{[^{}]*\}", t)):      # a nested template: not a plain text
            continue

        out.append(_fill(_unescape(t)))

    return out


# toasts and questions that carry only names and code: nothing to translate
NOT_TEXT = re.compile(r"^(?:[\W\d_]*[A-Za-z]{0,3}[\W\d_]*|[\w./:#-]+)$")


@SKIP_NODE
def test_every_toast_and_question_written_in_the_pages_has_persian():
    """toast("..."), gconfirm("..."), gprompt("..."), glassAsk("...") of every page: a new text without Persian fails here."""
    texts: dict[str, str] = {}

    for f in sorted(WEB.glob("*.js")):
        if f.name.startswith("i18n"):
            continue

        src = f.read_text(encoding="utf-8")

        for callee in ("toast", "gconfirm", "gprompt", "glassAsk", "alertBox"):
            for t in _literals(src, callee):
                t = t.strip()

                if len(t) > 2 and re.search(r"[A-Za-z]{3,}", t) and not NOT_TEXT.match(t):
                    texts.setdefault(t, f.name)

    assert len(texts) > 150, "the scan found too few texts: the pattern is wrong"
    out = _translate(list(texts))
    same = [f"{texts[s]}: {s[:90]!r}" for s, t in zip(texts, out, strict=True) if s == t]
    assert not same, "\n".join(same)


@SKIP_NODE
def test_the_catalog_of_tunnel_types_the_server_sends_is_in_persian():
    """The Create tunnel drop-down is built from /api/tunnels/kinds (labels, notes) and BK_FOR of app.js: every note and label of it has Persian words (a name of an engine or a protocol stays)."""
    app = (WEB / "app.js").read_text(encoding="utf-8")
    block = app[app.index("const BK_FOR = {"):app.index("};", app.index("const BK_FOR = {"))]
    notes = re.findall(r'"[a-z_:]+": "([^"]+)"', block)
    assert len(notes) > 35
    labels = ["TCP Mux · GRE over UDP (FOU)", "TCP Mux · GRE over UDP (GUE)", "TCP Mux · IPIP over UDP (FOU)", "TCP Mux · IPIP over UDP (GUE)", "TCP Mux · IPsec in UDP (NAT-T)",
              "TCP Mux · WireGuard in Chisel (foreign dials)", "TCP Mux · WireGuard in Chisel (Iran dials)", "GRE over UDP (FOU)", "IPsec (ESP in UDP, NAT-T)", "SNI spoofing", "IP spoofing",
              "Backhaul over another link", "Backhaul · TCP Mux over GRE-in-UDP (FOU)", "Backhaul · TCP Mux over IPsec in UDP (NAT-T)"]
    api = (ROOT / "nikatel" / "api" / "tunnels.py").read_text(encoding="utf-8")
    api_notes = re.findall(r'\("[a-z:]+", "[^"]+", "([^"]{20,})"\)', api)
    assert len(api_notes) >= 8
    out = _translate(notes + labels + api_notes)
    bad = [f"{s[:80]!r} -> {t[:80]!r}" for s, t in zip(notes + labels + api_notes, out, strict=True) if s == t or _left(t)]
    assert not bad, "\n".join(bad)


@SKIP_NODE
def test_the_texts_of_the_last_versions_are_in_persian():
    """1.69.36 - 1.69.49: a changed DNS record, Rebooting, bandwidth of the server, the question box, WireGuard installed / missing / not tested, the carried link, Best of the day, the real use of the users."""
    shown = ["Rebooting", "Reboot requested", "Best of the day", "Real use by the users", "at the peak", "wireguard-tools is not installed", "⬇ Put on 2 servers", "installed", "not tested", "missing",
             "Update from GitHub?", "The test moved no data at all although the checks of the tunnel pass: the data path is stalled - the services run and nothing flows under load. A restart of the tunnel, or another tunnel type for this pair, is the way.",
             "WireGuard in Chisel (Iran dials)", "the same, but the Iran server opens the TCP connection (use it when the foreign address is not filtered from Iran)", "★ Best of the day: 7", "ae1-north1: down", "the foreign side does not answer", "handed over by you", "taken back by you",
             "Smooth: 4K video, big downloads and calls run without waiting.", "Very slow: almost nothing gets through.", "no data comes back", "is not listening on the Iran server"]
    out = _translate(shown)
    stayed = [s for s, t in zip(shown, out, strict=True) if s == t and s not in ("Update from GitHub?",)]
    assert not stayed, stayed


# ---------------------------------------------------------------------------------------------- the server
def _server_texts() -> dict[str, str]:
    """The plain-constant messages the server raises for a person: `raise HTTPException(code, "...")`, `raise ValueError("...")`, `_say(id, "...")`, `emit(..., "...")` (an f-string keeps a stand-in for each value)."""
    found: dict[str, str] = {}
    EXC = {"HTTPException", "ValueError", "RuntimeError", "PermissionError", "SSHError"}
    CALLS = {"emit", "_say", "say", "_note", "note"}

    def text(node):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            return node.value

        if isinstance(node, ast.JoinedStr):
            out = ""

            for v in node.values:
                if isinstance(v, ast.Constant):
                    out += str(v.value)
                elif isinstance(v.value, ast.IfExp) and isinstance(v.value.body, ast.Constant) and isinstance(v.value.body.value, str):      # {"enabled" if on else "disabled"}
                    out += v.value.body.value
                else:      # a value glued to a letter is a plural ending; any other is a number
                    out += "s" if out[-1:].isalpha() and out[-1:].isascii() else "7"

            return out

        return None

    for p in sorted((ROOT / "nikatel").rglob("*.py")):
        if "tests" in p.parts:
            continue

        tree = ast.parse(p.read_text(encoding="utf-8"))

        for n in ast.walk(tree):
            args: list = []

            if isinstance(n, ast.Raise) and isinstance(n.exc, ast.Call):
                f = n.exc.func
                name = f.id if isinstance(f, ast.Name) else f.attr if isinstance(f, ast.Attribute) else ""

                if name == "HTTPException":
                    args = n.exc.args[1:2] + [k.value for k in n.exc.keywords if k.arg == "detail"]
                elif name in EXC:
                    args = n.exc.args[:1]
            elif isinstance(n, ast.Call):
                f = n.func
                name = f.id if isinstance(f, ast.Name) else f.attr if isinstance(f, ast.Attribute) else ""

                if name in CALLS:
                    args = list(n.args)

            for a in args:
                t = text(a)

                if t and len(t.split()) >= 3 and re.search(r"[A-Za-z]{3,}", t) and not re.search(r"[\\$|<>=;]|https?://|\.py\b", t):
                    found.setdefault(t.strip(), f"{p.relative_to(ROOT)}:{n.lineno}")

    return found


# technical-only or API-only messages (an argument of a command, a field name for an API client): not read by a person in the panel
SERVER_ALLOW = re.compile(r"^(?:Answer with the single word|You help the owner)")


@SKIP_NODE
def test_every_plain_message_of_the_server_has_persian():
    texts = {t: w for t, w in _server_texts().items() if not SERVER_ALLOW.match(t)}
    assert len(texts) > 800, "the scan found too few messages: the pattern is wrong"
    out = _translate(list(texts))
    same = [f"{texts[s]}: {s[:100]!r}" for s, t in zip(texts, out, strict=True) if s == t]
    assert not same, f"{len(same)} messages without Persian:\n" + "\n".join(same[:40])


@SKIP_NODE
def test_the_messages_of_the_server_that_are_built_from_pieces_are_in_persian():
    """The doctor's stories, causes and the undo labels are f-strings and catalogs (tuples, dicts) of the services: a sample of each family."""
    shown = ["Server ir-north-2 is busy (CPU 95 %): everything on it is slow, not only this tunnel.", "The line loses 12 % of its packets: the route got worse.",
             "The delay rose from 40 to 90 ms: the route changed or is congested.", "Only this tunnel is slower (12 Mb/s, usually 40): the servers are idle, the line loses nothing and its delay is as usual. A restart of the tunnel, or another tunnel type for this pair, may bring the speed back.",
             "before changing group web", "before deleting template t1", "client template “x” deleted (saved as b1)", "API key 3 renewed: its old secret no longer works", "all the 5 users of admin sam switched off (9 users in all)",
             "The panel's own database was busy or its schema is wrong. A restart clears a lock; a schema error is a bug.", "A server is short of memory, disk or CPU, so everything on it is slow.",
             "the standby is being built", "the standby has not been up for 90 s: it is built again", "Telegram is not configured (token and chat id)", "Too many requests - slow down a little.", "Not available in the demo.",
             "port: Field required", "port: Input should be greater than or equal to 1", "Last logs", "Listening ports", "Failed services", "Busiest processes", "Network and ports", "Who is logged in", "Tunnel units",
             "GPT-5 mini · fast and cheap", "Sonar · searches the web", "Your own (OpenAI-compatible)", "Mehr 1405", "Esfand 1404"]
    out = _translate(shown)
    stayed = [s for s, t in zip(shown, out, strict=True) if s == t]
    assert not stayed, stayed
    assert out[-2] == "مهر 1405" and out[-1] == "اسفند 1404"


# ---------------------------------------------------------------------------------------------- the index page
@SKIP_NODE
def test_the_login_page_is_in_persian():
    """index.html is not a script: its texts (the recovery hint, the Caps Lock note, the footer) were outside every scan."""
    html = (WEB / "index.html").read_text(encoding="utf-8")
    html = re.sub(r"<(script|style)[^>]*>.*?</\1>", "", html, flags=re.DOTALL)
    texts = {t.strip() for t in re.findall(r">([^<>]+)<", html) if re.search(r"[A-Za-z]{3,}", t)}
    texts |= {t.strip() for t in re.findall(r'\b(?:title|placeholder|aria-label)="([^"]+)"', html) if re.search(r"[A-Za-z]{3,}", t)}
    texts -= {"NIKATEL", "Nikatel", "Language", "123456", "Telegram code or recovery code"}
    out = _translate(sorted(texts))
    same = [s for s, t in zip(sorted(texts), out, strict=True) if s == t]
    assert not same, same


# ---------------------------------------------------------------------------------------------- the dictionaries
@SKIP_NODE
def test_the_dictionaries_keep_one_spelling_per_word():
    d = _dictionaries()
    values = [(f, k, v) for f, rows in d.items() for k, v in rows if isinstance(v, str)]
    assert len(values) > 5000
    # the minority spelling of a word that has a dominant one (counted by the audit: the right-hand word is used 3x to 100x more)
    minority = {"بکاپ": "پشتیبان", "ریستارت": "ری‌استارت", "راه‌اندازی مجدد": "ری‌استارت", "پیکربندی": "کانفیگ", "نشانی": "آدرس", "پکت‌لاس": "اتلاف بسته", "بازگردانی": "بازیابی",
                "جست‌وجو": "جستجو", "ریپیر هوشمند": "دکتر تانل", "فیل‌اور": "جابه‌جایی خودکار", "پروایدر": "ارائه‌دهنده", "سرویس‌دهنده": "ارائه‌دهنده", "مجوز": "لایسنس", "تونل": "تانل",
                "رمز SSH": "گذرواژه‌ی SSH", "درگاه آزمایشی": "درگاه تست", "واحدهای": "سرویس‌های"}
    bad = [f"{f[9:-3]}: {k[:50]!r} has {w!r} (use {r!r})" for f, k, v in values for w, r in minority.items() if w in v]
    assert not bad, "\n".join(bad[:30])
    # a word needs its half space; a kasra / hamza above is not written (tanwin of "مثلاً" is); yeh and kaf are the Persian ones
    for f, k, v in values:
        assert not re.search("[\u064a\u0643\u0650\u0654\u06c0]", v), (f, k[:60], v[:60])
        assert not re.search(r"(?<![\u0600-\u06ff\u200c])(?:نمی|می) [\u0600-\u06ff]", v), (f, k[:60], v[:60])


@SKIP_NODE
def test_the_holes_of_a_text_and_of_its_persian_fit():
    """A template whose Persian drops or invents a hole prints a hole or loses a name; a hole that is a plural ending is {s}, a count is {n}."""
    d = _dictionaries()
    bad = []

    for f, rows in d.items():
        for k, v in rows:
            if not isinstance(v, str) or "{" not in k:
                continue

            hk = re.findall(r"\{([snw]?)(\d*)\}", k)      # (kind, "")
            hv = re.findall(r"\{(\d*)\}", v)
            numbered = [int(x) for x in hv if x]
            n_k = len(hk)

            if numbered:
                if len(numbered) != len(hv) or any(x < 1 or x > n_k for x in numbered):
                    bad.append((f, k[:70], v[:70]))
            elif len(hv) != n_k and "{s}" not in k and "{n}" not in k and "{w}" not in k:
                bad.append((f, k[:70], v[:70]))

    assert not bad, "\n".join(map(str, bad[:20]))
    # no template of the audit block drops its first hole after another hole (it swallowed "Edit template")
    ui2 = (WEB / "i18n.fa.ui2.js").read_text(encoding="utf-8")
    assert '"{}{} template' not in ui2 and '"{}{} group' not in ui2 and '"{} server{}"' not in ui2 and '"{} day{}"' not in ui2


def test_the_audit_block_is_in_the_dictionary_and_the_test_knows_where():
    ui2 = (WEB / "i18n.fa.ui2.js").read_text(encoding="utf-8")
    assert "/* ---- Persian audit" in ui2 and ui2.count('"') > 9000
    assert "{s}" in ui2 and "{n}" in ui2

@SKIP_NODE
def test_a_template_does_not_shadow_the_sentence_rules_and_a_compound_title_is_said_whole():
    """`{} ago{}` took "Answers over SSH, last seen 17s ago, up 9 d." before the rule for that sentence was asked (templates run before the explainers); "version 0.8.0 · checked 2h ago" was cut at "ago" by `{} ago`."""
    out = _translate(["Answers over SSH, last seen 17s ago, up 9 d. Nothing needs attention.", "version 0.8.0 · checked 2h ago", "Tunnel ae1-north1: ir-north-1 ⇄ ae-edge-1 (down)", "Tunnel ae1-north1: ir-north-1 ⇄ ae-edge-1 (unstable)", "17s ago · up 785880"])
    assert out[0] == "با SSH پاسخ می‌دهد، آخرین مشاهده 17 ثانیه پیش، بالا 9 روز. به هیچ توجهی نیاز نیست."
    assert out[1] == "نسخه‌ی 0.8.0 · 2 ساعت پیش بررسی شد"
    assert out[2] == "تانل ae1-north1: ir-north-1 ⇄ ae-edge-1 (قطع)" and out[3] == "تانل ae1-north1: ir-north-1 ⇄ ae-edge-1 (ناپایدار)"
    assert out[4].startswith("17 ثانیه پیش")
    src = (WEB / "i18n.fa.ui2.js").read_text(encoding="utf-8")
    assert '"{} ago{}"' not in src      # (the template that shadowed it)


@SKIP_NODE
def test_a_name_in_a_one_verb_template_is_a_name_and_a_sentence_that_begins_alike_is_not():
    """"Open {w}" opens a name (ir-north-1, t1, 8072); "Open the front door" is a sentence of the user and stays as it is."""
    out = _translate(["Open ir-north-1", "Remove ir-north-1", "Update t1", "Open 8072", "Open the front door", "Tick every row", "Remove everything"])
    assert out[:4] == ["باز کردن ir-north-1", "حذف ir-north-1", "به‌روزرسانی t1", "باز کردن 8072"]
    assert out[4:] == ["Open the front door", "Tick every row", "Remove everything"]


@SKIP_NODE
def test_the_pydantic_messages_and_the_jalali_months_the_server_sends_are_in_persian():
    out = _translate(["port: Field required", "port: Input should be a valid integer, unable to parse string as an integer", "name: String should have at least 3 characters", "name: String should have at least 1 character",
                      "age: Input should be less than or equal to 99", "Farvardin 1405", "Ordibehesht 1405", "Aban 1404", "Dey 1404", "Tir 1405"])
    assert out[0] == "پورت: الزامی است" or out[0].endswith("الزامی است")
    assert out[1].endswith("باید یک عدد صحیح باشد") and out[2].endswith("حداقل 3 نویسه لازم است") and out[3].endswith("حداقل 1 نویسه لازم است") and out[4].endswith("باید حداکثر 99 باشد")
    assert out[5:] == ["فروردین 1405", "اردیبهشت 1405", "آبان 1404", "دی 1404", "تیر 1405"]

"""The web files call each other by name: a button says onclick="pgAdminSort('x')" and some other line defines window.pgAdminSort. A typo in either is a button that does nothing and
no browser test of ours would notice, so the names are read from the files and matched here: every inline handler calls a function that a web file defines, and no web file defines
the same global twice (the later one would silently replace the first)."""
import re
from pathlib import Path

WEB = Path(__file__).resolve().parent.parent / "nikatel" / "web"
HANDLER = re.compile(r"""\bon(?:click|dblclick|change|input|submit|keydown|keyup|keypress|focus|blur|mousedown|mouseup|mouseover|toggle|scroll|load|error)\s*=\s*(["'])(.*?)\1""", re.DOTALL)
CALL = re.compile(r"(?<![\w$.])([A-Za-z_$][\w$]*)\s*\(")
BUILTIN = {"if", "for", "while", "switch", "return", "function", "typeof", "new", "catch", "alert", "confirm", "prompt", "event", "parseInt", "parseFloat", "Number", "String", "Boolean", "Array",
           "Object", "JSON", "Math", "Date", "setTimeout", "setInterval", "clearTimeout", "clearInterval", "encodeURIComponent", "decodeURIComponent", "isNaN", "void", "await", "async"}
DEFINE = [re.compile(p, re.MULTILINE) for p in (r"\bwindow\.([A-Za-z_$][\w$]*)\s*=(?!=)", r"\bwindow\[\s*[\"']([A-Za-z_$][\w$]*)[\"']\s*\]\s*=(?!=)", r"\bfunction\s+([A-Za-z_$][\w$]*)\s*\(",
                                        r"\b(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=")]
FILES = sorted(WEB.glob("*.js")) + [WEB / "index.html"]


def _strip_expressions(text: str) -> str:
    """The text of a template string with every ${ ... } (nested braces counted) replaced by a placeholder: the expressions hold their own quotes, which would cut an attribute short."""
    out, i = [], 0

    while i < len(text):
        if text.startswith("${", i):
            depth, j = 1, i + 2

            while j < len(text) and depth:
                depth += {"{": 1, "}": -1}.get(text[j], 0)
                j += 1

            out.append("__x__")
            i = j
        else:
            out.append(text[i])
            i += 1

    return "".join(out)


def _calls(handler: str) -> list[str]:
    """The functions an inline handler calls: no member calls (x.y()), and no text inside quotes (a selector such as 'a:not(.b)' is not a call); a name that was a ${ ... } expression is not known."""
    code = re.sub(r"'[^']*'", "''", handler)
    return [n for n in CALL.findall(code) if n != "__x__"]


def _defined() -> set[str]:
    names: set[str] = set()

    for f in FILES:
        if f.suffix == ".js":
            src = f.read_text(encoding="utf-8")

            for rx in DEFINE:
                names.update(rx.findall(src))

    return names


def test_every_inline_handler_calls_a_function_that_is_defined():
    known = _defined()
    missing: dict[str, set[str]] = {}

    for f in FILES:
        for m in HANDLER.finditer(_strip_expressions(f.read_text(encoding="utf-8"))):
            for name in _calls(m.group(2)):
                if name not in known and name not in BUILTIN:
                    missing.setdefault(name, set()).add(f.name)

    assert not missing, "handlers that call a function no web file defines: " + "; ".join(f"{n} (in {', '.join(sorted(fs))})" for n, fs in sorted(missing.items()))


def test_no_global_function_is_defined_twice_by_window_assignment():
    seen: dict[str, list[str]] = {}

    for f in FILES:
        if f.suffix != ".js":
            continue

        for name in re.findall(r"^\s*window\.([A-Za-z_$][\w$]*)\s*=(?!=)", f.read_text(encoding="utf-8"), re.MULTILINE):
            seen.setdefault(name, []).append(f.name)

    twice = {n: fs for n, fs in seen.items() if len(fs) > 1}
    assert not twice, "a global is assigned more than once (the later one replaces the earlier): " + "; ".join(f"{n}: {', '.join(fs)}" for n, fs in sorted(twice.items()))

def test_the_node_and_update_files_never_put_a_name_from_a_panel_into_a_handler():
    """`esc()` makes a quote an entity, and an attribute is decoded BEFORE its script is read: onclick="f('${esc(name)}')" with the name x');alert(1);// runs the alert. A node or a panel name comes
    from a panel, so the buttons of these files carry numbers (ids, the row of a list) and look the name up when they run."""
    bad = []

    for name in ("pg_nodes.js", "pg_srvnode.js", "pg_update.js", "lab.js"):
        for i, line in enumerate((WEB / name).read_text(encoding="utf-8").splitlines(), 1):
            if any("'${esc(" in m.group(1) for m in re.finditer(r'\bon\w+="([^"]*)"', line)):
                bad.append(f"{name}:{i}")

    assert not bad, f"a handler with a name inside a string: {bad}"

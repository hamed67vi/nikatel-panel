#!/usr/bin/env python3
"""audit_code.py [repo] - the alignment audit, read only: docs vs code, settings vs docs, routes without auth, swallowed exceptions,
blocking routes, retention of the growing tables, long functions, duplicated route paths. Prints a report; changes nothing."""
import ast
import os
import re
import sys
from collections import Counter, defaultdict

ROOT = sys.argv[1] if len(sys.argv) > 1 else os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def read(p):
    with open(os.path.join(ROOT, p), encoding="utf-8", errors="replace") as f:
        return f.read()


def files(sub, ext=".py"):
    out = []
    for d, _, fs in os.walk(os.path.join(ROOT, sub)):
        for f in fs:
            if f.endswith(ext) and not f.startswith("__"):
                out.append(os.path.relpath(os.path.join(d, f), ROOT))
    return sorted(out)


docs = "".join(read(p) for p in ["README.md", "ARCHITECTURE.md"] + files("docs", ".md"))

print("=== 1. modules the architecture docs do not mention")
for layer in ("nikatel/services", "nikatel/api"):
    missing = [os.path.basename(p)[:-3] for p in files(layer) if os.path.basename(p)[:-3] not in docs]
    print(f"  {layer}: {len(files(layer))} modules, not mentioned anywhere in the docs: {missing}")

print("=== 2. settings (config.py) the docs do not mention as NIKATEL_<NAME>")
cfg = read("nikatel/config.py")
fields = re.findall(r"^    ([a-z_]+): [^=\n]+=", cfg, flags=re.M)
print("  ", [f for f in fields if "NIKATEL_" + f.upper() not in docs])

print("=== 3. routers / routes without the sign-in dependency")
for p in files("nikatel/api"):
    s = read(p)
    if "APIRouter(" in s and "dependencies=[Depends(current_user)]" not in s:
        open_routes = re.findall(r'@router\.(get|post|put|patch|delete)\("([^"]+)"', s)
        guarded = len(re.findall(r"Depends\(current_user\)|Depends\(auth\.current_user\)", s))
        print(f"  {p}: router has no global guard; {len(open_routes)} routes, {guarded} per-route guards -> {[r[1] for r in open_routes][:12]}")

print("=== 4. swallowed exceptions (except Exception / bare except with no log in the next 3 lines)")
sw = []
for p in files("nikatel"):
    lines = read(p).splitlines()
    for i, l in enumerate(lines):
        if re.match(r"\s*except( Exception)?( as \w+)?:\s*(#.*)?$", l):
            nxt = "\n".join(lines[i + 1:i + 4])
            if not re.search(r"log\.|logger\.|raise|_say\(|emit\(", nxt):
                sw.append(f"{p}:{i + 1}")
print(f"  {len(sw)}: {sw[:40]}")

print("=== 5. API routes that talk to a server synchronously (run_script / run_action / probe / check inside the route module)")
for p in files("nikatel/api"):
    s = read(p)
    hits = re.findall(r"\b(run_script|run_action|\.probe\(|\.scan_server\(|\.check\(|check_now|\.users\(|\.hosts\(|\.query\()", s)
    if hits:
        print(f"  {p}: {dict(Counter(hits))}")

print("=== 6. retention: which growing tables are ever purged")
svc = "".join(read(p) for p in files("nikatel"))
for t in ("ServerSample", "TunnelCheck", "ConfigSample", "Event", "ActionLog", "RepairCase"):
    n = len(re.findall(r"delete\(" + t + r"\)|db\.delete\(.*" + t, svc))
    print(f"  {t}: purge statements = {n}")

print("=== 7. long functions (> 80 lines) per file, and files over 800 lines")
for p in files("nikatel"):
    src = read(p)
    try:
        tree = ast.parse(src)
    except SyntaxError as e:
        print("  SYNTAX", p, e)
        continue
    longf = [(n.name, n.end_lineno - n.lineno) for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.end_lineno - n.lineno > 80]
    nl = src.count("\n")
    if longf or nl > 800:
        print(f"  {p} ({nl} lines): {sorted(longf, key=lambda x: -x[1])[:6]}")

print("=== 8. duplicated route (method, path)")
seen = defaultdict(list)
for p in files("nikatel/api"):
    for m, path in re.findall(r'@router\.(get|post|put|patch|delete)\("([^"]+)"', read(p)):
        seen[(m, path)].append(p)
print("  ", {k: v for k, v in seen.items() if len(v) > 1} or "none")

print("=== 9. TODO / FIXME / XXX / HACK")
todo = [(p, i + 1, l.strip()[:90]) for p in files("nikatel") + files("tests") for i, l in enumerate(read(p).splitlines()) if re.search(r"\b(TODO|FIXME|XXX|HACK)\b", l)]
print(f"  {len(todo)}: {todo[:15]}")

print("=== 10. threads started outside the jobs (threading.Thread) and global caches without a lock")
for p in files("nikatel"):
    s = read(p)
    th = len(re.findall(r"threading\.Thread\(", s))
    caches = re.findall(r"^_[A-Z_]+\s*(?::\s*dict[^=]*)?=\s*\{\}", s, flags=re.M)
    locks = "threading.Lock()" in s
    if th or (caches and not locks):
        print(f"  {p}: threads={th} module caches={len(caches)} lock={'yes' if locks else 'no'}")

print("=== 11. SSH scripts / actions declared and their use count (unused = dead)")
ssh = read("nikatel/ssh.py")
names = re.findall(r'^    "([a-z_0-9]+)": \(r"""', ssh, flags=re.M)
rest = "".join(read(p) for p in files("nikatel") if not p.endswith("ssh.py"))
unused = [n for n in names if f'"{n}"' not in rest and f"'{n}'" not in rest]
print(f"  {len(names)} declared, unused: {unused}")

print("=== 12. front-end: files, sizes, inline onclick handlers, functions defined twice")
for p in files("nikatel/web", ".js"):
    s = read(p)
    names = re.findall(r"^(?:function|window\.)\s*([A-Za-z_$][\w$]*)", s, flags=re.M) + re.findall(r"^window\.([A-Za-z_$][\w$]*)\s*=", s, flags=re.M)
    dup = [k for k, v in Counter(names).items() if v > 1]
    print(f"  {p}: {s.count(chr(10))} lines, onclick= {s.count('onclick=')}, api( {s.count('api(')}, twice-defined: {dup[:10]}")

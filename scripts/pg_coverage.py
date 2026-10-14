"""pg_coverage.py <openapi.json> [--log FILE] [pytest args...] - which routes of a PasarGuard panel can this panel send, and which does no test exercise?

It runs the test suite with a recorder on the calls to a panel (every fake of `nikatel.services.panels.call` that a test installs is wrapped, and each METHOD and path it receives is
appended to a file), then matches the recorded paths with the OpenAPI document of a live panel (`GET /openapi.json`) and lists the routes that were never sent. A route in that list is
either left out on purpose (docs/pasarguard-coverage.md: the by-username duplicates, the owner setup) or a gap: a button that is missing.

    python scripts/pg_coverage.py openapi.json                    # runs the whole suite (about 20 minutes), then prints the report
    python scripts/pg_coverage.py openapi.json --log run.jsonl    # no run: matches the log of an earlier run

The recorder is this same file, loaded as a pytest plugin (`-p pg_coverage`); it changes nothing in the code under test and writes only to the log. The tests have to run against a
throw-away copy - nothing here talks to a panel."""
import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
from collections import defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
LOG_ENV = "NK_PATHLOG"
METHODS = ("get", "post", "put", "delete", "patch")
WRAPPED = ("services.panels.call", "services.panels.call_raw")      # the two ways the code asks a panel: JSON (`call`) and a body that is not JSON (`call_raw`, the subscription)


# ------------------------------------------------------------------ the recorder (a pytest plugin)
def pytest_configure(config) -> None:
    log = os.environ.get(LOG_ENV)

    if not log:
        return

    import pytest

    orig = pytest.MonkeyPatch.setattr

    def recording(fn):
        def wrapped(c, method, path, *a, **k):
            try:
                with open(log, "a", encoding="utf-8") as f:
                    f.write(json.dumps([str(method).upper(), str(path).split("?")[0]]) + "\n")
            except OSError:
                pass                                                                  # a log that cannot be written must not fail a test

            return fn(c, method, path, *a, **k)

        return wrapped

    def setattr_(self, target, name, *args, **kw):
        if isinstance(target, str) and target.endswith(WRAPPED) and callable(name):          # setattr("nikatel.services.panels.call", fake)
            return orig(self, target, recording(name), *args, **kw)

        if name in ("call", "call_raw") and getattr(target, "__name__", "").endswith("services.panels") and args and callable(args[0]):      # setattr(panels, "call", fake)
            return orig(self, target, name, recording(args[0]), *args[1:], **kw)

        return orig(self, target, name, *args, **kw)

    pytest.MonkeyPatch.setattr = setattr_


# ------------------------------------------------------------------ the report
def sent(log: Path) -> dict[str, set[str]]:
    """{METHOD: {path}} from the log (a line is [method, path] or [method, path, anything]); a trailing slash does not matter."""
    seen: dict[str, set[str]] = defaultdict(set)

    for line in log.read_text(encoding="utf-8").splitlines():
        try:
            row = json.loads(line)
        except ValueError:
            continue

        if isinstance(row, list) and len(row) >= 2:
            seen[str(row[0])].add(str(row[1]).rstrip("/") or "/")

    return seen


def match(spec: dict, seen: dict[str, set[str]]) -> list[tuple[str, str, bool, str]]:
    """[(METHOD, path of the OpenAPI document, was it sent, its first tag)]: `{id}` in the document stands for any one segment of a sent path."""
    rows = []

    for path, ops in sorted(spec.get("paths", {}).items()):
        rx = re.compile("^" + re.sub(r"\\\{[^/]*?\\\}", "[^/]+", re.escape(path.rstrip("/") or "/")) + "$")

        for method, op in ops.items():
            if method in METHODS:
                rows.append((method.upper(), path, any(rx.match(p) for p in seen.get(method.upper(), ())), (op.get("tags") or [""])[0]))

    return rows


def report(spec_file: Path, log: Path) -> int:
    rows = match(json.loads(spec_file.read_text(encoding="utf-8")), sent(log))
    miss = [r for r in rows if not r[2]]
    print(f"routes in the OpenAPI document: {len(rows)} | sent by this panel's code in the tests: {len(rows) - len(miss)} | never sent: {len(miss)}")

    for method, path, _hit, tag in miss:
        print(f"   {method:6} {path}   [{tag}]")

    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("openapi", type=Path)
    ap.add_argument("--log", type=Path, help="match this log of an earlier run instead of running the tests")
    args, rest = ap.parse_known_args()

    if args.log:
        return report(args.openapi, args.log)

    log = Path(os.environ.get(LOG_ENV) or Path(tempfile.gettempdir()) / "nk_pg_paths.jsonl")
    log.unlink(missing_ok=True)
    env = dict(os.environ, **{LOG_ENV: str(log), "PYTHONPATH": os.pathsep.join(p for p in (str(HERE), os.environ.get("PYTHONPATH", "")) if p)})
    code = subprocess.call([sys.executable, "-m", "pytest", "-q", "-p", "pg_coverage", *rest], cwd=ROOT, env=env)

    if not log.exists():
        print("the tests wrote no log: nothing to match", file=sys.stderr)
        return code or 1

    report(args.openapi, log)
    return code


if __name__ == "__main__":
    sys.exit(main())

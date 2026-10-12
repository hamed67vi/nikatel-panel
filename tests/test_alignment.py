"""Alignment: the docs describe the code that exists, every setting is documented, every API router is behind the sign-in, every SSH script is used,
and the repository holds no byte-order marks or Windows line endings. These tests keep the project from drifting apart again."""
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
DOCS = "".join(p.read_text(encoding="utf-8") for p in [ROOT / "README.md", ROOT / "ARCHITECTURE.md"] + sorted((ROOT / "docs").glob("*.md")))


def _modules(sub: str) -> list[str]:
    return sorted(p.stem for p in (ROOT / "nikatel" / sub).glob("*.py") if not p.stem.startswith("__"))


def test_every_service_and_api_module_is_named_in_the_architecture_docs():
    arch = (ROOT / "ARCHITECTURE.md").read_text(encoding="utf-8")
    missing = [m for m in _modules("services") if m + ".py" not in arch] + [m for m in _modules("api") if m not in arch]
    assert not missing, f"not in ARCHITECTURE.md: {missing}"


def test_every_setting_is_documented_as_an_environment_variable():
    cfg = (ROOT / "nikatel" / "config.py").read_text(encoding="utf-8")
    fields = re.findall(r"^    ([a-z_]+): [^=\n]+=", cfg, flags=re.MULTILINE)
    ops = (ROOT / "docs" / "operations.md").read_text(encoding="utf-8")
    missing = [f for f in fields if "NIKATEL_" + f.upper() not in ops]
    assert not missing, f"not in docs/operations.md: {missing}"


def test_every_api_router_is_behind_the_sign_in():
    open_by_design = {"auth": "sign-in, sign-out, recovery", "system": "/health for the deploy script; every other route guarded one by one"}
    bad = []

    for p in sorted((ROOT / "nikatel" / "api").glob("*.py")):
        s = p.read_text(encoding="utf-8")

        if "APIRouter(" not in s or p.stem in open_by_design or p.stem.startswith("__"):
            continue

        if "dependencies=[Depends(current_user)" not in s:
            bad.append(p.stem)

    assert not bad, f"routers without the sign-in dependency: {bad}"
    sysapi = (ROOT / "nikatel" / "api" / "system.py").read_text(encoding="utf-8")
    routes = re.findall(r'@router\.(?:get|post|put|delete)\("([^"]+)"([^)]*)\)', sysapi)
    unguarded = [path for path, rest in routes if "current_user" not in rest and path != "/health"]
    assert not unguarded, f"system routes without a guard: {unguarded}"


def test_every_ssh_script_and_action_is_used():
    ssh = (ROOT / "nikatel" / "ssh.py").read_text(encoding="utf-8")
    names = re.findall(r'^    "([a-z_0-9]+)": \(r"""', ssh, flags=re.MULTILINE)
    rest = "".join(p.read_text(encoding="utf-8") for p in (ROOT / "nikatel").rglob("*.py") if p.name != "ssh.py")
    built_by_prefix = {"install_btblock", "install_marzban_node"}          # extras.py builds these names as "install_" + item
    unused = [n for n in names if f'"{n}"' not in rest and f"'{n}'" not in rest and n not in built_by_prefix]
    assert not unused, f"declared in ssh.py, used nowhere: {unused}"


@pytest.mark.parametrize("pattern", ["**/*.py", "**/*.js", "**/*.css", "**/*.html", "**/*.md", "**/*.sh", "**/*.yml", "**/*.toml"])
def test_no_byte_order_mark_and_no_windows_line_endings(pattern):
    bad = []

    for p in ROOT.glob(pattern):
        if any(part in (".git", ".venv", "node_modules", "__pycache__") for part in p.parts):
            continue

        b = p.read_bytes()

        if b.startswith(b"\xef\xbb\xbf"):
            bad.append(f"{p.relative_to(ROOT)}: BOM")
        elif b"\r\n" in b:
            bad.append(f"{p.relative_to(ROOT)}: CRLF")

    assert not bad, bad

"""The version of the code and the changelog agree (the owner, 2026-10-20: 1.69.37 was shipped with nikatel/__init__.py and CHANGELOG.md still on 1.69.36, so after the update the panel said "1.69.37 is available"
again and again). The guard is in the suite as well as in the ship script."""
import re
from pathlib import Path

from nikatel import __version__

ROOT = Path(__file__).resolve().parents[1]


def _heads():
    text = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    return [tuple(int(x) for x in m.split(".")) for m in re.findall(r"^## (\d+\.\d+\.\d+)\b", text, re.MULTILINE)]


def test_the_first_heading_of_the_changelog_is_the_version_of_the_code():
    assert re.fullmatch(r"\d+\.\d+\.\d+", __version__)
    assert ".".join(str(x) for x in _heads()[0]) == __version__


def test_the_changelog_headings_are_unique_and_newest_first():
    heads = _heads()
    assert len(heads) == len(set(heads))
    assert heads == sorted(heads, reverse=True)
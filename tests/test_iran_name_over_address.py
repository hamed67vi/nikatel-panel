"""The name of the Iran node in the tunnel list sits right above its address, on the same edge (the owner, 2026-11-03, a picture of the "Iran node" column: "here the names should go on top of the IPs, like the Foreign column").

The cell is right-aligned (toward the ping arrows) and the address followed it, but the name is a flex row (so a long name can end in an ellipsis) and a flex row ignores the cell's text-align: the name stayed at the left edge, far from its address. The foreign cell is left-aligned and its name and address already share the left edge.

What is tested: the Iran cell is the right-aligned one, its name row is pushed to the same edge as the address, and the foreign cell is left as it was."""
from pathlib import Path

WEB = Path(__file__).resolve().parents[1] / "nikatel" / "web"


def test_the_iran_name_row_ends_at_the_same_edge_as_its_address():
    css = (WEB / "lab.css").read_text(encoding="utf-8")
    app_css = (WEB / "app.css").read_text(encoding="utf-8")
    js = (WEB / "app.js").read_text(encoding="utf-8")

    assert ".tun-tbl td.td-node.ir { text-align: right; }" in app_css                       # (the address of the Iran cell is on the right)
    assert "html body #page .tun-tbl td.td-node.ir > b { justify-content: flex-end; }" in css      # (so the name is too)
    assert 'html body #page .tun-tbl td.td-node > b { display: flex;' in css                # (the name row that needs it: a flex row for the ellipsis)
    assert '<td class="td-node ir"><b title="${esc(t.iran_name)}">' in js


def test_the_foreign_name_stays_on_the_left_with_its_address():
    css = (WEB / "lab.css").read_text(encoding="utf-8")
    js = (WEB / "app.js").read_text(encoding="utf-8")

    assert "td.td-node:not(.ir) > b { justify-content" not in css and "td.td-node.foreign" not in css
    assert '<td class="td-node"><b title="${esc(t.foreign_name)}">' in js

"""The glow of a switch is not cut off by the box it sits in (the owner, 2026-11-04, with a picture of the nightly switch: "it went into a box, on the right of the button").

What is tested: the CSS - the two boxes that clip (the Tunnel cores box and the PasarGuard box) take the room the glow of a switch needs (its ring plus its blur) out of the card's own padding."""
import re
from pathlib import Path

WEB = Path(__file__).resolve().parents[1] / "nikatel" / "web"


def test_the_boxes_that_clip_leave_room_for_the_glow_of_a_switch():
    css = (WEB / "lab.css").read_text(encoding="utf-8")
    sel = r"html body #page #eng-box, html body #page \.abt-pg \.abt-cb"
    assert re.search(sel + r" \{[^}]*overflow: hidden", css), "the box still clips (that is why the list scrolls inside it)"
    room = re.search(sel + r" \{ margin-left: -(\d+)px; margin-right: -(\d+)px; margin-bottom: -(\d+)px; padding: 0 (\d+)px (\d+)px; \}", css)
    assert room, "the box gives the glow its room"
    side_l, side_r, below, pad_side, pad_below = map(int, room.groups())
    assert side_l == side_r == pad_side and below == pad_below          # (nothing moves: what the margin takes, the padding gives back)
    glow = re.search(r"\.ob2-sw\.on i, [^{]*\{[^}]*box-shadow: 0 0 0 (\d+)px rgba\(10, 124, 245, \.55\), 0 0 (\d+)px rgba\(10, 132, 255", css)
    assert glow, "the glow of the switch is where the test expects it"
    ring, blur = int(glow.group(1)), int(glow.group(2))
    assert pad_side >= ring + blur and pad_below >= blur, "the room is at least as wide as the glow"

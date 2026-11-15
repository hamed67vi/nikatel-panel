"""(1.69.278) The leftover of an L2TPv3 link is removed by its SESSION (the owner, 2026-11-15, after an interface `lp-l2tpv3` was found half-removed on an Iran server and cleaned by hand: «بله، بساز»).
A link of the L2TPv3 kind has no `ip link del`: the kernel keeps the device while its SESSION exists, so the sweep of test leftovers (`purge_test_leftovers`) and the clean-up of a link probe (`link_probe_clean`) - which
deleted the scripts and the device, and only the tunnels WITHOUT a session - left the session, the device and the firewall rules of the link for ever. Here the real shell lines of both actions run in bash with stubbed `ip`
and `iptables` that answer in the format the real ones print (taken from the leftover itself)."""
import shutil
import subprocess

import pytest

from nikatel import ssh

pytestmark = pytest.mark.skipif(shutil.which("bash") is None or shutil.which("awk") is None, reason="needs bash and awk")

SESSIONS = """Session 243569364 in tunnel 243569364
  Peer session 1317311188, tunnel 1317311188
  interface name: lp-l2tpv3
  offset 0, peer offset 0

Session 77 in tunnel 55
  Peer session 78, tunnel 56
  interface name: live-l2
  offset 0, peer offset 0

"""

IPTABLES_SAVE = """*filter
-A INPUT -i lp-l2tpv3 -m comment --comment nikatel-gre-lp-l2tpv3 -j ACCEPT
-A INPUT -i live-l2 -m comment --comment nikatel-gre-live-l2 -j ACCEPT
-A INPUT -i test-1 -m comment --comment nikatel-gre-test-1 -j ACCEPT
COMMIT
"""

S_INPUT = """-P INPUT ACCEPT
-A INPUT -i lp-l2tpv3 -m comment --comment nikatel-gre-lp-l2tpv3 -j ACCEPT
-A INPUT -i live-l2 -m comment --comment nikatel-gre-live-l2 -j ACCEPT
-A INPUT -i test-1 -m comment --comment nikatel-gre-test-1 -j ACCEPT
-A INPUT -i test-10 -m comment --comment nikatel-gre-test-10 -j ACCEPT
"""

S_FORWARD = """-P FORWARD ACCEPT
-A FORWARD -o lp-l2tpv3 -m comment --comment nikatel-gre-lp-l2tpv3 -j ACCEPT
-A FORWARD -o live-l2 -m comment --comment nikatel-gre-live-l2 -j ACCEPT
"""

S_MANGLE = """-P FORWARD ACCEPT
-A FORWARD -o lp-l2tpv3 -p tcp -m tcp --tcp-flags SYN,RST SYN -m comment --comment nikatel-gre-lp-l2tpv3 -j TCPMSS --clamp-mss-to-pmtu
-A FORWARD -o live-l2 -p tcp -m tcp --tcp-flags SYN,RST SYN -m comment --comment nikatel-gre-live-l2 -j TCPMSS --clamp-mss-to-pmtu
"""

STUB_IP = r"""#!/bin/bash
echo "ip $*" >> "$LOG"
if [ "$1 $2 $3" = "l2tp show session" ]; then
  awk -v RS= -v ORS='\n\n' -v want="${5:-}" -v dir="$DIR" '{ split($0, l, "\n"); split(l[1], h, " "); tid=h[5]; if ((want == "" || want == tid) && system("[ ! -e " dir "/gone-" tid "-" h[2] " ]") == 0) print $0 }' "$DIR/sessions"
  exit 0
fi
if [ "$1 $2 $3" = "l2tp del session" ]; then touch "$DIR/gone-$5-$7"; fi
exit 0
"""

STUB_IPTABLES = r"""#!/bin/bash
[ "$1" = "-w" ] && shift 2
echo "iptables $*" >> "$LOG"
case "$*" in
  "-S INPUT") cat "$DIR/S-INPUT";;
  "-S FORWARD") cat "$DIR/S-FORWARD";;
  "-t mangle -S FORWARD") cat "$DIR/S-MANGLE";;
esac
exit 0
"""

STUB_SAVE = "#!/bin/bash\ncat \"$DIR/save\"\n"


def _world(tmp_path):
    (tmp_path / "bin").mkdir()

    for name, text in (("ip", STUB_IP), ("iptables", STUB_IPTABLES), ("iptables-save", STUB_SAVE)):
        p = tmp_path / "bin" / name
        p.write_text(text)
        p.chmod(0o755)

    for name, text in (("sessions", SESSIONS), ("save", IPTABLES_SAVE), ("S-INPUT", S_INPUT), ("S-FORWARD", S_FORWARD), ("S-MANGLE", S_MANGLE)):
        (tmp_path / name).write_text(text)

    (tmp_path / "log").write_text("")
    return tmp_path


def _run(tmp_path, lines: list[str], head: str = "") -> list[str]:
    w = _world(tmp_path)
    env = {"PATH": f"{w / 'bin'}:/usr/bin:/bin", "LOG": str(w / "log"), "DIR": str(w)}
    r = subprocess.run(["bash", "-c", head + "\n".join(lines)], env=env, capture_output=True, text=True, check=False, timeout=30)
    assert r.returncode == 0, r.stderr
    return (w / "log").read_text().splitlines()


def _lines(script: str, *starts: str) -> list[str]:
    out = [ln for ln in script.splitlines() if ln.startswith(starts)]
    assert len(out) == len(starts), (starts, out)
    return out


def test_the_sweep_deletes_the_session_of_a_leftover_link_and_never_that_of_a_live_one(tmp_path):
    script = ssh.render_action("purge_test_leftovers", {"TESTPORT": "39400"})
    log = _run(tmp_path, _lines(script, "ip l2tp show session", "for tag in"))
    dels = [ln for ln in log if ln.startswith("ip l2tp del")]
    assert dels == ["ip l2tp del session tunnel_id 243569364 session_id 243569364"]                           # (the leftover lp-l2tpv3 only: the session of `live-l2` is not named test-* / lp-*)


def test_the_sweep_takes_the_firewall_rules_of_a_leftover_link_by_their_exact_tag(tmp_path):
    script = ssh.render_action("purge_test_leftovers", {"TESTPORT": "39400"})
    log = _run(tmp_path, _lines(script, "ip l2tp show session", "for tag in"))
    removed = [ln for ln in log if ln.startswith("iptables") and " -D " in ln]
    assert "iptables -D INPUT -i lp-l2tpv3 -m comment --comment nikatel-gre-lp-l2tpv3 -j ACCEPT" in removed
    assert "iptables -D FORWARD -o lp-l2tpv3 -m comment --comment nikatel-gre-lp-l2tpv3 -j ACCEPT" in removed
    assert "iptables -t mangle -D FORWARD -o lp-l2tpv3 -p tcp -m tcp --tcp-flags SYN,RST SYN -m comment --comment nikatel-gre-lp-l2tpv3 -j TCPMSS --clamp-mss-to-pmtu" in removed
    assert "iptables -D INPUT -i test-1 -m comment --comment nikatel-gre-test-1 -j ACCEPT" in removed        # (a test link's rule goes too ...)
    assert not any("test-10" in ln or "live-l2" in ln for ln in removed)                                     # (... test-10 only shares a prefix with test-1; the live link's rules stay)
    assert len(removed) == 4


def test_the_clean_up_of_a_probe_without_its_down_script_deletes_the_session_then_the_empty_tunnel(tmp_path):
    script = ssh.render_action("link_probe_clean", {"NAME": "lp-l2tpv3"})
    log = _run(tmp_path, _lines(script, "ip l2tp show session", 'TAG="nikatel-gre-$n"'), head="n='lp-l2tpv3'\n")
    dels = [ln for ln in log if ln.startswith("ip l2tp del")]
    assert dels == ["ip l2tp del session tunnel_id 243569364 session_id 243569364", "ip l2tp del tunnel tunnel_id 243569364"]          # (the session first; the tunnel only when no session is left in it)
    removed = [ln for ln in log if ln.startswith("iptables") and " -D " in ln]
    assert len(removed) == 3 and all("nikatel-gre-lp-l2tpv3" in ln for ln in removed)                         # (its own rules only)


def test_the_clean_up_of_a_probe_leaves_the_tunnel_that_still_holds_another_session(tmp_path):
    (tmp_path / "x").mkdir()
    script = ssh.render_action("link_probe_clean", {"NAME": "lp-l2tpv3"})
    w = _world(tmp_path / "x")
    (w / "sessions").write_text(SESSIONS.replace("Session 77 in tunnel 55", "Session 77 in tunnel 243569364").replace("Peer session 78, tunnel 56", "Peer session 78, tunnel 1317311188"))
    env = {"PATH": f"{w / 'bin'}:/usr/bin:/bin", "LOG": str(w / "log"), "DIR": str(w)}
    r = subprocess.run(["bash", "-c", "n='lp-l2tpv3'\n" + "\n".join(_lines(script, "ip l2tp show session"))], env=env, capture_output=True, text=True, check=False, timeout=30)
    assert r.returncode == 0, r.stderr
    dels = [ln for ln in (w / "log").read_text().splitlines() if ln.startswith("ip l2tp del")]
    assert dels == ["ip l2tp del session tunnel_id 243569364 session_id 243569364"]                           # (another session still lives in that tunnel: the tunnel is not deleted)


def test_a_name_that_is_not_a_probe_or_a_test_is_still_refused():
    with pytest.raises(ValueError):
        ssh.render_action("link_probe_clean", {"NAME": "live-l2"})

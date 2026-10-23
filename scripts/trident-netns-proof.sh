#!/bin/bash
# Proof of the TRIDENT chain (nikatel/ssh.py trident_set / trident_off / trident_list) on a REAL kernel, in network namespaces only (nothing of the host is touched):
#   wv_cli (users, 10.7.1.2)  <->  wv_ir (the "Iran server", 10.7.1.1) with three listeners: T = the tunnel on 443, A = strand A on 20443, B = strand B on 30443
#   the Iran server has a CLOSED firewall (INPUT policy DROP: established, 443, 22 only) - what was redirected must be let in by the trident's own accepts
# usage: bash scripts/trident-netns-proof.sh <tree> [nft|legacy]     (tree = a copy of the panel with the trident actions; needs root, ip netns, python3 and the panel's venv at /opt/nikatel/venv; touches nothing of the host)
set -u
TREE="${1:?tree}"; VARIANT="${2:-nft}"
PYV=/opt/nikatel/venv/bin/python
W=/tmp/wvproof; rm -rf "$W"; mkdir -p "$W/bin"
ok=0; bad=0
chk() { if [ "$1" = ok ]; then ok=$((ok+1)); echo "  ok   $2"; else bad=$((bad+1)); echo "  FAIL $2"; fi; }
if [ "$VARIANT" = legacy ]; then
  for t in iptables iptables-restore iptables-save; do ln -sf "$(command -v $t-legacy 2>/dev/null || command -v $t)" "$W/bin/$t"; done
  [ -x /usr/sbin/iptables-legacy ] && ln -sf /usr/sbin/iptables-legacy-restore "$W/bin/iptables-restore" && ln -sf /usr/sbin/iptables-legacy-save "$W/bin/iptables-save"
  modprobe iptable_nat 2>/dev/null; modprobe xt_statistic 2>/dev/null
fi
export PATH="$W/bin:$PATH"
echo "== variant: $VARIANT ($(iptables -V))"
cleanup() { for n in wv_cli wv_ir; do ip netns pids $n 2>/dev/null | xargs -r kill 2>/dev/null; ip netns del $n 2>/dev/null; done; }
trap cleanup EXIT; cleanup
for n in wv_cli wv_ir; do ip netns add $n; ip netns exec $n ip link set lo up; done
ip link add wvc type veth peer name wvi; ip link set wvc netns wv_cli; ip link set wvi netns wv_ir
ip netns exec wv_cli ip addr add 10.7.1.2/24 dev wvc; ip netns exec wv_cli ip link set wvc up
ip netns exec wv_ir ip addr add 10.7.1.1/24 dev wvi; ip netns exec wv_ir ip link set wvi up
IR() { ip netns exec wv_ir "$@"; }
CLI() { ip netns exec wv_cli "$@"; }

# the closed firewall of the Iran server
IR iptables -P INPUT DROP
IR iptables -A INPUT -m conntrack --ctstate ESTABLISHED,RELATED -j ACCEPT
IR iptables -A INPUT -i lo -j ACCEPT
IR iptables -A INPUT -p tcp --dport 443 -j ACCEPT
IR iptables -A INPUT -p tcp --dport 22 -j ACCEPT
# somebody else's NAT rule that must survive everything
IR iptables -t nat -A PREROUTING -p tcp --dport 9999 -j REDIRECT --to-ports 9998

cat > $W/srv.py <<'EOF'
import socket, sys, threading
port, label = int(sys.argv[1]), sys.argv[2]
s = socket.socket(); s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1); s.bind(("0.0.0.0", port)); s.listen(512)
def one(c):
    try:
        c.sendall((label + "\n").encode())
        f = c.makefile("rb")
        for line in f:
            if line.strip() == b"ping":
                c.sendall(("pong-" + label + "\n").encode())
    except OSError:
        pass
    finally:
        c.close()
while True:
    c, _ = s.accept()
    threading.Thread(target=one, args=(c,), daemon=True).start()
EOF
cat > $W/cli.py <<'EOF'
# cli.py many N [port] [sleep]  -> "T=.. A=.. B=.. FAIL=.."     (N short connections, each reads its label)
# cli.py hold N [port]          -> opens N long connections, prints their labels, waits for a line on stdin, pings each, prints "alive=k of N"
import socket, sys, time, collections
mode, n = sys.argv[1], int(sys.argv[2])
port = int(sys.argv[3]) if len(sys.argv) > 3 else 443
if mode == "many":
    gap = float(sys.argv[4]) if len(sys.argv) > 4 else 0
    cnt = collections.Counter()
    for i in range(n):
        try:
            c = socket.create_connection(("10.7.1.1", port), timeout=2)
            cnt[c.makefile("rb").readline().strip().decode() or "EMPTY"] += 1
            c.close()
        except OSError:
            cnt["FAIL"] += 1
        if gap: time.sleep(gap)
    print(" ".join(f"{k}={cnt.get(k, 0)}" for k in ("T", "A", "B", "FAIL", "EMPTY")))
else:
    socks, lab = [], collections.Counter()
    for i in range(n):
        c = socket.create_connection(("10.7.1.1", port), timeout=3)
        f = c.makefile("rb"); l = f.readline().strip().decode(); lab[l] += 1; socks.append((c, f, l))
    print("held", dict(lab), flush=True)
    sys.stdin.readline()
    alive = 0
    for c, f, l in socks:
        try:
            c.settimeout(3); c.sendall(b"ping\n")
            if f.readline().strip().decode() == "pong-" + l: alive += 1
        except OSError:
            pass
    print(f"alive={alive} of {n}", flush=True)
EOF
cat > $W/render.py <<'EOF'
import sys
sys.path.insert(0, sys.argv[1])
from nikatel import ssh
name = sys.argv[2]
params = dict(a.split("=", 1) for a in sys.argv[3:])
print(ssh._script_text(name, params) if name in ssh.SCRIPTS else ssh.render_action(name, params))
EOF
R() { PYTHONPATH="$TREE" $PYV $W/render.py "$TREE" "$@"; }
R trident_set NAME=de-ir1 'PLAN=tcp:443:20443:0.30000000,tcp:443:30443:0.42857143' > $W/set_all.sh
R trident_set NAME=de-ir1 'PLAN=tcp:443:20443:1' > $W/set_a1.sh
R trident_set NAME=de-ir1 'PLAN=tcp:443:30443:1' > $W/set_b1.sh
R trident_set NAME=de-ir1 'PLAN=tcp:443:20443:0.50000000' > $W/set_half.sh
R trident_set NAME=de-ir1 'PLAN=tcp:443:20443:0.30000000,tcp:443:30443:0.42857143,tcp:80:20080:0.50000000' > $W/set_two_ports.sh
R trident_off NAME=de-ir1 PARK=0 > $W/off0.sh
R trident_off NAME=de-ir1 PARK=1 > $W/off1.sh
R trident_off NAME=de-ir1 PARK=2 > $W/off2.sh
R trident_list > $W/list.sh
[ -s $W/set_all.sh ] && [ -s $W/list.sh ] && chk ok "the actions render from the tree" || { chk bad "the actions render from the tree"; exit 1; }

# listeners
(IR python3 $W/srv.py 443 T &) ; (IR python3 $W/srv.py 20443 A &) ; (IR python3 $W/srv.py 30443 B &)
sleep 1
SNAP0=$(IR iptables-save -t nat | grep -v '^#' | grep -v '^:' | sort)
many() { CLI python3 $W/cli.py many "$@"; }
val() { echo "$1" | tr ' ' '\n' | grep "^$2=" | cut -d= -f2; }

echo "-- baseline: no trident, everything goes to the tunnel"
r=$(many 100); [ "$(val "$r" T)" = 100 ] && chk ok "100 of 100 connections reach the tunnel ($r)" || chk bad "baseline $r"

echo "-- the trident: tunnel 40 %, A 30 %, B 30 % (closed firewall)"
o=$(IR bash $W/set_all.sh); echo "   $o"
[ "$o" = "TRIDENT_SET de-ir1 2" ] && chk ok "two redirect rules in the chain" || chk bad "set output: $o"
r=$(many 900); echo "   $r"
T=$(val "$r" T); A=$(val "$r" A); B=$(val "$r" B); F=$(val "$r" FAIL)
{ [ "$F" = 0 ] && [ "$A" -gt 215 ] && [ "$A" -lt 325 ] && [ "$B" -gt 215 ] && [ "$B" -lt 325 ] && [ "$T" -gt 305 ] && [ "$T" -lt 415 ]; } && chk ok "the split is about 40/30/30 and nothing failed" || chk bad "split $r"
before=$(IR iptables-save -t nat | grep -v '^#' | sort); IR bash $W/set_all.sh >/dev/null; after=$(IR iptables-save -t nat | grep -v '^#' | sort)
[ "$before" = "$after" ] && chk ok "idempotent: the same call changes nothing" || chk bad "idempotent"
IR iptables -t nat -S PREROUTING | grep -q -- '--dport 9999' && chk ok "somebody else's NAT rule is untouched" || chk bad "foreign rule"

echo "-- what the panel reads"
l=$(IR bash $W/list.sh); echo "$l" | sed 's/^/   /'
[ "$(echo "$l" | grep -c '^TRULE de-ir1 tcp 443')" = 2 ] && echo "$l" | grep -q '^TJUMP de-ir1 tcp 443 NKTR_[0-9a-f]* 0$' && chk ok "trident_list: two rules, one jump, not shadowed" || chk bad "list"
IR iptables -t nat -I PREROUTING 1 -p tcp --dport 443 -j REDIRECT --to-ports 9998
IR bash $W/list.sh | grep -q '^TJUMP de-ir1 tcp 443 NKTR_[0-9a-f]* 1$' && chk ok "a foreign redirect ABOVE the jump is reported as shadowing" || chk bad "shadow"
IR iptables -t nat -D PREROUTING -p tcp --dport 443 -j REDIRECT --to-ports 9998

echo "-- all or nothing: plan changes between 'everything to A' and 'everything to B' while 1500 connections arrive"
IR bash $W/set_a1.sh >/dev/null
t0=$(date +%s.%N)
( many 1500 443 0.004 > $W/flip.out; date +%s.%N > $W/flip.end ) &
cp=$!
nflip=0
while [ ! -s $W/flip.end ] && [ $nflip -lt 200 ]; do IR bash $W/set_b1.sh >/dev/null; IR bash $W/set_a1.sh >/dev/null; nflip=$((nflip+2)); done
wait $cp; r=$(cat $W/flip.out); echo "   $r   ($nflip plan changes in $(echo "$(cat $W/flip.end) - $t0" | bc | cut -c1-5) s)"
{ [ "$(val "$r" T)" = 0 ] && [ "$(val "$r" FAIL)" = 0 ] && [ "$(val "$r" EMPTY)" = 0 ] && [ "$(val "$r" B)" -gt 100 ]; } && chk ok "no connection ever reached the tunnel or failed during $nflip plan changes (A=$(val "$r" A) B=$(val "$r" B))" || chk bad "flips: $r"

echo "-- connections that are open stay where they are"
IR bash $W/set_a1.sh >/dev/null
mkfifo $W/go 2>/dev/null
( CLI python3 $W/cli.py hold 25 < $W/go > $W/hold.out ) &
hp=$!
exec 7> $W/go
sleep 1.5; head -1 $W/hold.out | sed 's/^/   /'
IR bash $W/set_b1.sh >/dev/null
r=$(many 50); [ "$(val "$r" B)" = 50 ] && chk ok "new connections follow the new plan (50 of 50 to B)" || chk bad "after change $r"
o=$(IR bash $W/off1.sh); echo "   $o"
r=$(many 50); [ "$(val "$r" T)" = 50 ] && chk ok "careful end (PARK=1): new connections go to the tunnel (50 of 50)" || chk bad "after off1 $r"
IR bash $W/list.sh | grep -q '^TRULE' && chk bad "no redirect should be left" || chk ok "no redirect left in the chain"
IR iptables -t nat -S PREROUTING | grep -q 'NKTR_' && chk ok "the jump stays (the NAT chain is never empty)" || chk bad "jump"
newA=$(CLI timeout 3 python3 -c "
import socket
s=socket.socket(); s.settimeout(2)
try:
    s.connect(('10.7.1.1',20443)); print('connected')
except OSError: print('dropped')
"); [ "$newA" = dropped ] && chk ok "a NEW connection straight to the shifted port is dropped again (the accept keeps only established ones)" || chk bad "direct shifted port: $newA"
echo >&7
wait $hp; cat $W/hold.out | tail -1 | sed 's/^/   /'
tail -1 $W/hold.out | grep -q 'alive=25 of 25' && chk ok "25 of 25 connections opened BEFORE the changes still answer after two plan changes and the careful end" || chk bad "open connections: $(tail -1 $W/hold.out)"
exec 7>&-

echo "-- the clean removal"
o=$(IR bash $W/off2.sh); echo "   $o"
echo "$o" | grep -q '^TRIDENT_OFF de-ir1 0 0 0$' && chk ok "PARK=2 with no connection left: everything of the trident is gone" || chk bad "off2: $o"
SNAP1=$(IR iptables-save -t nat | grep -v '^#' | grep -v '^:' | sort)
[ "$SNAP0" = "$SNAP1" ] && chk ok "the NAT table is exactly what it was before (rules)" || { chk bad "nat table differs"; diff <(echo "$SNAP0") <(echo "$SNAP1"); }
IR iptables -S INPUT | grep -q 'nikatel-trident' && chk bad "INPUT still has trident rules" || chk ok "INPUT has no trident rule left"
IR iptables-save -t nat | grep -q NKWV && chk bad "chain left" || chk ok "the chain is gone"

echo "-- PARK=2 waits while a connection is open"
IR bash $W/set_a1.sh >/dev/null
( CLI python3 $W/cli.py hold 3 < $W/go > $W/hold2.out ) &
hp=$!; exec 7> $W/go; sleep 1.2
IR bash $W/off1.sh >/dev/null
o=$(IR bash $W/off2.sh); echo "   $o"
echo "$o" | grep -q '^TRIDENT_OFF de-ir1 0 [1-9][0-9]* 3$' && chk ok "PARK=2 with 3 open connections keeps the parked rules and says 3" || chk bad "off2 while open: $o"
echo >&7; wait $hp; exec 7>&-; sleep 0.5
o=$(IR bash $W/off2.sh); echo "$o" | grep -q '^TRIDENT_OFF de-ir1 0 0 0$' && chk ok "after they closed, PARK=2 cleans up" || chk bad "off2 after close: $o"

echo "-- two ports; a port that leaves the plan"
IR bash $W/set_two_ports.sh >/dev/null
[ "$(IR bash $W/list.sh | grep -c '^TJUMP')" = 2 ] && chk ok "one jump per users' port (2)" || chk bad "two ports"
IR bash $W/set_all.sh >/dev/null
[ "$(IR bash $W/list.sh | grep -c '^TJUMP')" = 1 ] && chk ok "the jump of the port that left the plan is gone" || chk bad "port left"
IR iptables -S INPUT | grep -- '--dport 20080' | grep -q ESTABLISHED && chk ok "its accept now lets only established connections go on" || chk bad "accept parked"
IR bash $W/off0.sh >/dev/null
SNAP2=$(IR iptables-save -t nat | grep -v '^#' | grep -v '^:' | sort)
[ "$SNAP0" = "$SNAP2" ] && IR iptables -S INPUT | grep -vq nikatel-trident && chk ok "PARK=0 removes everything at once" || chk bad "off0"

echo "-- a mistake in the plan leaves the old chain alone"
IR bash $W/set_all.sh >/dev/null
bad_o=$(IR bash -c "$(R trident_set NAME=de-ir1 'PLAN=tcp:443:20443:0.30000000' | sed 's/--to-ports \$to/--to-ports \$to --bogus/')" 2>&1)
echo "$bad_o" | sed 's/^/   /'
echo "$bad_o" | grep -q '^TRIDENT_FAIL' && [ "$(IR bash $W/list.sh | grep -c '^TRULE')" = 2 ] && chk ok "TRIDENT_FAIL, and the two old rules are still in the chain" || chk bad "failed set"
IR bash $W/off0.sh >/dev/null

echo
echo "== $VARIANT: $ok ok, $bad failed"
[ "$bad" = 0 ]

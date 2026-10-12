"""SSH to the managed servers.

Only *named scripts* from SCRIPTS run. They are reviewed, read-only, and take validated
parameters. While `settings.read_only` is true, `run_raw` refuses everything else - the panel
can look at the servers but cannot change them. That is the safety net of phase 1.
"""
from __future__ import annotations

import re
import subprocess
from collections.abc import Mapping
from dataclasses import dataclass

from .config import get_settings
from .log import get_logger

log = get_logger("ssh")


class SSHError(RuntimeError):
    def __init__(self, msg: str, code: int = 1):
        super().__init__(msg)
        self.code = code          # 255 = the ssh connection itself failed (worth one retry)


@dataclass(frozen=True)
class Node:
    host: str
    user: str = "root"
    port: int = 22


# ---------------------------------------------------------------- reviewed read-only scripts
# Each value: (script, {param: validation regex}). Params are substituted as @NAME@.

_IDENT = r"^[A-Za-z0-9][A-Za-z0-9._@-]{0,63}$"
_IPV4 = r"^\d{1,3}(\.\d{1,3}){3}$"
_PORTS = r"^(\d{1,5}( \d{1,5})*)?$"
_UNITS = r"^([A-Za-z0-9][A-Za-z0-9._@-]{0,63}(\.service)?( |$))+$"

SCRIPTS: dict[str, tuple[str, Mapping[str, str]]] = {
    # resources of a server, one second of CPU and network counters
    "facts": (r"""s1=$(awk 'NR==1{print $2+$3+$4+$5+$6+$7+$8, $5+$6}' /proc/stat)
r1=$(awk 'NR>2 && $1!~/^(lo|docker|veth|br-)/{gsub(":"," ");rx+=$2;tx+=$10}END{print rx+0,tx+0}' /proc/net/dev)
sleep 1
s2=$(awk 'NR==1{print $2+$3+$4+$5+$6+$7+$8, $5+$6}' /proc/stat)
r2=$(awk 'NR>2 && $1!~/^(lo|docker|veth|br-)/{gsub(":"," ");rx+=$2;tx+=$10}END{print rx+0,tx+0}' /proc/net/dev)
echo "CPU=$s1 $s2"
echo "NET=$r1 $r2"
awk '/^MemTotal/{t=$2}/^MemAvailable/{a=$2}/^SwapTotal/{st=$2}/^SwapFree/{sf=$2}END{print "MEM="t" "a" "st" "sf}' /proc/meminfo
df -P / | awk 'NR==2{print "DISK="$2" "$3}'
echo "LOAD=$(cut -d' ' -f1-3 /proc/loadavg) $(nproc)"
echo "UP=$(cut -d. -f1 /proc/uptime)"
echo "CONN=$(ss -tan state established 2>/dev/null | tail -n +2 | wc -l)"
echo "HOST=$(hostname) $(uname -r)"
echo "TOP=$(ps -eo comm=,pcpu= --sort=-pcpu 2>/dev/null | head -1)"
echo "ABUSE=$(iptables -S abuse-defender 2>/dev/null | grep -c DROP)"
""", {}),

    # what tunnels already exist on this server (backhaul tomls + our GRE links), without any secret
    "scan_tunnels": (r"""for f in /etc/backhaul/*.toml; do [ -f "$f" ] || continue
  role=$(grep -m1 -oE '^\[(server|client)\]' "$f" | tr -d '[]'); addr=$(grep -m1 -oE '^(bind_addr|remote_addr) *= *"[^"]+"' "$f" | sed 's/.*"\(.*\)"/\1/')
  tr=$(grep -m1 -oE '^transport *= *"[^"]+"' "$f" | sed 's/.*"\(.*\)"/\1/'); ports=$(awk '/^ports *= *\[/{f=1;next} f&&/\]/{f=0} f{gsub(/[",]/,""); gsub(/ /,""); print}' "$f" | tr '\n' ',')
  echo "BH $(basename "$f" .toml) ${role:-?} ${addr:-?} ${tr:-?} ${ports:-none}"; done
for i in $(ip -o link show type gre 2>/dev/null | awk -F': ' '{print $2}' | cut -d@ -f1); do case "$i" in gre0|gretap0|erspan0) continue;; esac
  echo "GRE $i $(ip -o -4 addr show dev "$i" 2>/dev/null | awk '{print $4}' | head -1 || echo -) $(ip -d link show "$i" 2>/dev/null | grep -oE 'remote [0-9.]+' | awk '{print $2}' | head -1) $(systemctl is-active "nikatel-gre-$i.service" 2>/dev/null || echo none)"; done
echo "END"
""", {}),

    # versions of the tunnel cores on this server
    "engine_versions": (r"""echo "ENGINE backhaul $([ -x /usr/local/bin/backhaul ] && /usr/local/bin/backhaul -v 2>&1 | head -1 || echo NONE)"
echo "ENGINE xray $([ -x /opt/nikatel-xgw/xray ] && /opt/nikatel-xgw/xray version 2>&1 | head -1 || echo NONE)"
echo "ENGINE backpack $([ -x /usr/local/bin/backpack ] && /usr/local/bin/backpack -v 2>&1 | head -1 || echo NONE)"
echo "ENGINE paqet $([ -x /usr/local/bin/paqet ] && /usr/local/bin/paqet version 2>&1 | head -1 || echo NONE)"
echo "ENGINE chisel $([ -x /usr/local/bin/nikatel-chisel ] && /usr/local/bin/nikatel-chisel --version 2>&1 | head -1 || echo NONE)"
""", {}),

    # checksum of one core binary (is the same build installed as the one in the panel's cache?)
    "engine_sha": (r"""if [ -x '@BIN@' ]; then echo "SHA=$(sha256sum '@BIN@' | awk '{print $1}')"; else echo SHA=none; fi
""", {"BIN": r"^/usr/local/bin/[A-Za-z0-9._-]{1,40}$"}),

    # what the BackPack / Paqet / Chisel builders must know about a server
    "simple_facts": (r"""echo "LISTEN=$(ss -ltnH 2>/dev/null | awk '{print $4}' | sed 's/.*://' | sort -un | tr '\n' ' ')"
echo "LISTENU=$(ss -lunH 2>/dev/null | awk '{print $5}' | sed 's/.*://' | sort -un | tr '\n' ' ')"
echo "UNITS=$(systemctl list-unit-files --no-legend 2>/dev/null | awk '{print $1}' | grep -E '^(backpack|paqet|chisel|nikatel-sink)' | tr '\n' ' ')"
echo "IPS=$(ip -4 -o addr show 2>/dev/null | awk '{print $4}' | tr '\n' ' ')"
echo "ARCH=$(uname -m)"
echo "ID=$(id -u)"
if command -v ufw >/dev/null 2>&1 && ufw status 2>/dev/null | head -1 | grep -q "Status: active"; then echo "UFW=1"; else echo "UFW=0"; fi
if command -v python3 >/dev/null 2>&1; then echo "PY=1"; else echo "PY=0"; fi
""", {}),

    # the default interface, its address and the gateway's MAC (Paqet sends raw frames through it)
    "paqet_net": (r"""IF=$(ip route show default 2>/dev/null | awk '/default/ {print $5; exit}')
IP=$(ip -4 -o addr show dev "$IF" 2>/dev/null | awk '{print $4}' | cut -d/ -f1 | head -1)
GW=$(ip route show default 2>/dev/null | awk '/default/ {print $3; exit}')
ping -c1 -W1 "$GW" >/dev/null 2>&1 || true
MAC=$(ip neigh show "$GW" 2>/dev/null | awk '{for(i=1;i<=NF;i++) if($i=="lladdr") print $(i+1)}' | head -1)
echo "IF=$IF"; echo "IP=$IP"; echo "GW=$GW"; echo "MAC=$MAC"
""", {}),

    # the fingerprint the Chisel server printed when it started (the client pins it)
    "chisel_fp": (r"""echo "FP=$(journalctl -u 'chisel-@NAME@.service' -n 60 --no-pager 2>/dev/null | grep -o 'Fingerprint [A-Za-z0-9+/=]*' | tail -1 | cut -d' ' -f2)"
""", {"NAME": r"^[a-z0-9][a-z0-9-]{0,14}$"}),

    # latency / jitter / loss and (when SECS > 0) speed both ways through a tunnel's test door
    # (runs on the Iran server, talks to the sink on the foreign server through the tunnel)
    "probe_run": (r"""python3 - '@PORT@' '@SECS@' '@STREAMS@' '@PINGS@' '@KEY@' '@TO@' <<'__NK_PY__'
import socket, threading, time, json, sys
P = int(sys.argv[1]); SECS = float(sys.argv[2]); N = int(sys.argv[3]); PINGS = int(sys.argv[4]); KEY = sys.argv[5].encode()
TO = float(sys.argv[6]) if len(sys.argv) > 6 else 2.5
out = {"port": P}
rtts = []; lost = 0
try:
    s = socket.create_connection(("127.0.0.1", P), timeout=min(6, TO * 2 + 1))
    s.sendall(KEY + b"E")
    for i in range(PINGS):
        t = time.time()
        try:
            s.settimeout(TO)
            s.sendall(b"ping%03d" % i)
            d = s.recv(16)
            if d:
                rtts.append((time.time() - t) * 1000)
            else:
                lost += 1
        except Exception:
            lost += 1
        time.sleep(0.04)
    s.close()
except Exception as e:
    out["error"] = str(e)[:120]
if rtts:
    rtts.sort()
    avg = sum(rtts) / len(rtts)
    out["latency_ms"] = round(avg, 1)
    out["min_ms"] = round(rtts[0], 1)
    out["p95_ms"] = round(rtts[int(len(rtts) * 0.95) - 1] if len(rtts) > 1 else rtts[0], 1)
    out["jitter_ms"] = round(sum(abs(r - avg) for r in rtts) / len(rtts), 1)
out["loss_pct"] = round(lost * 100.0 / max(PINGS, 1), 1)
def run(mode, results, i):
    rate = 0.0
    try:
        s = socket.create_connection(("127.0.0.1", P), timeout=6)
        s.sendall(KEY + mode)
        t0 = time.time()
        if mode == b"D":
            n = 0; nw = 0; warm = None
            while time.time() - t0 < SECS:
                d = s.recv(1 << 20)
                if not d:
                    break
                now = time.time(); n += len(d)
                if warm is None and now - t0 >= 0.7:
                    warm = now; nw = n
            end = time.time()
            rate = (n - nw) * 8 / (end - warm) / 1e6 if (warm and end > warm) else n * 8 / max(end - t0, 0.2) / 1e6
        else:
            b = b"x" * 65536
            while time.time() - t0 < SECS:
                s.sendall(b)
            s.settimeout(30)
            buf = b""
            while b"\n" not in buf:
                d = s.recv(32)
                if not d:
                    break
                buf += d
            try:
                rate = float(buf[1:].strip() or 0)
            except ValueError:
                rate = 0.0
        s.close()
    except Exception:
        pass
    results[i] = rate
if SECS > 0 and "error" not in out:
    for mode, key in ((b"D", "down_mbps"), (b"U", "up_mbps")):
        res = [0.0] * N
        th = [threading.Thread(target=run, args=(mode, res, i)) for i in range(N)]
        [x.start() for x in th]; [x.join() for x in th]
        out[key] = round(sum(res), 1)
        out[key.replace("mbps", "streams")] = [round(x, 1) for x in res]
print("NKPROBE " + json.dumps(out))
__NK_PY__
""", {"PORT": r"^\d{1,5}$", "SECS": r"^\d{1,2}(\.\d)?$", "STREAMS": r"^\d{1,2}$", "PINGS": r"^\d{1,3}$", "KEY": r"^[a-z0-9]{1,32}$", "TO": r"^\d{1,2}(\.\d)?$"}),

    # can this server open a TCP connection to HOST:PORT (a tunnel's control port, seen from the other side)?
    "tcp_probe": (r"""if timeout 4 bash -c "exec 3<>/dev/tcp/@HOST@/@PORT@" 2>/dev/null; then echo OPEN; else echo CLOSED; fi
""", {"HOST": r"^[A-Za-z0-9][A-Za-z0-9.\-]{0,120}$", "PORT": r"^\d{1,5}$"}),

    # 4 pings to a host (used from the Iran servers: is a foreign address reachable / filtered?)
    # the text of one Backhaul config file, base64 (the re-tune reads the real file, changes two lines and can put the original back). Read-only.
    "read_cfg": (r"""base64 -w0 '@PATH@'
""", {"PATH": r"^/etc/backhaul/[A-Za-z0-9._-]{1,80}\.toml$"}),

    # how far a detached install (install_btblock / install_marzban_node) is: "STATE running|ok|failed|none" and the last lines of its log or result. Read-only.
    "install_status": (r"""d=/var/lib/nikatel-install; n=@NAME@; s="$n"; [ "$n" = btblock ] && s=btblock; [ "$n" = mznode ] && s=mznode
if [ -f $d/$n.ok ]; then echo "STATE ok"; cat $d/$n.out 2>/dev/null | tail -n 3
elif [ -f $d/$n.fail ]; then echo "STATE failed"; tail -n 12 $d/$n.log 2>/dev/null
elif pgrep -f "nikatel-install/$n.sh" >/dev/null 2>&1; then echo "STATE running"; tail -n 2 $d/$n.log 2>/dev/null; tail -n 1 $d/mz-install.log 2>/dev/null
else echo "STATE none"; fi
""", {"NAME": r"^(btblock|mznode)$"}),

    "ping_host": (r"""ping -c 4 -W 2 -i 0.4 '@HOST@' 2>&1 | grep -E 'packets transmitted|rtt'; true
""", {"HOST": r"^[A-Za-z0-9][A-Za-z0-9.\-]{0,120}$"}),

    # packet loss from this server to several hosts at once (20 pings each, in parallel): "LOSS host sent received avg_ms". Read-only.
    "loss_many": (r"""for h in @HOSTS@; do ( o=$(ping -c 20 -i 0.2 -W 1 -q "$h" 2>&1); t=$(echo "$o" | sed -n 's/^\([0-9]*\) packets transmitted, \([0-9]*\) received.*/\1 \2/p'); a=$(echo "$o" | awk -F/ '/^rtt/{print $5}'); echo "LOSS $h ${t:-20 0} ${a:--}" ) & done; wait
""", {"HOSTS": r"^[A-Za-z0-9][A-Za-z0-9.\-]{0,120}( [A-Za-z0-9][A-Za-z0-9.\-]{0,120}){0,11}$"}),

    # one side of a tunnel: unit states, the GRE interface, its counters, a ping to the other end,
    # and the forwarded ports (only on the Iran side, where users connect)
    "tunnel_side": (r"""for u in @UNITS@; do echo "UNIT $u $(systemctl is-active "$u" 2>/dev/null || echo unknown)"; done
if [ -n "@IFACE@" ]; then
  echo "IFACE @IFACE@ $(ip -o link show '@IFACE@' 2>/dev/null | grep -o 'state [A-Z]*' | head -1 | cut -d' ' -f2) $(cat /sys/class/net/@IFACE@/statistics/rx_bytes /sys/class/net/@IFACE@/statistics/tx_bytes 2>/dev/null | tr '\n' ' ')"
fi
if [ -n "@PEER@" ]; then
  P=$(ping -c 20 -i 0.2 -W 1 -q '@PEER@' 2>/dev/null)
  echo "PEER @PEER@ $(echo "$P" | awk -F/ '/^rtt/{print $5}') $(echo "$P" | grep -o '[0-9]*% packet loss' | cut -d% -f1)"
fi
for p in @PORTS@; do echo "LISTEN $p $(ss -ltnH 2>/dev/null | grep -c ":$p ")"; done
# end to end: one request through the first forwarded port (Iran side only); any HTTP answer proves the data path, 000 = nothing came back
set -- @PORTS@; if [ -n "$1" ]; then C=$(curl -s -m 4 -o /dev/null -w '%{http_code} %{time_total}' http://127.0.0.1:$1/ 2>/dev/null); echo "DATA $1 ${C:-000 0}"; fi
echo "NOW $(date +%s)"
""", {"UNITS": _UNITS, "IFACE": r"^([A-Za-z0-9][A-Za-z0-9._-]{0,14})?$", "PEER": r"^(\d{1,3}(\.\d{1,3}){3})?$", "PORTS": _PORTS}),

    # the users' path, looked at from another Iran server: one HTTP request to host:port (several at once, in parallel) - http first,
    # then https without verification for TLS ports. "REACH host port <http code> <seconds>": any code but 000 proves the port answers from Iran.
    "reach_many": (r"""for hp in @TARGETS@; do ( h=${hp%:*}; p=${hp##*:}; r=$(curl -s -o /dev/null -m 6 --connect-timeout 4 -w '%{http_code} %{time_total}' "http://$h:$p/" 2>/dev/null); c=${r%% *}; if [ -z "$r" ] || [ "$c" = "000" ]; then r=$(curl -sk -o /dev/null -m 6 --connect-timeout 4 -w '%{http_code} %{time_total}' "https://$h:$p/" 2>/dev/null); fi; [ -n "$r" ] || r="000 0"; echo "REACH $h $p $r" ) & done; wait
""", {"TARGETS": r"^[A-Za-z0-9.\-]{1,253}:\d{1,5}( [A-Za-z0-9.\-]{1,253}:\d{1,5}){0,80}$"}),

    # can this server open a TCP connection to host:port (several at once): "REACH host:port 1|0 <connect ms>". Only connects, sends nothing.
    "tcp_reach": (r"""for hp in @TARGETS@; do h=${hp%:*}; p=${hp##*:}; ( s=$(date +%s%N); if timeout 4 bash -c "echo > /dev/tcp/$h/$p" 2>/dev/null; then e=$(date +%s%N); echo "REACH $hp 1 $(( (e - s) / 1000000 ))"; else echo "REACH $hp 0 0"; fi ) & done; wait
""", {"TARGETS": r"^[A-Za-z0-9][A-Za-z0-9.\-]{0,100}:\d{1,5}( [A-Za-z0-9][A-Za-z0-9.\-]{0,100}:\d{1,5}){0,39}$"}),
    # does a TLS handshake with this server name (SNI) get an answer, and does one with a neutral name: tells a filtered name from a filtered address
    "tls_sni": (r"""ip=$(getent ahostsv4 '@HOST@' | head -1 | cut -d" " -f1)
c() { timeout 7 openssl s_client -connect "$ip:@PORT@" -servername "$1" </dev/null 2>/dev/null | grep -c 'BEGIN CERTIFICATE'; }
echo "SNI $ip own=$(c '@HOST@') control=$(c example.com)"
""", {"HOST": r"^[A-Za-z0-9][A-Za-z0-9.\-]{0,100}$", "PORT": r"^\d{1,5}$"}),

    # is the server there at all, and what is it
    "ping": (r"""echo "HOST=$(hostname) $(uname -sr)" """, {}),

    # the shop bot (PHP + MariaDB on the bot server): services, cron, counts, states, logs - as JSON.
    # DB credentials are read on that server from its own env file; nothing secret leaves it.
    # the Mini App installed next to the bot: which files, their fingerprints, the version in index.html, whether the PHP parses, the public URL
    "bot_miniapp_info": (r"""cd '@DIR@' 2>/dev/null || { echo NO_DIR; exit 0; }
[ -d app ] || { echo NOT_INSTALLED; exit 0; }
for f in api.php index.html app.js app.css; do if [ -f "app/$f" ]; then echo "FILE $f $(stat -c %s "app/$f") $(sha256sum "app/$f" | cut -c1-16)"; else echo "MISSING $f"; fi; done
grep -oE 'app\.js\?v=[0-9.]+' app/index.html 2>/dev/null | head -1 | sed 's/.*v=/VERSION /'
[ -f app/api.php ] && { php -l app/api.php >/dev/null 2>&1 && echo PHP_OK || echo PHP_BAD; }
grep -oE "botUrl *= *'[^']+'" baseInfo.php 2>/dev/null | head -1 | sed "s/.*'\(.*\)'/URL \1/"
[ -f settings/miniapp_error.log ] && echo "ERRORS $(grep -c . settings/miniapp_error.log) $(tail -n 1 settings/miniapp_error.log | cut -c1-200)"; true
""", {"DIR": r"^/var/www/[A-Za-z0-9._-]{1,40}$"}),

    "bot_info": (r"""set -a; [ -f '@ENV@' ] && . '@ENV@'; set +a
Q() { mysql --batch --raw -N --default-character-set=utf8mb4 -u "$DB_USER" -p"$DB_PASS" "$DB_NAME" -e "$1" 2>/dev/null; }
echo "INSTALLED $([ -d '@DIR@' ] && [ -n "$DB_PASS" ] && echo 1 || echo 0)"
for u in php8.3-fpm php8.2-fpm php-fpm mariadb mysql caddy nginx; do echo "UNIT $u $(systemctl is-active "$u" 2>/dev/null || echo unknown)"; done
echo "CRON $([ -f /etc/cron.d/nikabot ] && grep -c '^[^#]' /etc/cron.d/nikabot || echo 0)"
echo "COUNTS $(Q "SELECT CONCAT((SELECT COUNT(*) FROM users),' ',(SELECT COUNT(*) FROM orders_list),' ',(SELECT COUNT(*) FROM pays WHERE state='have_sent'),' ',(SELECT COUNT(*) FROM pays WHERE state IN ('confirmed','approved')),' ',(SELECT COUNT(*) FROM server_plans),' ',(SELECT COUNT(*) FROM server_config))")"
echo "STAGE $(mysql -N -e "SELECT COUNT(*) FROM nikabot_stage.users" 2>/dev/null || echo -)"
echo "STATES $(Q "SELECT value FROM setting WHERE type='BOT_STATES' LIMIT 1" | head -c 4000)"
echo "PANELS $(Q "SELECT CONCAT(id,'|',type,'|',SUBSTRING_INDEX(SUBSTRING_INDEX(panel_url,'/',3),'/',-1)) FROM server_config" | tr '\n' ' ')"
echo "TODAY $(Q "SELECT CONCAT(COUNT(*),' ',COALESCE(SUM(amount),0)) FROM orders_list WHERE date >= UNIX_TIMESTAMP(CURDATE())")"
echo "WEEK $(Q "SELECT CONCAT(COUNT(*),' ',COALESCE(SUM(amount),0)) FROM orders_list WHERE date >= UNIX_TIMESTAMP()-7*86400")"
echo "MONTH $(Q "SELECT CONCAT(COUNT(*),' ',COALESCE(SUM(amount),0)) FROM orders_list WHERE date >= UNIX_TIMESTAMP()-30*86400")"
echo "NEWUSERS $(Q "SELECT CONCAT((SELECT COUNT(*) FROM users WHERE date >= UNIX_TIMESTAMP(CURDATE())),' ',(SELECT COUNT(*) FROM users WHERE date >= UNIX_TIMESTAMP()-7*86400))")"
echo "BACKUPS $(ls -1 /opt/nikabot/backups 2>/dev/null | tail -n 5 | tr '\n' ' ')"
echo "TICKETS $(Q "SELECT COUNT(*) FROM chats WHERE state=1")"
echo "QUEUE $(Q "SELECT COUNT(*) FROM send_list WHERE state IN (1,2)")"
echo "HOOKED $(grep -c text_overrides.json '@DIR@/config.php' 2>/dev/null || echo 0)"
echo "LOG_ERRORS"; tail -n 20 '@DIR@/error_log.txt' 2>/dev/null | cut -c1-240
echo "LOG_PHP"; tail -n 10 '@DIR@/php_errors.log' 2>/dev/null | cut -c1-240
echo "LOG_END"
""", {"DIR": r"^/var/www/[A-Za-z0-9._-]{1,40}$", "ENV": r"^/opt/[A-Za-z0-9._-]{1,40}/db\.env$"}),

    # ask Telegram about the bot, from the bot server with the bot's own token (the token never leaves it)
    "bot_tg_get": (r"""TOK=$(grep -oE "botToken *= *'[^']+'" '@DIR@/baseInfo.php' | head -1 | sed "s/.*'\(.*\)'/\1/")
[ -n "$TOK" ] || { echo '{"ok":false,"description":"no bot token in baseInfo.php"}'; exit 0; }
curl -s -m 20 "https://api.telegram.org/bot$TOK/@METHOD@" | head -c 4000
""", {"DIR": r"^/var/www/[A-Za-z0-9._-]{1,40}$", "METHOD": r"^(getMe|getWebhookInfo)$"}),

    # one read-only query against the bot's database (SELECT / SHOW only, no second statement)
    "bot_query": (r"""set -a; [ -f '@ENV@' ] && . '@ENV@'; set +a
mysql --batch --default-character-set=utf8mb4 -u "$DB_USER" -p"$DB_PASS" "$DB_NAME" -e @SQL@ 2>&1 | head -c 200000
""", {"ENV": r"^/opt/[A-Za-z0-9._-]{1,40}/db\.env$", "SQL": r"^'(SELECT|SHOW|DESCRIBE)\b[^;'`\\]{0,1500}'$"}),

    # the bot's own texts and button labels (values.php, read by PHP itself) and the panel's overrides next to them
    "bot_texts": (r"""cd '@DIR@' || exit 1
echo "DEFAULTS $(php -r 'include "settings/values.php"; echo json_encode(["texts" => $mainValues, "buttons" => $buttonValues], JSON_UNESCAPED_UNICODE);' 2>/dev/null | head -c 600000)"
echo "TEXT_OVERRIDES $(cat settings/text_overrides.json 2>/dev/null | tr -d '\n' | head -c 300000)"
echo "BUTTON_OVERRIDES $(cat settings/button_overrides.json 2>/dev/null | tr -d '\n' | head -c 300000)"
echo "HOOKED $(grep -c text_overrides.json config.php 2>/dev/null || echo 0)"
""", {"DIR": r"^/var/www/[A-Za-z0-9._-]{1,40}$"}),

    # the database dumps of the bot
    # shop bot: one dump file as base64 (for the Telegram backup); refuses a file over 30 MB
    "bot_fetch_dump": (r"""f='@FILE@'; if [ -f "$f" ] && [ "$(stat -c %s "$f")" -lt 30000000 ]; then base64 -w0 "$f"; else echo TOO_BIG_OR_MISSING; fi
""", {"FILE": r"^/opt/nikabot/backups/[A-Za-z0-9._-]{1,80}\.sql\.gz$"}),

    # shop bot: the bot's cron jobs, as the cron file is now (active, or renamed with .disabled)
    "bot_cron_get": (r"""if [ -f /etc/cron.d/nikabot ]; then echo "CRONFILE active"; cat /etc/cron.d/nikabot
elif [ -f /etc/cron.d/nikabot.disabled ]; then echo "CRONFILE disabled"; cat /etc/cron.d/nikabot.disabled
else echo "CRONFILE missing"; fi
""", {}),
    "bot_backups": (r"""for f in /opt/nikabot/backups/*.sql.gz; do [ -f "$f" ] && echo "BACKUP $(basename "$f") $(stat -c %s "$f")"; done; true
""", {}),

    # what the tunnel builder must know about a server before it touches it
    "build_facts": (r"""echo "IPS=$(ip -o -4 addr show scope global 2>/dev/null | awk '{print $4}' | tr '\n' ' ')"
echo "LINKS=$(ip -br link show 2>/dev/null | awk '{print $1}' | cut -d@ -f1 | tr '\n' ' ')"
echo "LISTEN=$(ss -ltnH 2>/dev/null | awk '{print $4}' | sed 's/.*://' | sort -un | tr '\n' ' ')"
echo "UNITS=$(systemctl list-units --all --no-legend --plain 'backhaul-*' 'nikatel-*' 'backpack-*' 'chisel-*' 'paqet-*' 2>/dev/null | awk '{print $1}' | tr '\n' ' ')"
echo "BROKEN=$(systemctl list-units --all --no-legend --plain 'backhaul-*.service' 'nikatel-gre-*.service' 2>/dev/null | awk '$3=="failed"{print $1}' | tr '\n' ' ')"
modprobe ip_gre 2>/dev/null; echo "GRE_MOD=$( (lsmod | grep -q '^ip_gre' || [ -d /sys/module/ip_gre ]) && echo 1 || echo 0)"
echo "BACKHAUL=$([ -x /usr/local/bin/backhaul ] && /usr/local/bin/backhaul -v 2>&1 | head -1 || echo none)"
echo "IPTABLES=$(command -v iptables >/dev/null 2>&1 && echo 1 || echo 0)"
""", {}),

    # largest ICMP payload that crosses to the peer with DF set (GRE inner MTU)
    "mtu_probe": (r"""best=0; for s in 1472 1464 1452 1440 1420 1400 1380 1350 1300 1250 1200; do ping -c 2 -W 1 -M do -s $s '@PEER@' >/dev/null 2>&1 && { best=$s; break; }; done; echo "MTU=$best"
""", {"PEER": _IPV4}),

    # 10 pings over the GRE interface to the other end
    "gre_verify": (r"""ping -c 10 -i 0.3 -W 1 -I '@IFACE@' '@PEER@' 2>&1 | tail -3
""", {"IFACE": r"^[A-Za-z0-9][A-Za-z0-9._-]{0,14}$", "PEER": _IPV4}),

    # hold test: 20 slow pings, does the link keep passing packets
    "gre_hold": (r"""ping -c 20 -i 0.4 -W 1 -I '@IFACE@' '@PEER@' 2>&1 | tail -3
""", {"IFACE": r"^[A-Za-z0-9][A-Za-z0-9._-]{0,14}$", "PEER": _IPV4}),

    # who is connected to a local port (the backhaul control connection)
    "estab_check": (r"""ss -tnH state established '( sport = :@PORT@ )' 2>/dev/null | head -5
""", {"PORT": r"^\d{1,5}$"}),

    # a request through a forwarded port on the Iran side
    "http_probe": (r"""curl -sS -m 8 -o /dev/null -w '%{http_code}' 'http://127.0.0.1:@PORT@/' 2>/dev/null || echo FAIL
""", {"PORT": r"^\d{1,5}$"}),

    # 30 pings with per-packet times (for jitter) over the GRE interface
    "gre_pings": (r"""I=""; V=0.6; [ -n '@IFACE@' ] && I="-I @IFACE@" && V=0.2
ping -c 30 -i $V -W 1 $I '@PEER@' 2>&1 | grep -E 'time=|packets transmitted'
""", {"IFACE": r"^([A-Za-z0-9][A-Za-z0-9._-]{0,14})?$", "PEER": _IPV4}),

    # pull from a sink through a forwarded port for 10 s; bytes per second
    "speed_pull": (r"""curl -s -m 12 -o /dev/null -w 'SPEED=%{speed_download} SIZE=%{size_download} TIME=%{time_total} CODE=%{http_code}\n' 'http://127.0.0.1:@PORT@/' 2>/dev/null || echo "SPEED=0 SIZE=0 TIME=0 CODE=000"
echo "WHO $(ss -tnpH state established '( dport = :@PORT@ or sport = :@PORT@ )' 2>/dev/null | head -3 | tr '\n' ';')"
""", {"PORT": r"^\d{1,5}$"}),

    # the last log lines of a unit
    "unit_log": (r"""journalctl -u '@UNIT@' -n 8 --no-pager 2>/dev/null | cut -c1-180
""", {"UNIT": r"^[A-Za-z0-9][A-Za-z0-9._@-]{0,63}(\.service|\.timer)?$"}),

    # the Iran gateway: service state, version, which of our ports listen, last log lines
    "xgw_status": (r"""echo "ACTIVE $(systemctl is-active nikatel-xgw 2>/dev/null || echo none)"
echo "VER $(/opt/nikatel-xgw/xray version 2>/dev/null | head -1)"
for p in @PORTS@; do echo "LISTEN $p $(ss -ltnH 2>/dev/null | grep -c ":$p ")"; done
echo LOG; tail -n 6 /opt/nikatel-xgw/error.log 2>/dev/null | cut -c1-200
""", {"PORTS": _PORTS}),

    # through every local test door of the gateway: alive, how fast, which exit IP / country
    "xgw_test": (r"""for p in @PORTS@; do
  r=$(curl -s -m 14 --socks5-hostname 127.0.0.1:$p -o /dev/null -w '%{http_code} %{time_total}' https://www.gstatic.com/generate_204 2>/dev/null)
  t=$(curl -s -m 14 --socks5-hostname 127.0.0.1:$p https://www.cloudflare.com/cdn-cgi/trace 2>/dev/null | grep -E '^(ip|loc)=' | tr '\n' ' ')
  echo "T $p $r | $t"
done
""", {"PORTS": _PORTS}),
}


def _ssh_base(node: Node) -> list[str]:
    """The ssh command line. With multiplexing on, every command shares one background connection per server (ControlMaster=auto: the first command starts it and it
    stays two minutes after the last use; a second command that races the start simply connects on its own). A new SSH handshake for each of the many short commands
    of the jobs was most of the CPU the panel used."""
    s = get_settings()
    cmd = [
        "ssh", "-i", str(s.ssh_key), "-p", str(node.port),
        "-o", "BatchMode=yes",
        "-o", "StrictHostKeyChecking=accept-new",
        "-o", f"UserKnownHostsFile={s.known_hosts}",
        "-o", f"ConnectTimeout={s.ssh_connect_timeout}",
        "-o", "ServerAliveInterval=10",
        "-o", "LogLevel=ERROR",
    ]

    if s.ssh_multiplex:
        try:
            (s.data_dir / "cm").mkdir(parents=True, exist_ok=True)
            (s.data_dir / "cm").chmod(0o700)
        except OSError:
            pass

        cmd += ["-o", "ControlMaster=auto", "-o", f"ControlPath={s.data_dir / 'cm' / '%C'}", "-o", "ControlPersist=120"]

    return cmd + [f"{node.user}@{node.host}"]

def _exec(node: Node, script: str, timeout: int) -> str:
    try:
        r = subprocess.run(_ssh_base(node) + ["bash -s"], input=script.encode(), stdout=subprocess.PIPE,
                           stderr=subprocess.STDOUT, timeout=timeout, check=False)
    except FileNotFoundError:
        raise SSHError("ssh is not installed on the panel server")
    except subprocess.TimeoutExpired:
        raise SSHError(f"timeout after {timeout}s")

    out = r.stdout.decode("utf-8", "replace")

    if r.returncode != 0:
        tail = out.strip().splitlines()[-1] if out.strip() else f"exit {r.returncode}"
        log.warning("ssh %s exit %s: %s", node.host, r.returncode, out.strip().replace("\n", " | ")[-400:])

        if "Permission denied" in out:
            tail = "the panel's key is not accepted (authorized_keys)"
        elif "Host key verification failed" in out or "IDENTIFICATION HAS CHANGED" in out:
            tail = "the host key of this server changed (reinstalled?) - forget it in Settings"
        elif r.returncode == 255:
            why = re.search(r"(Timeout, server[^|]*|Connection (reset|closed|timed out)[^|]*|kex_exchange_identification[^|]*|Broken pipe)", out)
            tail = "ssh connection lost (" + (why.group(0).strip() if why else "network") + ")"

        raise SSHError(tail[:200], r.returncode)

    return out


def run_script(node: Node, name: str, params: Mapping[str, str] | None = None, timeout: int = 40) -> str:
    """Run one reviewed script with validated parameters."""
    if name not in SCRIPTS:
        raise ValueError(f"unknown script {name!r}")

    script, rules = SCRIPTS[name]
    params = dict(params or {})

    for key, rule in rules.items():
        value = str(params.get(key, ""))

        if not re.match(rule, value):
            raise ValueError(f"bad value for {key}: {value[:40]!r}")

        script = script.replace(f"@{key}@", value)

    unknown = [k for k in params if k not in rules]

    if unknown:
        raise ValueError(f"script {name} takes no parameter {unknown[0]!r}")

    log.debug("run %s on %s", name, node.host)
    return _exec(node, script, timeout)


# ---------------------------------------------------------------- reviewed actions (they change a server)
# Same shape as SCRIPTS. They run only when the panel is not read-only, only through services/actions.py.
_UNIT1 = r"^[A-Za-z0-9][A-Za-z0-9._@-]{0,63}\.(service|timer)$"
_B64 = r"^[A-Za-z0-9+/=]{0,200000}$"
_PATH = r"^/(etc/(systemd/system|backhaul|backpack|paqet|nikatel-chisel|sysctl\.d)|usr/local/(bin|sbin)|opt/nikatel-probe-manager|opt/nikatel-probe/xray)/[A-Za-z0-9._-]{1,80}$"
_TNAME = r"^[a-z0-9][a-z0-9-]{0,14}$"

ACTIONS: dict[str, tuple[str, Mapping[str, str]]] = {
    # restart systemd units, one after the other, report each state afterwards
    "restart_units": (r"""for u in @UNITS@; do systemctl restart "$u" 2>/dev/null; sleep 1; echo "UNIT $u $(systemctl is-active "$u" 2>/dev/null || echo unknown)"; done
""", {"UNITS": _UNITS}),

    # write one file (base64 payload) atomically into an allowed directory
    "put_file": (r"""set -e; mkdir -p "$(dirname '@PATH@')"; echo '@B64@' | base64 -d > '@PATH@.nikatel-tmp'; chmod @MODE@ '@PATH@.nikatel-tmp'; mv -f '@PATH@.nikatel-tmp' '@PATH@'; echo "WROTE @PATH@"
""", {"PATH": _PATH, "B64": _B64, "MODE": r"^0[67][0145][0145]$"}),

    # (re)load systemd, enable and restart one unit, report its state
    "unit_enable_restart": (r"""systemctl daemon-reload; systemctl enable '@UNIT@' >/dev/null 2>&1 || true; systemctl restart '@UNIT@' 2>&1 | tail -2; sleep 2; echo "UNIT @UNIT@ $(systemctl is-active '@UNIT@' 2>/dev/null || echo unknown)"
""", {"UNIT": _UNIT1}),

    # who listens on a TCP port of this server: the pid, the process name and the systemd unit it runs in (read-only). Used by the Create tunnel check to tell a DEAD tunnel of ours from a real service.
    "port_owner": (r"""p='@PORT@'; l=$(ss -ltnpH "( sport = :$p )" 2>/dev/null | head -1)
pid=$(echo "$l" | grep -o 'pid=[0-9]*' | head -1 | cut -d= -f2); comm=$(echo "$l" | grep -o 'users:(("[^"]*"' | sed 's/users:(("//; s/"//')
unit=""; [ -n "$pid" ] && unit=$(grep -o '[A-Za-z0-9@._-]*\.service' /proc/$pid/cgroup 2>/dev/null | tail -1)
echo "OWNER ${pid:--} ${comm:--} ${unit:--}"
""", {"PORT": r"^\d{1,5}$"}),

    # remove ONE Iran-side backhaul server unit and its config (a dead tunnel that holds a port a new tunnel needs); the panel has checked that nobody is connected to it
    "remove_backhaul_unit": (r"""u='@UNIT@'; c="${u#backhaul-iran-}"; c="${c%.service}"
systemctl disable --now "$u" >/dev/null 2>&1 || true; rm -f "/etc/systemd/system/$u" "/etc/backhaul/iran-$c.toml"; systemctl daemon-reload; echo "REMOVED $u"
""", {"UNIT": r"^backhaul-iran-(tcp|tcpmux|wsmux|wssmux)-\d{1,5}\.service$"}),

    # Backhaul over TLS WebSocket (wssmux / wss): the server side needs a certificate; a self-signed one, made once per tunnel, is enough (the client does not verify it)
    "backhaul_tls": (r"""set -e; n='@NAME@'; mkdir -p /etc/backhaul
[ -s "/etc/backhaul/$n.crt" ] && [ -s "/etc/backhaul/$n.key" ] || openssl req -x509 -newkey rsa:2048 -nodes -days 3650 -subj "/CN=$n" -keyout "/etc/backhaul/$n.key" -out "/etc/backhaul/$n.crt" >/dev/null 2>&1
chmod 600 "/etc/backhaul/$n.key"; echo "TLS_OK $n"
""", {"NAME": _TNAME}),

    # remove everything a tunnel of ours left on a server: units, scripts, configs, the GRE interface
    "remove_tunnel_files": (r"""n='@NAME@'; p='@PORT@'; tr='@TRANSPORT@'
systemctl disable --now "nikatel-gre-check-$n.timer" >/dev/null 2>&1 || true
systemctl disable --now "nikatel-gre-$n.service" >/dev/null 2>&1 || true
[ -x "/usr/local/sbin/nikatel-gre-down-$n" ] && "/usr/local/sbin/nikatel-gre-down-$n" || true
for u in "backhaul-iran-$tr-$p.service" "backhaul-kharej-$tr-$p.service"; do systemctl disable --now "$u" >/dev/null 2>&1 || true; rm -f "/etc/systemd/system/$u"; done
rm -f "/etc/backhaul/iran-$tr-$p.toml" "/etc/backhaul/kharej-$tr-$p.toml" "/etc/backhaul/$n.crt" "/etc/backhaul/$n.key"
rm -f "/etc/systemd/system/nikatel-gre-$n.service" "/etc/systemd/system/nikatel-gre-check-$n.service" "/etc/systemd/system/nikatel-gre-check-$n.timer"
rm -f "/usr/local/sbin/nikatel-gre-up-$n" "/usr/local/sbin/nikatel-gre-down-$n" "/usr/local/sbin/nikatel-gre-check-$n"
systemctl daemon-reload; ip tunnel del "$n" 2>/dev/null || true; echo "REMOVED $n"
""", {"NAME": _TNAME, "PORT": r"^\d{1,5}$", "TRANSPORT": r"^(tcp|tcpmux|wsmux|wssmux)$"}),

    # kernel tuning for many connections (BBR, fq, big buffers); previous values kept once
    "sysctl_tune": (r"""set -e; mkdir -p /etc/nikatel
[ -f /etc/nikatel/sysctl-before.conf ] || sysctl -a 2>/dev/null | grep -E '^net\.(core\.default_qdisc|ipv4\.tcp_congestion_control|core\.rmem_max|core\.wmem_max|ipv4\.tcp_rmem|ipv4\.tcp_wmem) ' > /etc/nikatel/sysctl-before.conf || true
cat > /etc/sysctl.d/99-nikatel-tunnel.conf <<'EOF'
# Nikatel tunnel tuning (previous values: /etc/nikatel/sysctl-before.conf)
net.core.default_qdisc = fq
net.ipv4.tcp_congestion_control = bbr
net.core.rmem_max = 67108864
net.core.wmem_max = 67108864
net.ipv4.tcp_rmem = 4096 262144 67108864
net.ipv4.tcp_wmem = 4096 262144 67108864
net.ipv4.tcp_mtu_probing = 1
net.ipv4.tcp_slow_start_after_idle = 0
net.ipv4.tcp_fastopen = 3
net.core.somaxconn = 65535
net.ipv4.tcp_max_syn_backlog = 65535
net.core.netdev_max_backlog = 250000
net.ipv4.ip_local_port_range = 1024 65535
net.ipv4.tcp_tw_reuse = 1
net.ipv4.tcp_fin_timeout = 15
fs.file-max = 2097152
net.ipv4.ip_forward = 1
net.ipv4.conf.all.rp_filter = 0
net.ipv4.conf.default.rp_filter = 0
EOF
sysctl -q -p /etc/sysctl.d/99-nikatel-tunnel.conf >/dev/null 2>&1 || true
echo "TUNED cc=$(sysctl -n net.ipv4.tcp_congestion_control 2>/dev/null) qdisc=$(sysctl -n net.core.default_qdisc 2>/dev/null)"
""", {}),

    # a throw-away web server on 127.0.0.1 for 14 s, to prove the data path through a forwarded port
    "data_test_server": (r"""(timeout 14 python3 -m http.server '@PORT@' --bind 127.0.0.1 >/dev/null 2>&1 &); sleep 1; echo started
""", {"PORT": r"^\d{1,5}$"}),

    # the Iran gateway binary was copied to /opt/nikatel-xgw/xray.new by scp: make it the live one
    "xgw_install_bin": (r"""set -e; cd /opt/nikatel-xgw; chmod 755 xray.new; ./xray.new version >/dev/null 2>&1; mv -f xray.new xray; echo "VER $(./xray version | head -1)"
""", {}),

    # apply a gateway config: test it, swap it in, (re)start the unit, open the ports; put the old one back when it does not start
    "xgw_apply": (r"""set -e; cd /opt/nikatel-xgw
echo '@B64@' | base64 -d > config.new.json
if ! ./xray run -test -c config.new.json >/tmp/xgw-test.out 2>&1; then echo "REFUSED $(tail -n 2 /tmp/xgw-test.out | tr '\n' ' ')"; exit 0; fi
cat > /etc/systemd/system/nikatel-xgw.service <<'EOF'
[Unit]
Description=Nikatel Xray gateway
After=network-online.target

[Service]
ExecStart=/opt/nikatel-xgw/xray run -c /opt/nikatel-xgw/config.json
Restart=always
RestartSec=3
LimitNOFILE=1048576

[Install]
WantedBy=multi-user.target
EOF
[ -f config.json ] && cp -f config.json config.prev.json; mv -f config.new.json config.json
for p in @PORTS@; do if iptables -S INPUT 2>/dev/null | head -1 | grep -q DROP; then iptables -C INPUT -p tcp --dport $p -j ACCEPT 2>/dev/null || iptables -I INPUT -p tcp --dport $p -j ACCEPT; fi; done
systemctl daemon-reload; systemctl enable nikatel-xgw >/dev/null 2>&1; systemctl restart nikatel-xgw; sleep 2
if systemctl is-active --quiet nikatel-xgw; then echo "ACTIVE"; else [ -f config.prev.json ] && cp -f config.prev.json config.json && systemctl restart nikatel-xgw; echo "FAILED $(tail -n 3 error.log 2>/dev/null | tr '\n' ' ')"; fi
""", {"B64": _B64, "PORTS": _PORTS}),

    # a throw-away server on 127.0.0.1 that streams zeros for 20 s (the other end of the speed test)
    "speed_sink": (r"""(timeout 20 python3 -c 'import socket,sys
s=socket.socket(); s.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1); s.bind(("127.0.0.1",int(sys.argv[1]))); s.listen(4)
c,_=s.accept(); c.recv(2048); c.sendall(b"HTTP/1.1 200 OK\r\nContent-Type: application/octet-stream\r\nConnection: close\r\n\r\n"); z=b"\0"*65536
try:
    while True: c.sendall(z)
except OSError: pass' '@PORT@' >/dev/null 2>&1 &); sleep 1; echo started
""", {"PORT": r"^\d{1,5}$"}),

    # put a public key into authorized_keys (the operator's own key, so PuTTY / ssh open without a password)
    "add_pubkey": (r"""umask 077; mkdir -p ~/.ssh; touch ~/.ssh/authorized_keys; chmod 700 ~/.ssh; chmod 600 ~/.ssh/authorized_keys
grep -qxF '@PUB@' ~/.ssh/authorized_keys || echo '@PUB@' >> ~/.ssh/authorized_keys; echo ADDED
""", {"PUB": r"^ssh-(ed25519|rsa) [A-Za-z0-9+/=]{20,800}( [A-Za-z0-9@._-]{1,64})?$"}),

    # change the forwarded ports of a backhaul tunnel on the Iran side: the toml is edited, the unit
    # restarted, every port must listen within 10 s, otherwise the old file is put back
    "set_ports": (r"""set -e; CFG='/etc/backhaul/@CFG@.toml'; SVC='backhaul-@CFG@.service'
[ -f "$CFG" ] || { echo "NOCFG"; exit 2; }
cp "$CFG" "$CFG.bak-ports"
echo '@B64@' | base64 -d > /tmp/.nikatel-ports.txt
python3 - "$CFG" <<'PY'
import re, sys
p = sys.argv[1]; s = open(p).read(); new = open("/tmp/.nikatel-ports.txt").read()
s2, n = re.subn(r"ports\s*=\s*\[.*?\]", lambda m: "ports = [\n" + new + "\n]", s, count=1, flags=re.S)
sys.exit(3 if n != 1 else 0) if n != 1 else open(p, "w").write(s2)
PY
rm -f /tmp/.nikatel-ports.txt
systemctl restart "$SVC"
ok=0; for i in 1 2 3 4 5 6 7 8 9 10; do sleep 1; ok=1; for P in @CHECK@; do ss -ltn "( sport = :$P )" 2>/dev/null | grep -q LISTEN || ok=0; done; [ $ok = 1 ] && break; done
if [ $ok = 1 ]; then echo "PORTS_OK"; exit 0; fi
cp "$CFG.bak-ports" "$CFG"; systemctl restart "$SVC"; echo "NOTLISTEN"; exit 4
""", {"CFG": r"^iran-(tcp|tcpmux|wsmux|wssmux)-\d{1,5}$", "B64": _B64, "CHECK": r"^\d{1,5}( \d{1,5}){0,200}$"}),

    # swap a core binary that put_file left next to it (checksum verified there), restart its tunnel units
    "engine_swap": (r"""set -e; NEW='@BIN@.nikatel.new'; [ -f "$NEW" ] || { echo NO_FILE; exit 2; }
S=$(sha256sum "$NEW" | awk '{print $1}'); [ "$S" = '@SHA@' ] || { rm -f "$NEW"; echo BAD_SHA; exit 3; }
chmod 755 "$NEW"; mv -f "$NEW" '@BIN@'
U=$(systemctl list-units --all --no-legend --plain '@UNITS@' 2>/dev/null | awk '{print $1}' | tr '\n' ' ')
for u in $U; do systemctl restart "$u" || true; done; echo "SWAPPED units=$U"
""", {"BIN": r"^/(usr/local/bin|opt/nikatel-xgw)/[A-Za-z0-9._-]{1,40}$", "SHA": r"^[0-9a-f]{64}$", "UNITS": r"^[A-Za-z0-9*._-]{1,40}$"}),

    # reboot the server (the tunnel units come back with systemd)
    "reboot": (r"""echo REBOOTING; (sleep 1; systemctl reboot) >/dev/null 2>&1 &
""", {}),

    # the test sink of a tunnel (foreign server): a tiny python service behind the tunnel's test door
    # that echoes pings and streams bytes both ways so the Iran side can measure the real tunnel
    "sink_install": (r"""set -e; install -d /usr/local/sbin
cat > /usr/local/sbin/nikatel-sink-@NAME@.py <<'__NK_SINK__'
import socket, threading, time, sys
P = int(sys.argv[1]); KEY = sys.argv[2].encode()
s = socket.socket(); s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
s.bind((sys.argv[3] if len(sys.argv) > 3 else "127.0.0.1", P)); s.listen(256)
def h(c):
    try:
        c.settimeout(15); need = len(KEY) + 1; buf = b""
        while len(buf) < need:
            d = c.recv(need - len(buf))
            if not d:
                return
            buf += d
        if buf[:len(KEY)] != KEY:
            return
        m = buf[len(KEY):]
        if m == b"E":
            while True:
                d = c.recv(256)
                if not d:
                    break
                c.sendall(d)
        elif m == b"D":
            b = b"x" * 65536; t = time.time()
            while time.time() - t < 12:
                c.sendall(b)
        elif m == b"U":
            t_first = None; n = 0; n1 = 0; t1 = None; tl = None
            c.settimeout(2.0)
            while True:
                try:
                    d = c.recv(1 << 20)
                except socket.timeout:
                    break
                if not d:
                    break
                now = time.time()
                if t_first is None:
                    t_first = now
                n += len(d); tl = now
                if t1 is None and now - t_first >= 1.0:
                    t1 = now; n1 = n
            overall = (n * 8 / (tl - t_first) / 1e6) if (t_first and tl and tl > t_first) else 0.0
            if t1 and tl and tl > t1 and n > n1:
                rate = (n - n1) * 8 / (tl - t1) / 1e6
                if overall and rate > 1.5 * overall:
                    rate = overall * 1.2
            else:
                rate = overall
            c.sendall(("K%.1f\n" % rate).encode())
    except Exception:
        pass
    finally:
        try:
            c.close()
        except Exception:
            pass
while True:
    c, _ = s.accept()
    threading.Thread(target=h, args=(c,), daemon=True).start()
__NK_SINK__
cat > /etc/systemd/system/nikatel-sink-@NAME@.service <<'__NK_UNIT__'
[Unit]
Description=Nikatel tunnel test sink @NAME@
After=network.target

[Service]
ExecStart=/usr/bin/python3 /usr/local/sbin/nikatel-sink-@NAME@.py @PORT@ @KEY@ @BIND@
Restart=always
RestartSec=3
DynamicUser=yes
NoNewPrivileges=yes

[Install]
WantedBy=multi-user.target
__NK_UNIT__
systemctl daemon-reload; systemctl enable nikatel-sink-@NAME@.service >/dev/null 2>&1; systemctl restart nikatel-sink-@NAME@.service; sleep 1
systemctl is-active --quiet nikatel-sink-@NAME@.service && echo SINK_OK || echo SINK_DOWN
""", {"NAME": _TNAME, "PORT": r"^\d{1,5}$", "KEY": r"^[a-z0-9]{8,32}$", "BIND": r"^(0\.0\.0\.0)?$"}),

    # open / close one port in ufw (only when ufw is active; otherwise a no-op)
    "ufw_allow": (r"""command -v ufw >/dev/null 2>&1 && ufw status 2>/dev/null | head -1 | grep -q active && ufw allow '@PORT@/@PROTO@' >/dev/null 2>&1; echo UFW_DONE
""", {"PORT": r"^\d{1,5}$", "PROTO": r"^(tcp|udp)$"}),

    # Chisel server key (and a self-signed certificate for the TLS variant) on the Iran server
    "chisel_prepare": (r"""set -e; B=/etc/nikatel-chisel; install -d -m 700 $B
[ -f $B/@NAME@.key ] || /usr/local/bin/nikatel-chisel server --keygen $B/@NAME@.key >/dev/null 2>&1
if [ '@TLS@' = 1 ]; then [ -f $B/@NAME@.crt ] || openssl req -x509 -newkey ec -pkeyopt ec_paramgen_curve:prime256v1 -nodes -days 3650 -subj /CN=nikatel -keyout $B/@NAME@.tls.key -out $B/@NAME@.crt >/dev/null 2>&1; fi
chmod 600 $B/@NAME@.*; echo PREPARED
""", {"NAME": _TNAME, "TLS": r"^[01]$"}),

    # remove everything a BackPack / Paqet / Chisel tunnel (and its test sink) left on a server
    # a tunnel TEST that was cut short (a restart of the panel in the middle of it) leaves its temporary units behind: the test-* units of every engine, the sink,
    # the GRE link, and a backhaul unit whose config forwards the reserved test port. Removing them is safe: no real tunnel is named test-* or forwards that port.
    "purge_test_leftovers": (r"""tp='@TESTPORT@'; n=0
for u in $(systemctl list-units --all --no-legend --plain 'backpack-test-*' 'paqet-test-*' 'chisel-test-*' 'nikatel-gre-test-*' 'nikatel-gre-check-test-*' 'nikatel-sink-test-*' 2>/dev/null | awk '{print $1}'); do systemctl disable --now "$u" >/dev/null 2>&1 || true; rm -f "/etc/systemd/system/$u"; n=$((n+1)); done
for f in $(grep -lE "\"$tp(=|\")|\"$tp:" /etc/backhaul/*.toml 2>/dev/null); do b=$(basename "$f" .toml); systemctl disable --now "backhaul-$b.service" >/dev/null 2>&1 || true; rm -f "/etc/systemd/system/backhaul-$b.service" "$f"; n=$((n+1)); done
for s in /usr/local/sbin/nikatel-gre-up-test-* /usr/local/sbin/nikatel-gre-down-test-* /usr/local/sbin/nikatel-gre-check-test-* /usr/local/sbin/nikatel-bpfw-test-* /usr/local/sbin/nikatel-paqet-fw-test-* /usr/local/sbin/nikatel-sink-test-*; do [ -e "$s" ] && rm -f "$s"; done
rm -f /etc/backpack/test-*.toml /etc/backpack/test-*.metrics.json /etc/paqet/test-*.yaml /etc/nikatel-chisel/test-*.key /etc/nikatel-chisel/test-*.crt /etc/nikatel-chisel/test-*.env 2>/dev/null
for i in $(ip -o link show type gre 2>/dev/null | awk -F': ' '{print $2}' | cut -d@ -f1 | grep '^test-'); do ip tunnel del "$i" 2>/dev/null || true; done
systemctl daemon-reload; echo "PURGED $n"
""", {"TESTPORT": r"^\d{4,5}$"}),

    "other_remove": (r"""n='@NAME@'; k='@KIND@'; p='@PORT@'; pr='@PROTO@'
case "$k" in
  backpack) U="backpack-$n.service backpack-$n-kharej.service"; F="/etc/backpack/$n.toml /etc/backpack/$n-kharej.toml /etc/backpack/$n.metrics.json /etc/backpack/$n-kharej.metrics.json";;
  paqet) U="paqet-$n.service"; F="/etc/paqet/$n.yaml";;
  chisel) U="chisel-$n.service chisel-$n-kharej.service"; F="/etc/nikatel-chisel/$n.key /etc/nikatel-chisel/$n.crt /etc/nikatel-chisel/$n.tls.key /etc/nikatel-chisel/$n.srv.env /etc/nikatel-chisel/$n.cli.env";;
esac
for u in $U nikatel-sink-$n.service; do systemctl disable --now "$u" >/dev/null 2>&1 || true; rm -f "/etc/systemd/system/$u"; done
for s in nikatel-bpfw-$n nikatel-paqet-fw-$n; do [ -x "/usr/local/sbin/$s" ] && "/usr/local/sbin/$s" down >/dev/null 2>&1 || true; rm -f "/usr/local/sbin/$s"; done
rm -f $F "/usr/local/sbin/nikatel-sink-$n.py"
[ "$p" != 0 ] && command -v ufw >/dev/null 2>&1 && ufw delete allow "$p/$pr" >/dev/null 2>&1 || true
systemctl daemon-reload; echo "REMOVED $n"
""", {"NAME": _TNAME, "KIND": r"^(backpack|paqet|chisel)$", "PORT": r"^\d{1,5}$", "PROTO": r"^(tcp|udp)$"}),

    # Abuse-Defender (foreign servers only): drop outgoing traffic to a public list of abused ranges,
    # the panel's own servers / tunnel links / DNS / gateway stay whitelisted; refreshed nightly by cron
    "abuse_install": (r"""set -e
export DEBIAN_FRONTEND=noninteractive
command -v iptables >/dev/null 2>&1 || { apt-get update -y >/dev/null 2>&1; apt-get install -y iptables >/dev/null 2>&1; }
command -v curl >/dev/null 2>&1 || { apt-get update -y >/dev/null 2>&1; apt-get install -y curl >/dev/null 2>&1; }
if ! dpkg -s iptables-persistent >/dev/null 2>&1; then
  echo iptables-persistent iptables-persistent/autosave_v4 boolean true | debconf-set-selections 2>/dev/null || true
  echo iptables-persistent iptables-persistent/autosave_v6 boolean true | debconf-set-selections 2>/dev/null || true
  apt-get install -y iptables-persistent >/dev/null 2>&1 || true
fi
URL="@URL@"; WL="@WL@"
tmp=$(mktemp)
curl -fsS --max-time 25 "$URL" -o "$tmp" || { echo "LIST-DOWNLOAD-FAILED"; exit 3; }
[ -s "$tmp" ] || { echo "LIST-EMPTY"; exit 3; }
for c in abuse-defender abuse-defender-custom abuse-defender-whitelist; do iptables -N "$c" 2>/dev/null || true; done
iptables -F abuse-defender-whitelist
for i in $(ip -o link show type gre 2>/dev/null | awk -F': ' '{print $2}' | cut -d@ -f1); do
  case "$i" in gre0|gretap0|erspan0) continue;; esac
  iptables -A abuse-defender-whitelist -o "$i" -j ACCEPT
done
for ip in $WL; do iptables -A abuse-defender-whitelist -d "$ip" -j ACCEPT; done
for ip in $(awk '/^nameserver/{print $2}' /etc/resolv.conf 2>/dev/null | grep -E '^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$'); do iptables -A abuse-defender-whitelist -d "$ip" -j ACCEPT; done
gw=$(ip route show default 2>/dev/null | awk '/default/{print $3; exit}')
[ -n "$gw" ] && iptables -A abuse-defender-whitelist -d "$gw" -j ACCEPT
iptables -A abuse-defender-whitelist -d 169.254.169.254 -j ACCEPT
iptables -F abuse-defender
while read -r r; do
  r=$(echo "$r" | tr -d '\r ')
  echo "$r" | grep -qE '^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+(/[0-9]+)?$' || continue
  iptables -A abuse-defender -d "$r" -j DROP
done < "$tmp"
rm -f "$tmp"
for c in abuse-defender abuse-defender-custom abuse-defender-whitelist; do
  while iptables -D OUTPUT -j "$c" 2>/dev/null; do :; done
  iptables -I OUTPUT 1 -j "$c"
done
cat > /root/abuse-defender-update.sh <<'EOF'
#!/bin/bash
tmp=$(mktemp)
curl -fsS --max-time 25 "@URL@" -o "$tmp" || exit 1
[ -s "$tmp" ] || exit 1
iptables -F abuse-defender
while read -r r; do
  r=$(echo "$r" | tr -d '\r ')
  echo "$r" | grep -qE '^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+(/[0-9]+)?$' || continue
  iptables -A abuse-defender -d "$r" -j DROP
done < "$tmp"
rm -f "$tmp"
mkdir -p /etc/iptables; iptables-save > /etc/iptables/rules.v4
EOF
chmod +x /root/abuse-defender-update.sh
( crontab -l 2>/dev/null | grep -v 'abuse-defender-update.sh'; echo "0 0 * * * /root/abuse-defender-update.sh" ) | crontab -
mkdir -p /etc/iptables; iptables-save > /etc/iptables/rules.v4
echo "ABUSE_OK=$(iptables -S abuse-defender | grep -c DROP)"
""", {"URL": r"^https://raw\.githubusercontent\.com/[A-Za-z0-9._/-]{5,120}$", "WL": r"^(\d{1,3}(\.\d{1,3}){3}( \d{1,3}(\.\d{1,3}){3}){0,200})?$"}),
    "abuse_remove": (r"""for c in abuse-defender abuse-defender-custom abuse-defender-whitelist; do
  while iptables -D OUTPUT -j "$c" 2>/dev/null; do :; done
  iptables -F "$c" 2>/dev/null; iptables -X "$c" 2>/dev/null
done
( crontab -l 2>/dev/null | grep -v 'abuse-defender-update.sh' ) | crontab - 2>/dev/null || true
rm -f /root/abuse-defender-update.sh; mkdir -p /etc/iptables; iptables-save > /etc/iptables/rules.v4 2>/dev/null || true; echo ABUSE_REMOVED
""", {}),

    # shop bot: one UPDATE/INSERT with validated values (statement chosen here, values are quoted by the regexes)
    "bot_sql": (r"""set -a; [ -f '@ENV@' ] && . '@ENV@'; set +a
mysql --batch --raw --default-character-set=utf8mb4 -u "$DB_USER" -p"$DB_PASS" "$DB_NAME" -e @SQL@ 2>&1 | head -c 20000; echo DONE
""", {"ENV": r"^/opt/[A-Za-z0-9._-]{1,40}/db\.env$", "SQL": r"^'(UPDATE|INSERT INTO|DELETE FROM|CREATE TABLE IF NOT EXISTS) [^;`\\]{1,20000}'$"}),

    # shop bot: dump the live database (before an import, or on request)
    "bot_backup": (r"""set -e; set -a; . '@ENV@'; set +a; mkdir -p /opt/nikabot/backups
F=/opt/nikabot/backups/@NAME@-$(date +%Y%m%d-%H%M%S).sql
mysqldump -u "$DB_USER" -p"$DB_PASS" --single-transaction "$DB_NAME" > "$F"; gzip -f "$F"; echo "BACKUP $F.gz $(stat -c %s "$F.gz")"
""", {"ENV": r"^/opt/[A-Za-z0-9._-]{1,40}/db\.env$", "NAME": r"^[a-z-]{1,24}$"}),

    # shop bot: load a dump (base64 of a .sql.gz taken by bot_backup) into the live database; the panel takes a safety dump first
    "bot_restore": (r"""set -e; set -a; . '@ENV@'; set +a
T=$(mktemp /tmp/botrestore.XXXXXX)
trap 'rm -f "$T"' EXIT
base64 -d > "$T" <<'NIKATEL_B64'
@B64@
NIKATEL_B64
gunzip -t "$T"
gunzip < "$T" | mysql -u "$DB_USER" -p"$DB_PASS" "$DB_NAME"
echo "RESTORED $(mysql -N -u "$DB_USER" -p"$DB_PASS" "$DB_NAME" -e "SELECT COUNT(*) FROM information_schema.tables WHERE table_schema = DATABASE()") tables"
""", {"ENV": r"^/opt/[A-Za-z0-9._-]{1,40}/db\.env$", "B64": r"^[A-Za-z0-9+/=]{16,60000000}$"}),

    # shop bot: keep only the last 7 dumps of the automatic backup (auto-*.sql.gz)
    "bot_prune_dumps": (r"""ls -1t /opt/nikabot/backups/auto-*.sql.gz 2>/dev/null | tail -n +8 | xargs -r rm -f; echo PRUNED
""", {}),

    # shop bot: copy every table of the staging database (the real data) over the live one,
    # except the broadcast queues; tables that exist only in one of them are left alone
    "bot_import_stage": (r"""set -e; set -a; . '@ENV@'; set +a
# the staging copy over the live database, table by table and column by column BY NAME (the real database and the bot's own schema differ in the order and the list of columns); the broadcast queues are not copied
TABLES=$(mysql -N -e "SELECT s.table_name FROM information_schema.tables s JOIN information_schema.tables l ON l.table_name=s.table_name AND l.table_schema='$DB_NAME' WHERE s.table_schema='nikabot_stage' AND s.table_name NOT IN ('bc_jobs','bc_messages','send_list')")
[ -n "$TABLES" ] || { echo "NO_STAGE"; exit 1; }
SQL="SET FOREIGN_KEY_CHECKS=0; SET SESSION sql_mode='';"
for t in $TABLES; do
  ROW=$(mysql -N -e "SELECT GROUP_CONCAT(CONCAT('\`',s.column_name,'\`') ORDER BY l.ordinal_position SEPARATOR ','), GROUP_CONCAT(CASE WHEN l.data_type IN ('bigint','int') AND s.data_type IN ('datetime','timestamp') THEN CONCAT('UNIX_TIMESTAMP(\`',s.column_name,'\`)') WHEN l.data_type IN ('datetime','timestamp') AND s.data_type IN ('bigint','int') THEN CONCAT('FROM_UNIXTIME(\`',s.column_name,'\`)') ELSE CONCAT('\`',s.column_name,'\`') END ORDER BY l.ordinal_position SEPARATOR ',') FROM information_schema.columns s JOIN information_schema.columns l ON l.table_schema='$DB_NAME' AND l.table_name=s.table_name AND l.column_name=s.column_name WHERE s.table_schema='nikabot_stage' AND s.table_name='$t'")
  COLS=$(printf '%s' "$ROW" | cut -f1); SEL=$(printf '%s' "$ROW" | cut -f2)
  [ -n "$COLS" ] && SQL="$SQL TRUNCATE TABLE \`$t\`; INSERT INTO \`$t\` ($COLS) SELECT $SEL FROM nikabot_stage.\`$t\`;"
done
mysql -u "$DB_USER" -p"$DB_PASS" "$DB_NAME" -e "$SQL SET FOREIGN_KEY_CHECKS=1;"
# the bot's schema has only primary keys: the panel and the bot join on userid, so these indexes keep every list fast with 15 000 users (best effort)
mysql -u "$DB_USER" -p"$DB_PASS" "$DB_NAME" -e "ALTER TABLE users ADD INDEX IF NOT EXISTS idx_users_userid (userid); ALTER TABLE orders_list ADD INDEX IF NOT EXISTS idx_orders_userid (userid), ADD INDEX IF NOT EXISTS idx_orders_status_id (status, id); ALTER TABLE pays ADD INDEX IF NOT EXISTS idx_pays_user (user_id), ADD INDEX IF NOT EXISTS idx_pays_state_id (state, id);" >/dev/null 2>&1 || true
for t in $TABLES; do echo "TABLE $t $(mysql -N -u "$DB_USER" -p"$DB_PASS" "$DB_NAME" -e "SELECT COUNT(*) FROM \`$t\`")"; done
echo IMPORTED""", {"ENV": r"^/opt/[A-Za-z0-9._-]{1,40}/db\.env$"}),

    # shop bot: the bot's cron on / off - a rename of /etc/cron.d/nikabot (cron ignores names with a dot), nothing is lost
    "bot_cron": (r"""case '@STATE@' in
  on) [ -f /etc/cron.d/nikabot.disabled ] && mv /etc/cron.d/nikabot.disabled /etc/cron.d/nikabot; [ -f /etc/cron.d/nikabot ] && echo "CRON ON" || echo "CRON MISSING";;
  off) [ -f /etc/cron.d/nikabot ] && mv /etc/cron.d/nikabot /etc/cron.d/nikabot.disabled; [ -f /etc/cron.d/nikabot.disabled ] && echo "CRON OFF" || echo "CRON MISSING";;
esac
""", {"STATE": r"^(on|off)$"}),

    # shop bot: write the bot's cron jobs. The file comes base64 from the panel and every line is checked again here: only the five known scripts of the bot, only a plain schedule.
    # At least one active line -> /etc/cron.d/nikabot; none -> the same text is kept as nikabot.disabled (cron ignores names with a dot), nothing is lost.
    "bot_cron_set": (r"""echo '@B64@' | base64 -d > /tmp/nikabot.cron.new || { echo CRON_BAD_B64; exit 1; }
BAD=$(grep -vE '^(#[^`$]*|SHELL=/bin/bash|PATH=[A-Za-z0-9/:._-]+|)$|^(#OFF )?[0-9*/,-]+( [0-9*/,-]+){4} www-data cd /var/www/[A-Za-z0-9._-]+/settings && timeout [0-9]{1,3} php (messagewizwiz|warnusers|rewardReport|tronChecker|dailyjobs)\.php >/dev/null 2>&1$' /tmp/nikabot.cron.new || true)
[ -z "$BAD" ] || { echo CRON_BAD_LINE; unlink /tmp/nikabot.cron.new; exit 1; }
if grep -qE '^[0-9*]' /tmp/nikabot.cron.new; then
  install -m 644 -o root -g root /tmp/nikabot.cron.new /etc/cron.d/nikabot; unlink /etc/cron.d/nikabot.disabled 2>/dev/null || true
else
  install -m 644 -o root -g root /tmp/nikabot.cron.new /etc/cron.d/nikabot.disabled; unlink /etc/cron.d/nikabot 2>/dev/null || true
fi
unlink /tmp/nikabot.cron.new
echo "CRON_SET $(grep -cE '^[0-9*]' /etc/cron.d/nikabot 2>/dev/null || echo 0)"
""", {"B64": r"^[A-Za-z0-9+/=]{1,6000}$"}),
    # shop bot: restart PHP / clear the bot's log files
    "bot_restart_php": (r"""for u in php8.3-fpm php8.2-fpm php-fpm; do systemctl is-active "$u" >/dev/null 2>&1 && systemctl restart "$u" && echo "RESTARTED $u"; done; true
""", {}),
    "bot_clear_logs": (r"""for n in error_log.txt api_trace.txt php_errors.log in_trace.txt; do [ -f '@DIR@'/$n ] && : > '@DIR@'/$n; done; echo CLEARED
""", {"DIR": r"^/var/www/[A-Za-z0-9._-]{1,40}$"}),

    # shop bot: talk to Telegram from the bot server with the bot's own token (the token never leaves it)
    "bot_tg": (r"""TOK=$(grep -oE "botToken *= *'[^']+'" '@DIR@/baseInfo.php' | head -1 | sed "s/.*'\(.*\)'/\1/")
[ -n "$TOK" ] || { echo '{"ok":false,"description":"no bot token in baseInfo.php"}'; exit 0; }
curl -s -m 20 -X POST "https://api.telegram.org/bot$TOK/@METHOD@" -H 'Content-Type: application/json' -d @BODY@ | head -c 4000
""", {"DIR": r"^/var/www/[A-Za-z0-9._-]{1,40}$", "METHOD": r"^(setWebhook|setMyCommands|setChatMenuButton|deleteWebhook)$", "BODY": r"^'\{[^'`\\$]{0,3000}\}'$"}),

    # the same with the JSON body as base64 (messages with any text, keyboards): methods that send or edit, never ones that read the chats
    "bot_tg_json": (r"""TOK=$(grep -oE "botToken *= *'[^']+'" '@DIR@/baseInfo.php' | head -1 | sed "s/.*'\(.*\)'/\1/")
[ -n "$TOK" ] || { echo '{"ok":false,"description":"no bot token in baseInfo.php"}'; exit 0; }
echo '@B64@' | base64 -d | curl -s -m 20 -X POST "https://api.telegram.org/bot$TOK/@METHOD@" -H 'Content-Type: application/json' -d @- | head -c 4000
""", {"DIR": r"^/var/www/[A-Za-z0-9._-]{1,40}$", "METHOD": r"^(sendMessage|editMessageReplyMarkup|editMessageText|setMyCommands|setChatMenuButton|setWebhook|deleteWebhook|pinChatMessage)$", "B64": _B64}),

    # one of the two override files the bot reads (texts, buttons); checked as JSON, owned by the web server user
    "bot_put_json": (r"""set -e; echo '@B64@' | base64 -d > '@PATH@.tmp'
php -r 'exit(json_decode(file_get_contents("@PATH@.tmp"), true) === null ? 1 : 0);' || { unlink '@PATH@.tmp'; echo BAD_JSON; exit 1; }
mv -f '@PATH@.tmp' '@PATH@'; chown www-data:www-data '@PATH@' 2>/dev/null; chmod 0640 '@PATH@'; echo "WROTE @PATH@"
""", {"PATH": r"^/var/www/[A-Za-z0-9._-]{1,40}/settings/(text|button)_overrides\.json$", "B64": _B64}),

    # the Mini App (bot/miniapp in this repository) goes to <bot dir>/app/ one file at a time; PHP is syntax-checked before it replaces the old file
    "bot_miniapp_put": (r"""set -e; mkdir -p "$(dirname '@PATH@')"; echo '@B64@' | base64 -d > '@PATH@.tmp'
case '@PATH@' in *.php) php -l '@PATH@.tmp' >/dev/null 2>&1 || { unlink '@PATH@.tmp'; echo PHP_SYNTAX; exit 1; };; esac
[ -f '@PATH@' ] && cp -f '@PATH@' '@PATH@.prev' || true
mv -f '@PATH@.tmp' '@PATH@'; chown www-data:www-data '@PATH@' 2>/dev/null || true; chmod 0644 '@PATH@'; echo "WROTE @PATH@ $(stat -c %s '@PATH@')"
""", {"PATH": r"^/var/www/[A-Za-z0-9._-]{1,40}/app/(api\.php|index\.html|app\.js|app\.css)$", "B64": _B64}),

    # make the bot read settings/text_overrides.json on top of its values.php: one line in config.php, put there once, checked with php -l
    "bot_texts_hook": (r"""set -e; cd '@DIR@'
grep -q text_overrides.json config.php && { echo HOOKED; exit 0; }
cp config.php "config.php.bak-texts-$(date +%s)"
sed -i '0,/include_once "settings\/values.php";/s//include_once "settings\/values.php"; if(is_file(__DIR__ . "\/settings\/text_overrides.json")){ $__tv = json_decode((string) file_get_contents(__DIR__ . "\/settings\/text_overrides.json"), true); if(is_array($__tv)) $mainValues = array_merge($mainValues, $__tv); }/' config.php
php -l config.php >/dev/null 2>&1 || { cp "$(ls -t config.php.bak-texts-* | head -1)" config.php; echo SYNTAX_ERROR; exit 1; }
grep -q text_overrides.json config.php && echo HOOKED || { echo NOT_HOOKED; exit 1; }
""", {"DIR": r"^/var/www/[A-Za-z0-9._-]{1,40}$"}),

    # probe install: directories, python deps, then start everything
    "probe_prepare": (r"""set -e; mkdir -p /opt/nikatel-probe-manager/{configs,health-state,speed-state,logs} /opt/nikatel-probe/xray /etc/systemd/system
command -v curl >/dev/null 2>&1 || (export DEBIAN_FRONTEND=noninteractive; apt-get update -qq >/dev/null; apt-get install -y -qq curl >/dev/null)
python3 --version; echo PREPARED
""", {}),
    "probe_start": (r"""set -e; cd /opt/nikatel-probe/xray; [ -f xray ] && chmod 755 xray && ./xray version | head -1
cp -f /opt/nikatel-probe-manager/manager.py /usr/local/bin/nikatel-probe; chmod 755 /usr/local/bin/nikatel-probe
systemctl daemon-reload
for u in nikatel-speed-api nikatel-probe-manager nikatel-probe-monitor; do systemctl enable "$u" >/dev/null 2>&1 || true; systemctl restart "$u"; done
systemctl enable --now nikatel-daily-speed.timer >/dev/null 2>&1 || true
sleep 3; for u in nikatel-speed-api nikatel-probe-manager nikatel-probe-monitor; do echo "UNIT $u $(systemctl is-active "$u" 2>/dev/null || echo unknown)"; done
""", {}),

    # run a speed test for one config on the probe (the daily timer does the same for all)
    "probe_speed": (r"""timeout 180 /usr/local/bin/nikatel-probe speed '@NAME@' 2>&1 | tail -n 5
""", {"NAME": r"^[\w .\-]{1,32}$"}),

    # after adding a server: stop torrent trackers (the public script of Heclalava/blockpublictorrent-iptables: a tracker list, DROP rules in a daily cron job, hosts entries).
    # The script writes the cron job but does not run it, so it is run once to make the rules active now. That run applies thousands of iptables rules and takes minutes,
    # so the whole install runs DETACHED on the server (it survives the SSH call and a restart of the panel); the panel asks `install_status` how far it is.
    "install_btblock": (r"""set -e; d=/var/lib/nikatel-install; mkdir -p $d
if pgrep -f 'nikatel-install/btblock.sh' >/dev/null 2>&1; then echo ALREADY_RUNNING; exit 0; fi
rm -f $d/btblock.ok $d/btblock.fail $d/btblock.out
cat > $d/btblock.sh <<'EOS'
set -e; export DEBIAN_FRONTEND=noninteractive
command -v wget >/dev/null 2>&1 || { apt-get update -qq >/dev/null; apt-get install -y -qq wget >/dev/null; }
command -v iptables >/dev/null 2>&1 || { apt-get update -qq >/dev/null; apt-get install -y -qq iptables >/dev/null; }
w=$(mktemp -d); cd "$w"
wget -q https://github.com/Heclalava/blockpublictorrent-iptables/raw/main/bt.sh && chmod +x bt.sh && bash bt.sh >/dev/null 2>&1
[ -x /etc/cron.daily/denypublic ] || { echo BT_FAILED: the script did not create /etc/cron.daily/denypublic; exit 1; }
bash /etc/cron.daily/denypublic >/dev/null 2>&1 || true
echo "BT_OK drop_rules=$(iptables -S 2>/dev/null | grep -c -- '-j DROP')" > /var/lib/nikatel-install/btblock.out
EOS
nohup setsid bash -c 'bash /var/lib/nikatel-install/btblock.sh > /var/lib/nikatel-install/btblock.log 2>&1 && touch /var/lib/nikatel-install/btblock.ok || touch /var/lib/nikatel-install/btblock.fail' >/dev/null 2>&1 &
echo STARTED
""", {}),

    # after adding a server: a Marzban node (the official installer of Gozargah/Marzban-scripts). It reads the panel's certificate from stdin, then asks about REST (Enter = yes)
    # and the two ports (Enter = 62050 / 62051), and ends in `logs -f`, which never returns: it runs in the background and is stopped as soon as the container is up.
    # Detached like the torrent block: Docker alone can take minutes.
    "install_marzban_node": (r"""set -e; d=/var/lib/nikatel-install; mkdir -p $d
[ "$(id -u)" = 0 ] || { echo NOT_ROOT; exit 1; }
[ -d /opt/marzban-node ] && { echo MZNODE_EXISTS; exit 0; }
if pgrep -f 'nikatel-install/mznode.sh' >/dev/null 2>&1; then echo ALREADY_RUNNING; exit 0; fi
rm -f $d/mznode.ok $d/mznode.fail $d/mznode.out
echo '@B64@' | base64 -d | sed '/^[[:space:]]*$/d' > $d/mznode.cert; chmod 600 $d/mznode.cert
grep -q 'BEGIN CERTIFICATE' $d/mznode.cert || { echo BAD_CERT; exit 1; }
cat > $d/mznode.sh <<'EOS'
set -e; export DEBIAN_FRONTEND=noninteractive
d=/var/lib/nikatel-install; cd $d
command -v curl >/dev/null 2>&1 || { apt-get update -qq >/dev/null; apt-get install -y -qq curl >/dev/null; }
curl -fsSL https://github.com/Gozargah/Marzban-scripts/raw/master/marzban-node.sh -o mz.sh
[ -s mz.sh ] || { echo DOWNLOAD_FAILED; exit 1; }
( cat mznode.cert; printf '\n\n\n\n\n' ) | setsid bash mz.sh install > mz-install.log 2>&1 &
ok=0
for i in $(seq 1 200); do sleep 3; if docker ps --format '{{.Names}} {{.Status}}' 2>/dev/null | grep -qi 'marzban-node.* Up'; then ok=1; break; fi; done
sleep 3
pkill -f 'docker.* logs' 2>/dev/null || true; pkill -f 'bash mz.sh' 2>/dev/null || true
if [ "$ok" = 1 ]; then echo "MZNODE_OK $(docker ps --format '{{.Names}} {{.Status}}' | grep -i marzban-node | head -1)" > mznode.out; else echo MZNODE_FAILED; tail -n 12 mz-install.log; exit 1; fi
shred -u mznode.cert 2>/dev/null || true
EOS
nohup setsid bash -c 'bash /var/lib/nikatel-install/mznode.sh > /var/lib/nikatel-install/mznode.log 2>&1 && touch /var/lib/nikatel-install/mznode.ok || touch /var/lib/nikatel-install/mznode.fail' >/dev/null 2>&1 &
echo STARTED
""", {"B64": _B64}),

    # change the login password of the SSH user (the payload is base64 of "user:password"; it never appears in the logs). The panel logs in with its key, so it is not affected.
    "set_password": (r"""set -e; u=$(echo '@B64@' | base64 -d | cut -d: -f1); [ "$u" = '@USER@' ] || { echo BAD_USER; exit 1; }
echo '@B64@' | base64 -d | chpasswd && echo PASSWORD_SET
""", {"B64": _B64, "USER": r"^[a-z_][a-z0-9_-]{0,31}$"}),

    # the gateway directory must exist before the binary is copied in
    "xgw_mkdir": (r"""mkdir -p /opt/nikatel-xgw && chmod 755 /opt/nikatel-xgw && echo OK
""", {}),

    # remove the gateway completely (the list in the panel stays)
    "xgw_remove": (r"""systemctl disable --now nikatel-xgw >/dev/null 2>&1 || true; rm -f /etc/systemd/system/nikatel-xgw.service; systemctl daemon-reload; rm -rf /opt/nikatel-xgw; echo REMOVED
""", {}),
}


def run_action(node: Node, name: str, params: Mapping[str, str] | None = None, timeout: int = 90) -> str:
    """Run one reviewed action. Refused while the panel is read-only."""
    if get_settings().read_only:
        raise SSHError("the panel is in read-only mode: set NIKATEL_READ_ONLY=false on the server to allow repairs")

    if name not in ACTIONS:
        raise ValueError(f"unknown action {name!r}")

    script, rules = ACTIONS[name]
    params = dict(params or {})

    for key, rule in rules.items():
        value = str(params.get(key, ""))

        if not re.match(rule, value):
            raise ValueError(f"bad value for {key}: {value[:40]!r}")

        script = script.replace(f"@{key}@", value)

    log.info("action %s on %s params=%s", name, node.host, {k: ("…" if k in ("B64", "KEY", "TOKEN") else v) for k, v in params.items()})   # payloads (files, passwords, keys) stay out of the log
    return _exec(node, script, timeout)


def render_action(name: str, params: Mapping[str, str]) -> str:
    """The exact script an action would run - for dry runs. Validates like run_action, touches nothing."""
    if name not in ACTIONS:
        raise ValueError(f"unknown action {name!r}")

    script, rules = ACTIONS[name]

    for key, rule in rules.items():
        value = str(params.get(key, ""))

        if not re.match(rule, value):
            raise ValueError(f"bad value for {key}: {value[:40]!r}")

        script = script.replace(f"@{key}@", value)

    return script.strip()


def run_raw(node: Node, script: str, timeout: int = 60) -> str:
    """Arbitrary command - refused while the panel is read-only. Changing anything on a server
    goes through a named, reviewed action, never through this directly from an API handler."""
    if get_settings().read_only:
        raise SSHError("the panel is in read-only mode: no commands that change servers")

    log.info("raw command on %s: %s", node.host, script[:120].replace("\n", " "))
    return _exec(node, script, timeout)

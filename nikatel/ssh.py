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
""", {}),

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
echo "NOW $(date +%s)"
""", {"UNITS": _UNITS, "IFACE": r"^([A-Za-z0-9][A-Za-z0-9._-]{0,14})?$", "PEER": r"^(\d{1,3}(\.\d{1,3}){3})?$", "PORTS": _PORTS}),

    # is the server there at all, and what is it
    "ping": (r"""echo "HOST=$(hostname) $(uname -sr)" """, {}),

    # the shop bot (PHP + MariaDB on the bot server): services, cron, counts, states, logs - as JSON.
    # DB credentials are read on that server from its own env file; nothing secret leaves it.
    "bot_info": (r"""set -a; [ -f '@ENV@' ] && . '@ENV@'; set +a
Q() { mysql --batch --raw -N --default-character-set=utf8mb4 -u "$DB_USER" -p"$DB_PASS" "$DB_NAME" -e "$1" 2>/dev/null; }
echo "INSTALLED $([ -d '@DIR@' ] && [ -n "$DB_PASS" ] && echo 1 || echo 0)"
for u in php8.3-fpm php8.2-fpm php-fpm mariadb mysql caddy nginx; do echo "UNIT $u $(systemctl is-active "$u" 2>/dev/null || echo unknown)"; done
echo "CRON $([ -f /etc/cron.d/nikabot ] && grep -c '^[^#]' /etc/cron.d/nikabot || echo 0)"
echo "COUNTS $(Q "SELECT CONCAT((SELECT COUNT(*) FROM users),' ',(SELECT COUNT(*) FROM orders_list),' ',(SELECT COUNT(*) FROM pays WHERE state='pending'),' ',(SELECT COUNT(*) FROM pays WHERE state IN ('confirmed','approved')),' ',(SELECT COUNT(*) FROM server_plans),' ',(SELECT COUNT(*) FROM server_config))")"
echo "STAGE $(mysql -N -e "SELECT COUNT(*) FROM nikabot_stage.users" 2>/dev/null || echo -)"
echo "STATES $(Q "SELECT value FROM setting WHERE type='BOT_STATES' LIMIT 1" | head -c 4000)"
echo "PANELS $(Q "SELECT CONCAT(id,'|',type,'|',SUBSTRING_INDEX(SUBSTRING_INDEX(panel_url,'/',3),'/',-1)) FROM server_config" | tr '\n' ' ')"
echo "TODAY $(Q "SELECT CONCAT(COUNT(*),' ',COALESCE(SUM(amount),0)) FROM orders_list WHERE date >= UNIX_TIMESTAMP(CURDATE())")"
echo "WEEK $(Q "SELECT CONCAT(COUNT(*),' ',COALESCE(SUM(amount),0)) FROM orders_list WHERE date >= UNIX_TIMESTAMP()-7*86400")"
echo "MONTH $(Q "SELECT CONCAT(COUNT(*),' ',COALESCE(SUM(amount),0)) FROM orders_list WHERE date >= UNIX_TIMESTAMP()-30*86400")"
echo "NEWUSERS $(Q "SELECT CONCAT((SELECT COUNT(*) FROM users WHERE date >= UNIX_TIMESTAMP(CURDATE())),' ',(SELECT COUNT(*) FROM users WHERE date >= UNIX_TIMESTAMP()-7*86400))")"
echo "BACKUPS $(ls -1 /opt/nikabot/backups 2>/dev/null | tail -n 5 | tr '\n' ' ')"
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
mysql --batch --raw --default-character-set=utf8mb4 -u "$DB_USER" -p"$DB_PASS" "$DB_NAME" -e @SQL@ 2>&1 | head -c 200000
""", {"ENV": r"^/opt/[A-Za-z0-9._-]{1,40}/db\.env$", "SQL": r"^'(SELECT|SHOW|DESCRIBE)\b[^;'`\\]{0,1500}'$"}),

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
    "gre_pings": (r"""ping -c 30 -i 0.2 -W 1 -I '@IFACE@' '@PEER@' 2>&1 | grep -E 'time=|packets transmitted'
""", {"IFACE": r"^[A-Za-z0-9][A-Za-z0-9._-]{0,14}$", "PEER": _IPV4}),

    # pull from a sink through a forwarded port for 10 s; bytes per second
    "speed_pull": (r"""echo "SPEED=$(curl -s -m 12 -o /dev/null -w '%{speed_download}' 'http://127.0.0.1:@PORT@/' 2>/dev/null || echo 0)"
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
    s = get_settings()
    return [
        "ssh", "-i", str(s.ssh_key), "-p", str(node.port),
        "-o", "BatchMode=yes",
        "-o", "StrictHostKeyChecking=accept-new",
        "-o", f"UserKnownHostsFile={s.known_hosts}",
        "-o", f"ConnectTimeout={s.ssh_connect_timeout}",
        "-o", "ServerAliveInterval=10",
        "-o", "LogLevel=ERROR",
        f"{node.user}@{node.host}",
    ]


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
            tail = "ssh connection lost (" + (re.search(r"(Timeout, server[^|]*|Connection (reset|closed|timed out)[^|]*|kex_exchange_identification[^|]*|Broken pipe)", out) or [None, "network"])[0].strip() + ")"

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
_PATH = r"^/(etc/(systemd/system|backhaul|sysctl\.d)|usr/local/(bin|sbin)|opt/nikatel-probe-manager|opt/nikatel-probe/xray)/[A-Za-z0-9._-]{1,80}$"
_TNAME = r"^[a-z0-9][a-z0-9-]{0,14}$"

ACTIONS: dict[str, tuple[str, Mapping[str, str]]] = {
    # restart systemd units, one after the other, report each state afterwards
    "restart_units": (r"""for u in @UNITS@; do systemctl restart "$u" 2>/dev/null; sleep 1; echo "UNIT $u $(systemctl is-active "$u" 2>/dev/null || echo unknown)"; done
""", {"UNITS": _UNITS}),

    # write one file (base64 payload) atomically into an allowed directory
    "put_file": (r"""set -e; mkdir -p "$(dirname '@PATH@')"; echo '@B64@' | base64 -d > '@PATH@.nikatel-tmp'; chmod @MODE@ '@PATH@.nikatel-tmp'; mv -f '@PATH@.nikatel-tmp' '@PATH@'; echo "WROTE @PATH@"
""", {"PATH": _PATH, "B64": _B64, "MODE": r"^0[67][04][04]$"}),

    # (re)load systemd, enable and restart one unit, report its state
    "unit_enable_restart": (r"""systemctl daemon-reload; systemctl enable '@UNIT@' >/dev/null 2>&1 || true; systemctl restart '@UNIT@' 2>&1 | tail -2; sleep 2; echo "UNIT @UNIT@ $(systemctl is-active '@UNIT@' 2>/dev/null || echo unknown)"
""", {"UNIT": _UNIT1}),

    # remove everything a tunnel of ours left on a server: units, scripts, configs, the GRE interface
    "remove_tunnel_files": (r"""n='@NAME@'; p='@PORT@'; tr='@TRANSPORT@'
systemctl disable --now "nikatel-gre-check-$n.timer" >/dev/null 2>&1 || true
systemctl disable --now "nikatel-gre-$n.service" >/dev/null 2>&1 || true
[ -x "/usr/local/sbin/nikatel-gre-down-$n" ] && "/usr/local/sbin/nikatel-gre-down-$n" || true
for u in "backhaul-iran-$tr-$p.service" "backhaul-kharej-$tr-$p.service"; do systemctl disable --now "$u" >/dev/null 2>&1 || true; rm -f "/etc/systemd/system/$u"; done
rm -f "/etc/backhaul/iran-$tr-$p.toml" "/etc/backhaul/kharej-$tr-$p.toml"
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

    # shop bot: one UPDATE/INSERT with validated values (statement chosen here, values are quoted by the regexes)
    "bot_sql": (r"""set -a; [ -f '@ENV@' ] && . '@ENV@'; set +a
mysql --batch --raw --default-character-set=utf8mb4 -u "$DB_USER" -p"$DB_PASS" "$DB_NAME" -e @SQL@ 2>&1 | head -c 20000; echo DONE
""", {"ENV": r"^/opt/[A-Za-z0-9._-]{1,40}/db\.env$", "SQL": r"^'(UPDATE|INSERT INTO|DELETE FROM|CREATE TABLE IF NOT EXISTS) [^;`\\]{1,6000}'$"}),

    # shop bot: dump the live database (before an import, or on request)
    "bot_backup": (r"""set -e; set -a; . '@ENV@'; set +a; mkdir -p /opt/nikabot/backups
F=/opt/nikabot/backups/@NAME@-$(date +%Y%m%d-%H%M%S).sql
mysqldump -u "$DB_USER" -p"$DB_PASS" --single-transaction "$DB_NAME" > "$F"; gzip -f "$F"; echo "BACKUP $F.gz $(stat -c %s "$F.gz")"
""", {"ENV": r"^/opt/[A-Za-z0-9._-]{1,40}/db\.env$", "NAME": r"^[a-z-]{1,24}$"}),

    # shop bot: copy every table of the staging database (the real data) over the live one,
    # except the broadcast queues; tables that exist only in one of them are left alone
    "bot_import_stage": (r"""set -e; set -a; . '@ENV@'; set +a
TABLES=$(mysql -N -e "SELECT s.table_name FROM information_schema.tables s JOIN information_schema.tables l ON l.table_name=s.table_name AND l.table_schema='$DB_NAME' WHERE s.table_schema='nikabot_stage' AND s.table_name NOT IN ('bc_jobs','bc_messages')")
[ -n "$TABLES" ] || { echo "NO_STAGE"; exit 1; }
mysql -u "$DB_USER" -p"$DB_PASS" "$DB_NAME" -e "SET FOREIGN_KEY_CHECKS=0; $(for t in $TABLES; do echo "TRUNCATE TABLE \`$t\`; INSERT INTO \`$t\` SELECT * FROM nikabot_stage.\`$t\`;"; done) SET FOREIGN_KEY_CHECKS=1;"
for t in $TABLES; do echo "TABLE $t $(mysql -N -u "$DB_USER" -p"$DB_PASS" "$DB_NAME" -e "SELECT COUNT(*) FROM \`$t\`")"; done
echo IMPORTED
""", {"ENV": r"^/opt/[A-Za-z0-9._-]{1,40}/db\.env$"}),

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

    log.info("action %s on %s params=%s", name, node.host, dict(params))
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

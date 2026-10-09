#!/usr/bin/env bash
# Nikatel panel - one-line installer (Debian/Ubuntu, run as root).
#
#   curl -fsSL https://raw.githubusercontent.com/hamed67vi/nikatel-panel/main/install.sh | bash
#
# Asks a few questions (or takes them from flags / env), installs what is missing, clones the
# source, creates the service and starts the panel. Running it again upgrades in place.
#
#   --yes                  no questions, defaults for everything
#   --port 9443            https port
#   --domain panel.example.com   use Let's Encrypt (needs port 80 reachable) instead of a self-signed cert
#   --password '...'       admin password (default: generated and printed once)
#   --branch main          git branch
#   --read-only true|false start in read-only mode (default true)
set -euo pipefail

REPO="${NIKATEL_REPO:-https://github.com/hamed67vi/nikatel-panel.git}"
BRANCH="${NIKATEL_BRANCH:-main}"
SRC=/opt/nikatel/src
VENV=/opt/nikatel/venv
DATA=/var/lib/nikatel
ETC=/etc/nikatel
YES=0; PORT=""; DOMAIN=""; PASSWORD=""; READ_ONLY=""

while [ $# -gt 0 ]; do
    case "$1" in
        --yes|-y) YES=1; shift ;;
        --port) PORT="$2"; shift 2 ;;
        --domain) DOMAIN="$2"; shift 2 ;;
        --password) PASSWORD="$2"; shift 2 ;;
        --branch) BRANCH="$2"; shift 2 ;;
        --read-only) READ_ONLY="$2"; shift 2 ;;
        *) echo "unknown option: $1"; exit 1 ;;
    esac
done

say()  { printf '\033[1;36m==\033[0m %s\n' "$*"; }
die()  { printf '\033[1;31m!!\033[0m %s\n' "$*" >&2; exit 1; }
ask()  { # ask VAR "question" "default"
    local var="$1" q="$2" def="$3" ans=""
    if [ "$YES" = 1 ] || [ ! -r /dev/tty ]; then eval "$var=\"\${$var:-$def}\""; return; fi
    [ -n "$(eval echo "\${$var}")" ] && return
    read -r -p "$q [$def]: " ans < /dev/tty || true
    eval "$var=\"\${ans:-$def}\""
}

[ "$(id -u)" = 0 ] || die "run as root (sudo -i)"
command -v apt-get >/dev/null || die "this installer supports Debian/Ubuntu"

say "Nikatel panel installer"
ask PORT      "HTTPS port" "9443"
ask DOMAIN    "Domain for Let's Encrypt (empty = self-signed certificate on the server IP)" ""
ask READ_ONLY "Start in read-only mode (the panel only looks at servers, changes nothing)" "true"
[ "$YES" = 1 ] || [ -n "$PASSWORD" ] || { read -r -s -p "Admin password (empty = generate one) : " PASSWORD < /dev/tty || true; echo; }

say "packages"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq >/dev/null
apt-get install -y -qq git python3 python3-venv python3-pip openssh-client openssl curl rsync sshpass >/dev/null

say "source ($BRANCH)"
if [ -d "$SRC/.git" ]; then
    git -C "$SRC" fetch -q origin "$BRANCH" && git -C "$SRC" checkout -q "$BRANCH" && git -C "$SRC" pull -q --ff-only
else
    mkdir -p /opt/nikatel && git clone -q --branch "$BRANCH" "$REPO" "$SRC"
fi

say "python environment"
[ -d "$VENV" ] || python3 -m venv "$VENV"
"$VENV/bin/pip" install -q --upgrade pip
"$VENV/bin/pip" install -q -e "$SRC"

say "directories and config"
mkdir -p "$DATA"/{data,backups,logs} "$ETC"
chmod 700 "$DATA" "$DATA/data"

if [ ! -f "$ETC/nikatel.env" ]; then
    cat > "$ETC/nikatel.env" <<EOF
NIKATEL_LISTEN_HOST=0.0.0.0
NIKATEL_LISTEN_PORT=$PORT
NIKATEL_LOG_LEVEL=INFO
NIKATEL_READ_ONLY=$READ_ONLY
EOF
    chmod 600 "$ETC/nikatel.env"
fi

say "ssh key for the managed servers"
if [ ! -f /root/.ssh/nikatel_v2_node ]; then
    mkdir -p /root/.ssh && chmod 700 /root/.ssh
    ssh-keygen -q -t ed25519 -N "" -C "nikatel-panel@$(hostname)" -f /root/.ssh/nikatel_v2_node
fi

say "tls"
if [ -n "$DOMAIN" ]; then
    if [ ! -f "$ETC/tls.key" ]; then
        apt-get install -y -qq certbot >/dev/null
        certbot certonly -q --standalone --non-interactive --agree-tos --register-unsafely-without-email -d "$DOMAIN" \
            || die "Let's Encrypt failed - is port 80 open and does $DOMAIN point here? Re-run without --domain for a self-signed certificate."
        ln -sf "/etc/letsencrypt/live/$DOMAIN/fullchain.pem" "$ETC/tls.crt"
        ln -sf "/etc/letsencrypt/live/$DOMAIN/privkey.pem" "$ETC/tls.key"
        printf '#!/bin/sh\nsystemctl restart nikatel\n' > /etc/letsencrypt/renewal-hooks/deploy/nikatel.sh && chmod +x /etc/letsencrypt/renewal-hooks/deploy/nikatel.sh
    fi
elif [ ! -f "$ETC/tls.key" ]; then
    IP=$(curl -fsS -m 5 https://api.ipify.org 2>/dev/null || hostname -I | awk '{print $1}')
    openssl req -x509 -newkey ec -pkeyopt ec_paramgen_curve:prime256v1 -nodes -days 3650 -subj "/CN=nikatel-panel" \
        -addext "subjectAltName=IP:${IP}" -keyout "$ETC/tls.key" -out "$ETC/tls.crt" 2>/dev/null
    chmod 600 "$ETC/tls.key"
fi

say "service"
cp "$SRC/scripts/nikatel.service" /etc/systemd/system/nikatel.service
systemctl daemon-reload
systemctl enable -q nikatel
systemctl restart nikatel

for _ in 1 2 3 4 5 6 7 8 9 10; do
    sleep 1
    curl -fsk -m 3 "https://127.0.0.1:$PORT/api/health" >/dev/null 2>&1 && break
done
curl -fsk -m 5 "https://127.0.0.1:$PORT/api/health" >/dev/null 2>&1 || { journalctl -u nikatel -n 30 --no-pager; die "the panel did not start"; }

if [ -n "$PASSWORD" ]; then
    "$VENV/bin/nikatel" set-password "$PASSWORD" >/dev/null
    rm -f "$DATA/data/initial-admin-password.txt"
fi

HOST_SHOWN="${DOMAIN:-$(curl -fsS -m 5 https://api.ipify.org 2>/dev/null || hostname -I | awk '{print $1}')}"
echo
say "done. Nikatel panel is running."
echo "   URL       : https://$HOST_SHOWN:$PORT"
echo "   user      : admin"
if [ -z "$PASSWORD" ] && [ -f "$DATA/data/initial-admin-password.txt" ]; then
    echo "   password  : $(cat "$DATA/data/initial-admin-password.txt")   (shown once - change it in the panel)"
fi
echo "   logs      : journalctl -u nikatel -f"
echo "   upgrade   : re-run this installer, or: cd $SRC && git pull && bash scripts/deploy.sh"
echo
echo "   Add this public key to ~/.ssh/authorized_keys on every server the panel should see:"
echo "   $(cat /root/.ssh/nikatel_v2_node.pub)"
echo
echo "   Import the inventory of the old panel:  $VENV/bin/nikatel import /path/to/json-files"

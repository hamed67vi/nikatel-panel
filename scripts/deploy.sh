#!/usr/bin/env bash
# Deploy the checked-out source: secrets check → tests → install → restart → health → rollback on failure.
#   bash scripts/deploy.sh            # deploy HEAD
#   bash scripts/deploy.sh --rollback # go back to the last commit that passed the health check
set -euo pipefail
SRC="$(cd "$(dirname "$0")/.." && pwd)"
VENV=/opt/nikatel/venv
STATE=/var/lib/nikatel/last-good-commit
PORT=$(grep -oE '^NIKATEL_LISTEN_PORT=[0-9]+' /etc/nikatel/nikatel.env 2>/dev/null | cut -d= -f2); PORT=${PORT:-9443}
cd "$SRC"

health() { for _ in 1 2 3 4 5 6 7 8; do sleep 1; curl -sk -m 5 "https://127.0.0.1:$PORT/api/health" 2>/dev/null | grep -q '"ok":true' && return 0; done; return 1; }
install() {
    "$VENV/bin/pip" install -q -e "$SRC[dev]"
    # the unit file travels with the source: install it when it changed
    if ! cmp -s scripts/nikatel.service /etc/systemd/system/nikatel.service; then cp scripts/nikatel.service /etc/systemd/system/nikatel.service; systemctl daemon-reload; echo "unit file updated"; fi
    systemctl restart nikatel
}

if [ "${1:-}" = "--rollback" ]; then
    [ -f "$STATE" ] || { echo "no known good commit"; exit 1; }
    git checkout -q "$(cat "$STATE")"; install
    health && echo "rolled back to $(git rev-parse --short HEAD)" || { echo "still unhealthy"; exit 1; }
    exit 0
fi

PREV=$(cat "$STATE" 2>/dev/null || true)
echo "== no secrets in the repo";  bash scripts/check_no_secrets.sh
echo "== tests";                    "$VENV/bin/python" -m pytest -q
echo "== install $(git rev-parse --short HEAD)"; install

if health; then
    git rev-parse HEAD > "$STATE"; echo "DEPLOY OK $(git rev-parse --short HEAD)"
else
    echo "HEALTH CHECK FAILED"; journalctl -u nikatel -n 20 --no-pager
    if [ -n "$PREV" ]; then echo "rolling back to ${PREV:0:7}"; git checkout -q "$PREV"; install; fi
    exit 1
fi

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

health() { for _ in $(seq 1 45); do sleep 2; curl -sk -m 5 "https://127.0.0.1:$PORT/api/health" 2>/dev/null | grep -q '"ok":true' && return 0; done; return 1; }      # a graceful stop waits up to 40 s for running checks: the answer may take a minute
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
if [ "${FORCE_TESTS:-0}" != 1 ] && git describe --exact-match --tags HEAD >/dev/null 2>&1; then
    echo "== tests skipped: $(git describe --exact-match --tags HEAD) is a released version (the whole suite ran before it was published; FORCE_TESTS=1 runs it here too)"
else
    echo "== tests";                "$VENV/bin/python" -m pytest -q
fi
echo "== migrations on a copy of the live database"; "$VENV/bin/python" scripts/migrate_check.py /var/lib/nikatel/data
echo "== install $(git rev-parse --short HEAD)"; install

if health; then
    git rev-parse HEAD > "$STATE"; echo "DEPLOY OK $(git rev-parse --short HEAD)"
else
    echo "HEALTH CHECK FAILED"; journalctl -u nikatel -n 20 --no-pager
    if [ -n "$PREV" ]; then
        echo "rolling back to ${PREV:0:7}"
        # the files of the last good commit come back into the working tree (HEAD stays on the branch, so the next deploy commits the fix on top);
        # uncommitted work is put aside first: git stash pop brings it back
        if [ -n "$(git status --porcelain)" ]; then git stash push -u -q -m "deploy failed $(date -u +%FT%TZ)" && echo "uncommitted work put aside (git stash pop brings it back)"; fi
        git checkout -q "$PREV" -- . && install
        health && echo "rolled back: the panel runs ${PREV:0:7} again" || { echo "STILL DOWN after the rollback: journalctl -u nikatel -n 50"; }
    fi
    exit 1
fi

#!/usr/bin/env bash
# Deploy the checked-out source: secrets check → compile → libraries (only when they changed) → migrations and imports on a copy of the live data → restart → health → rollback on failure.
#   bash scripts/deploy.sh                  # deploy HEAD
#   bash scripts/deploy.sh --rollback       # go back to the last commit that passed the health check
#   FORCE_TESTS=1 bash scripts/deploy.sh    # run the whole test suite here too
# The tests do NOT run here by default (the owner, 2026-10-17: "the update is very slow"): the whole suite runs on the build server before a version is published, and running it again on the panel's own
# server took ten minutes of two cores that also watch the tunnels. What only THIS machine can answer is checked: the new code compiles, imports with the libraries installed here and migrates a COPY of the live
# database, and the panel answers after the restart (if not, the last good commit comes back). About fifteen seconds in all.
# `step` keeps $PROGRESS - one small file - up to date: the Update button of the panel shows it (nikatel/services/updater.py), so a page that is refreshed, or the restart of the panel itself, loses nothing.
# The environment variables VENV, STATE_DIR, ENV_FILE, UNIT, SYSTEMCTL, HEALTH_TRIES and HEALTH_SLEEP are for the tests of this script (tests/test_deploy_script.py).
set -euo pipefail
SRC="$(cd "$(dirname "$0")/.." && pwd)"
VENV=${VENV:-/opt/nikatel/venv}
STATE_DIR=${STATE_DIR:-/var/lib/nikatel}
STATE=$STATE_DIR/last-good-commit
PROGRESS=$STATE_DIR/update-progress.json
ENV_FILE=${ENV_FILE:-/etc/nikatel/nikatel.env}
UNIT=${UNIT:-/etc/systemd/system/nikatel.service}
SYSTEMCTL=${SYSTEMCTL:-systemctl}
PORT=$(grep -oE '^NIKATEL_LISTEN_PORT=[0-9]+' "$ENV_FILE" 2>/dev/null | cut -d= -f2 || true); PORT=${PORT:-9443}
cd "$SRC"
RUN=${UPDATE_RUN:-$(date +%s)}; FROM=${UPDATE_FROM:-}
TO=$(sed -n 's/^__version__ = "\([^"]*\)".*/\1/p' nikatel/__init__.py 2>/dev/null | head -n 1 || true)
STEP=""; WHY=""; PREV=$(cat "$STATE" 2>/dev/null || true)

step() {      # step <name> [reason]: where the run is, for the page (GET /api/system/update): the file is replaced in one move, so a reader never sees half of it
    STEP=$1
    { printf '{"run":%s,"step":"%s","at":%s,"from":"%s","to":"%s","reason":"%s"}\n' "$RUN" "$1" "$(date +%s)" "$FROM" "$TO" "${2:-}" > "$PROGRESS.tmp" && mv -f "$PROGRESS.tmp" "$PROGRESS"; } 2>/dev/null || true
}
health() { for _ in $(seq 1 "${HEALTH_TRIES:-45}"); do sleep "${HEALTH_SLEEP:-2}"; curl -sk -m 5 "https://127.0.0.1:$PORT/api/health" 2>/dev/null | grep -q '"ok":true' && return 0; done; return 1; }      # a graceful stop waits up to 40 s for running checks: the answer may take a minute
deps_changed() { [ -z "${1:-}" ] || ! git diff --quiet "$1" "$2" -- pyproject.toml 2>/dev/null; }      # true when the last good commit is not known, or pyproject.toml is not the same in the two commits
deps() { echo "== libraries: pyproject.toml changed, installing"; "$VENV/bin/pip" install -q -e "$SRC[dev]"; }
unit_file() {      # the unit file travels with the source: install it when it changed
    if ! cmp -s scripts/nikatel.service "$UNIT"; then cp scripts/nikatel.service "$UNIT"; "$SYSTEMCTL" daemon-reload; echo "unit file updated"; fi
}
back_to_last_good() {      # the files AND the branch go back to the last good commit, so the next update is a plain fast-forward again; uncommitted work is put aside first: git stash pop brings it back
    if [ -n "$(git status --porcelain)" ]; then git stash push -u -q -m "deploy failed $(date -u +%FT%TZ)" && echo "uncommitted work put aside (git stash pop brings it back)"; fi
    git reset -q --hard "$1"
    find "$SRC/nikatel" -name __pycache__ -type d -prune -exec rm -rf {} + 2>/dev/null || true      # compiled files of the version that was put away must not be taken for the old one: a file of the same size rewritten in the same second looks unchanged to python (found for real: the version printed was the failed one)
}
finish() {      # any stop that was not planned: say it for the page, and while the panel still runs the old files put them back, so that a reboot does not start a version that failed its checks
    local rc=$?
    trap - EXIT
    if [ "$rc" -ne 0 ] && [ "$STEP" != failed ]; then
        echo "UPDATE STOPPED at '$STEP': ${WHY:-unknown}"
        if { [ "$STEP" = checks ] || [ "$STEP" = install ]; } && [ -n "$PREV" ] && git cat-file -e "$PREV^{commit}" 2>/dev/null; then
            back_to_last_good "$PREV" && echo "rolled back: the files of ${PREV:0:7} are back, the panel was not restarted" || true
        fi
        step failed "${WHY:-the update stopped}"
    fi
    exit "$rc"
}

if [ "${1:-}" = "--rollback" ]; then
    [ -f "$STATE" ] || { echo "no known good commit"; exit 1; }
    LAST=$(cat "$STATE"); BAD=$(git rev-parse HEAD)
    back_to_last_good "$LAST"
    if deps_changed "$LAST" "$BAD"; then deps; fi
    unit_file; "$SYSTEMCTL" restart nikatel
    health && echo "rolled back to $(git rev-parse --short HEAD)" || { echo "still unhealthy"; exit 1; }
    exit 0
fi

NEW=$(git rev-parse HEAD)
trap finish EXIT
step checks
WHY="the secrets check found something in the new version"
echo "== no secrets in the repo";  bash scripts/check_no_secrets.sh
if [ "${FORCE_TESTS:-0}" = 1 ]; then
    WHY="the tests of the new version failed"
    echo "== tests";                "$VENV/bin/python" -m pytest -q
else
    echo "== tests skipped: the whole suite ran on the build server before this version was published (FORCE_TESTS=1 runs it here too)"
fi
WHY="a file of the new version does not compile"
echo "== compile";                  "$VENV/bin/python" -m compileall -q nikatel
step install
if deps_changed "$PREV" "$NEW"; then WHY="the libraries of the new version could not be installed"; deps; fi
WHY="the new version does not start with the live data (an import or a migration fails)"
echo "== migrations on a copy of the live database"; PYTHONPATH="$SRC${PYTHONPATH:+:$PYTHONPATH}" "$VENV/bin/python" scripts/migrate_check.py "$STATE_DIR/data"
WHY="the unit file could not be installed"
echo "== install $(git rev-parse --short HEAD)"; unit_file
step restart
if "$SYSTEMCTL" restart nikatel && { step health; health; }; then
    git rev-parse HEAD > "$STATE"; step done; echo "DEPLOY OK $(git rev-parse --short HEAD)"
else
    echo "HEALTH CHECK FAILED"; journalctl -u nikatel -n 20 --no-pager || true
    if [ -n "$PREV" ] && git cat-file -e "$PREV^{commit}" 2>/dev/null; then
        echo "rolling back to ${PREV:0:7}"; step rollback
        back_to_last_good "$PREV"
        if deps_changed "$PREV" "$NEW"; then deps; fi
        unit_file; "$SYSTEMCTL" restart nikatel || true
        if health; then echo "rolled back: the panel runs ${PREV:0:7} again"; step failed "the new version did not answer: the old version runs again"
        else echo "STILL DOWN after the rollback: journalctl -u nikatel -n 50"; step failed "the new version did not answer and the old one did not come back either"; fi
    else
        step failed "the new version did not answer and there is no earlier version to go back to"
    fi
    exit 1
fi

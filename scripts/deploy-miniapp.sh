#!/usr/bin/env bash
# Puts the Mini App (bot/miniapp/) into the bot's web root, keeps a copy of what was there, checks the PHP.
#   bash scripts/deploy-miniapp.sh <ssh target> [bot dir]        e.g. root@bot-host /var/www/mybot
#   bash scripts/deploy-miniapp.sh --local [bot dir]              when the panel runs on the bot's server
# The bot directory holds baseInfo.php; the Mini App lives in <bot dir>/app/ and is opened from the bot's
# "Mini App" button (BOT_STATES miniAppState = on, $botUrl in baseInfo.php).
# The copy of the old files goes OUTSIDE the web root (<bot dir>/../.miniapp-prev-<name>/<time>/): inside <bot dir>/app/ it was served
# to anybody (app/.prev/app.js, app/.prev-<time>/index.html ...). An old app/.prev* folder from an earlier deploy is moved there too.
set -euo pipefail
cd "$(dirname "$0")/.."
TARGET="${1:?ssh target or --local}"
DIR="${2:-/var/www/nikabot}"
SRC="bot/miniapp"
[ -f "$SRC/api.php" ] || { echo "no $SRC/api.php"; exit 1; }

STAMP=$(date +%Y%m%d-%H%M%S)
BACKUP="$(dirname "$DIR")/.miniapp-prev-$(basename "$DIR")/$STAMP"
REMOTE_CMD="set -e; mkdir -p '$DIR/app' '$BACKUP'; chmod 700 '$(dirname "$BACKUP")'; cp '$DIR'/app/*.{php,html,js,css} '$BACKUP/' 2>/dev/null || true; for old in '$DIR'/app/.prev '$DIR'/app/.prev-*; do [ -e \"\$old\" ] && mv \"\$old\" '$BACKUP/' || true; done"
FINISH_CMD="set -e; cd '$DIR/app'; php -l api.php >/dev/null; chown -R www-data:www-data '$DIR/app' 2>/dev/null || true; chmod 644 api.php index.html app.js app.css; echo 'mini app deployed to $DIR/app ($STAMP); the old files are in $BACKUP'"

if [ "$TARGET" = "--local" ]; then
    bash -c "$REMOTE_CMD"
    cp "$SRC"/api.php "$SRC"/index.html "$SRC"/app.js "$SRC"/app.css "$DIR/app/"
    bash -c "$FINISH_CMD"
else
    ssh -o BatchMode=yes "$TARGET" "$REMOTE_CMD"
    scp -q "$SRC"/api.php "$SRC"/index.html "$SRC"/app.js "$SRC"/app.css "$TARGET:$DIR/app/"
    ssh -o BatchMode=yes "$TARGET" "$FINISH_CMD"
fi
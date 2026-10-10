#!/usr/bin/env bash
# Puts the Mini App (bot/miniapp/) into the bot's web root, keeps a copy of what was there, checks the PHP.
#   bash scripts/deploy-miniapp.sh <ssh target> [bot dir]        e.g. root@bot-host /var/www/mybot
#   bash scripts/deploy-miniapp.sh --local [bot dir]              when the panel runs on the bot's server
# The bot directory holds baseInfo.php; the Mini App lives in <bot dir>/app/ and is opened from the bot's
# "Mini App" button (BOT_STATES miniAppState = on, $botUrl in baseInfo.php).
set -euo pipefail
cd "$(dirname "$0")/.."
TARGET="${1:?ssh target or --local}"
DIR="${2:-/var/www/nikabot}"
SRC="bot/miniapp"
[ -f "$SRC/api.php" ] || { echo "no $SRC/api.php"; exit 1; }

STAMP=$(date +%Y%m%d-%H%M%S)
REMOTE_CMD="set -e; mkdir -p '$DIR/app'; [ -f '$DIR/app/api.php' ] && mkdir -p '$DIR/app/.prev' && cp '$DIR'/app/*.{php,html,js,css} '$DIR/app/.prev/' 2>/dev/null || true"
FINISH_CMD="set -e; cd '$DIR/app'; php -l api.php >/dev/null; chown -R www-data:www-data '$DIR/app' 2>/dev/null || true; chmod 644 api.php index.html app.js app.css; echo 'mini app deployed to $DIR/app ($STAMP)'"

if [ "$TARGET" = "--local" ]; then
    bash -c "$REMOTE_CMD"
    cp "$SRC"/api.php "$SRC"/index.html "$SRC"/app.js "$SRC"/app.css "$DIR/app/"
    bash -c "$FINISH_CMD"
else
    ssh -o BatchMode=yes "$TARGET" "$REMOTE_CMD"
    scp -q "$SRC"/api.php "$SRC"/index.html "$SRC"/app.js "$SRC"/app.css "$TARGET:$DIR/app/"
    ssh -o BatchMode=yes "$TARGET" "$FINISH_CMD"
fi

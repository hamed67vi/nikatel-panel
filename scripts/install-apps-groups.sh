#!/usr/bin/env bash
# The apps screen of the shop bot by system (iPhone / iOS, Android, Windows, Mac), the recommended apps first: the first screen has one button per system, a system opens its apps.
#   bash scripts/install-apps-groups.sh [--dry-run | --uninstall] <bot dir>        e.g. /var/www/bot
# Copies bot/menu/apps_groups.php next to bot.php and changes TWO places of the bot's own handler of the button "reciveApplications" in bot.php (the condition: it also answers the system buttons;
# the loop that builds the keyboard: it calls shopAppsKeyboard inside a try, and builds the old flat list when anything fails). Both places carry the marker "// [apps-groups]".
# The old bot.php is kept in $BACKUP_DIR (default /root/backups) and php -l runs on the new file BEFORE it replaces the old one; --uninstall puts the two places back byte for byte and removes apps_groups.php.
# Texts of the apps: the list needed_sofwares (title, link) - a title with (پیشنهادی) is a recommended app; see apps_groups.php.
set -euo pipefail
MODE=install
case "${1:-}" in --dry-run) MODE=dry; shift;; --uninstall) MODE=uninstall; shift;; esac
DIR="${1:?bot directory, e.g. /var/www/bot}"
F="$DIR/bot.php"
SRC="$(cd "$(dirname "$0")/.." && pwd)/bot/menu/apps_groups.php"
[ -f "$F" ] || { echo "no $F"; exit 1; }
[ -f "$SRC" ] || { echo "no $SRC"; exit 1; }
STAMP=$(date +%Y%m%d-%H%M%S)
BACKUP_DIR="${BACKUP_DIR:-/root/backups}"
NEW="$F.appsgrp.$$"
trap 'rm -f "$NEW"' EXIT

python3 - "$F" "$NEW" "$MODE" <<'PY'
import sys
src, dst, mode = sys.argv[1:4]
OLD_COND = "if($data == 'reciveApplications') {"
NEW_COND = "if($data == 'reciveApplications' || preg_match('/^reciveApplications_(ios|android|windows|mac)$/', $data)) { // [apps-groups]"
OLD_LOOP = ("    $keyboard = []; \n"
            "    while($file =  $respd->fetch_assoc()){ \n"
            "        $link = $file['link'];\n"
            "        $title = $file['title'];\n"
            "        $keyboard[] = ['text' => \"$title\", 'url' => $link];\n"
            "    }\n")
NEW_LOOP = ("    $keyboard = []; // [apps-groups]\n"
            "    try { require_once __DIR__ . '/apps_groups.php'; $keyboard = shopAppsKeyboard($respd, $data); }\n"
            "    catch (Throwable $e) { $keyboard = []; $respd->data_seek(0); while($file = $respd->fetch_assoc()){ $keyboard[] = ['text' => $file['title'], 'url' => $file['link']]; } } // [/apps-groups]\n")
s = open(src, encoding="utf-8", newline="").read()
crlf = "\r\n" in s
if crlf:
    s = s.replace("\r\n", "\n")
has = "// [apps-groups]" in s
if mode == "uninstall":
    if not has:
        print("not installed"); out = s
    else:
        if s.count(NEW_COND) != 1 or s.count(NEW_LOOP) != 1:
            print("the two marked places are not as the installer wrote them: not uninstalling", file=sys.stderr); sys.exit(3)
        out = s.replace(NEW_COND, OLD_COND, 1).replace(NEW_LOOP, OLD_LOOP, 1)
        print("the two places are back as they were")
else:
    if has:
        print("already installed"); out = s
    else:
        if s.count(OLD_COND) != 1 or s.count(OLD_LOOP) != 1:
            print(f"the handler is not as expected (condition x{s.count(OLD_COND)}, loop x{s.count(OLD_LOOP)}; want 1 and 1): not installing", file=sys.stderr); sys.exit(3)
        out = s.replace(OLD_COND, NEW_COND, 1).replace(OLD_LOOP, NEW_LOOP, 1)
        print("two places changed")
open(dst, "w", encoding="utf-8", newline="").write(out.replace("\n", "\r\n") if crlf else out)
PY

php -l "$NEW" >/dev/null || { echo "php -l failed on the new file - nothing replaced"; exit 1; }
if [ "$MODE" = install ] || [ "$MODE" = dry ]; then php -l "$SRC" >/dev/null || { echo "php -l failed on apps_groups.php"; exit 1; }; fi
if [ "$MODE" = dry ]; then echo "(dry run: both files pass php -l, nothing replaced or copied)"; exit 0; fi
if [ "$MODE" = install ]; then
    install -m 644 "$SRC" "$DIR/apps_groups.php"
    chown --reference="$F" "$DIR/apps_groups.php" 2>/dev/null || true
fi
if ! cmp -s "$F" "$NEW"; then
    mkdir -p "$BACKUP_DIR"
    cp -p "$F" "$BACKUP_DIR/bot.php.before-apps-groups-$STAMP"
    chown --reference="$F" "$NEW" 2>/dev/null || true
    chmod --reference="$F" "$NEW"
    mv "$NEW" "$F"
    echo "done: $F ($(grep -c '\[apps-groups\]' "$F") marker lines); the old file: $BACKUP_DIR/bot.php.before-apps-groups-$STAMP"
else
    echo "no change in bot.php"
fi
if [ "$MODE" = uninstall ]; then rm -f "$DIR/apps_groups.php"; fi
php -l "$F" >/dev/null && echo "bot.php passes php -l"

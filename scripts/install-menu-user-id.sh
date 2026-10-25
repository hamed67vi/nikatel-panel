#!/usr/bin/env bash
# The customer's own numeric id (monospace, a tap copies it) in the main-menu text of the shop bot: the word MENU-ID in a text becomes the id of the chat the message goes to.
#   bash scripts/install-menu-user-id.sh [--dry-run | --uninstall] <bot dir>        e.g. /var/www/bot
# Copies bot/menu/menu_user_id.php next to config.php and puts TWO marked lines (both end with "// [menu-id]") into config.php:
#   1) the include of menu_user_id.php at the top (only when the file is there);
#   2) inside the bot's own function bot() - the one place every Telegram request passes - right after the line of the premium-emoji layer: menuIdFormat() writes the id as <code>id</code> (HTML), `id` (Markdown) or plain,
#      for the parse mode of that very message (the same menu text is sent with both modes in different places of the bot); the copy of the request that the premium-emoji layer keeps for a retry is done the same way.
# A text without the word MENU-ID is not touched: nothing changes until the owner puts the word into a text of settings/values.php. Nothing old is changed or removed.
# The old file is kept in $BACKUP_DIR (default /root/backups); php -l runs on the new file BEFORE it replaces the old one; --uninstall takes the two lines out again (byte for byte) and removes menu_user_id.php.
set -euo pipefail
MODE=install
case "${1:-}" in --dry-run) MODE=dry; shift;; --uninstall) MODE=uninstall; shift;; esac
DIR="${1:?bot directory, e.g. /var/www/bot}"
C="$DIR/config.php"
SRC="$(cd "$(dirname "$0")/.." && pwd)/bot/menu/menu_user_id.php"
[ -f "$C" ] || { echo "no $C"; exit 1; }
[ -f "$SRC" ] || { echo "no $SRC"; exit 1; }
STAMP=$(date +%Y%m%d-%H%M%S)
BACKUP_DIR="${BACKUP_DIR:-/root/backups}"
NC="$C.menuid.$$"
trap 'rm -f "$NC"' EXIT

python3 - "$C" "$NC" "$MODE" <<'PY'
import sys
src, dst, mode = sys.argv[1:4]
TAG = " // [menu-id]"
PE = "    $__peBack = null; if(function_exists('peOutgoing') && is_array($datas)){ [$datas, $__peBack] = peOutgoing($datas); } // [pe-support]\n"
EDITS = [
    ("<?php\n", "if (is_file(__DIR__ . '/menu_user_id.php')) require_once __DIR__ . '/menu_user_id.php';" + TAG + "\n"),
    (PE, "    if (function_exists('menuIdFormat')) { if (is_array($datas)) $datas = menuIdFormat($datas); if (is_array($__peBack)) $__peBack = menuIdFormat($__peBack); }" + TAG + "\n"),
]
s = open(src, encoding="utf-8", newline="").read()
crlf = "\r\n" in s
if crlf:
    s = s.replace("\r\n", "\n")
has = s.count(TAG)
if mode == "uninstall":
    if has == 0:
        print("not installed")
    else:
        for anchor, ins in EDITS:
            if s.count(ins) != 1:
                print(f"the marked lines are not as the installer wrote them (x{s.count(ins)}): not uninstalling", file=sys.stderr); sys.exit(3)
        for anchor, ins in EDITS:
            s = s.replace(ins, "", 1)
        print(f"{len(EDITS)} line(s) taken out")
else:
    if has:
        if has != len(EDITS):
            print(f"{has} marked line(s) found, {len(EDITS)} expected: not touching it", file=sys.stderr); sys.exit(3)
        print("already installed")
    else:
        if not s.startswith("<?php\n"):
            print("config.php does not start with <?php + newline: not installing", file=sys.stderr); sys.exit(3)
        if s.count(PE) != 1:
            print(f"the place in bot() (the premium-emoji line) is not as expected (x{s.count(PE)}; want 1): not installing", file=sys.stderr); sys.exit(3)
        for anchor, ins in EDITS:
            s = anchor + ins + s[len(anchor):] if anchor == "<?php\n" else s.replace(anchor, anchor + ins, 1)
        print(f"{len(EDITS)} line(s) put in")
open(dst, "w", encoding="utf-8", newline="").write(s.replace("\n", "\r\n") if crlf else s)
PY

php -l "$NC" >/dev/null || { echo "php -l failed on the new config.php - nothing replaced"; exit 1; }
if [ "$MODE" = install ] || [ "$MODE" = dry ]; then php -l "$SRC" >/dev/null || { echo "php -l failed on menu_user_id.php"; exit 1; }; fi
if [ "$MODE" = dry ]; then echo "(dry run: both files pass php -l, nothing replaced or copied)"; exit 0; fi
if [ "$MODE" = install ]; then
    install -m 644 "$SRC" "$DIR/menu_user_id.php"
    chown --reference="$C" "$DIR/menu_user_id.php" 2>/dev/null || true
fi
if ! cmp -s "$C" "$NC"; then
    mkdir -p "$BACKUP_DIR"
    cp -p "$C" "$BACKUP_DIR/config.php.before-menu-id-$STAMP"
    chown --reference="$C" "$NC" 2>/dev/null || true
    chmod --reference="$C" "$NC"
    mv "$NC" "$C"
    echo "done: $C ($(grep -c '\[menu-id\]' "$C") marker line(s)); the old file: $BACKUP_DIR/config.php.before-menu-id-$STAMP"
else
    echo "no change in config.php"
fi
if [ "$MODE" = uninstall ]; then rm -f "$DIR/menu_user_id.php"; fi
php -l "$C" >/dev/null && echo "config.php passes php -l"

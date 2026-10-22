#!/usr/bin/env bash
# The /shop command of the shop bot: a message with a button that opens the Mini App (https://<bot host>/app/).
#   bash scripts/install-shop-command.sh [--dry-run | --uninstall] <bot dir>        e.g. /var/www/bot
# Puts ONE block into <bot dir>/bot.php, just before "// Pre Pay Check By ACh" - after the bot's own ban and spam checks - between the markers
# "// [shop-command]" and "// [/shop-command]". Idempotent; the old file is kept in $BACKUP_DIR (default /root/backups) before it is replaced; php -l runs on the new file BEFORE it replaces the old one;
# --uninstall takes the block out again (byte for byte what was there before). Nothing else of the bot is touched. Add the command to the list of Telegram too:
#   {"command":"shop","description":"..."} through setMyCommands (the panel's Shop bot page does it with its other commands).
set -euo pipefail
MODE=install
case "${1:-}" in --dry-run) MODE=dry; shift;; --uninstall) MODE=uninstall; shift;; esac
DIR="${1:?bot directory, e.g. /var/www/bot}"
F="$DIR/bot.php"
[ -f "$F" ] || { echo "no $F"; exit 1; }
STAMP=$(date +%Y%m%d-%H%M%S)
BACKUP_DIR="${BACKUP_DIR:-/root/backups}"
NEW="$F.shopcmd.$$"
trap 'rm -f "$NEW"' EXIT

python3 - "$F" "$NEW" "$MODE" <<'PY'
import re, sys
src, dst, mode = sys.argv[1:4]
BEGIN, END, ANCHOR = "// [shop-command]", "// [/shop-command]", "// Pre Pay Check By ACh"
BLOCK = """// [shop-command] /shop opens the Mini App: a message with a web_app button. Installed by scripts/install-shop-command.sh; delete the lines between the two markers to undo.
if (!empty($botUrl) && preg_match('~^/shop(@\\\\w+)?$~i', trim((string) ($update->message->text ?? ''))) && ($update->message->chat->type ?? 'private') == 'private') {
    sendMessage("\U0001F6CD برای باز کردن فروشگاه روی دکمهٔ زیر بزنید \U0001F447", json_encode(['inline_keyboard' => [[['text' => "\U0001F6CD فروشگاه", 'web_app' => ['url' => rtrim($botUrl, '/') . '/app/']]]]], JSON_UNESCAPED_UNICODE), "HTML");
    exit();
}
// [/shop-command]
"""
s = open(src, encoding="utf-8", newline="").read()
crlf = "\r\n" in s
if crlf:
    s = s.replace("\r\n", "\n")
has = BEGIN in s
if mode == "uninstall":
    if not has:
        print("not installed"); open(dst, "w", encoding="utf-8", newline="").write(s.replace("\n", "\r\n") if crlf else s); sys.exit(0)
    out = re.sub(re.escape(BEGIN) + r".*?" + re.escape(END) + r"\n\n", "", s, count=1, flags=re.S)
    print("removed the block")
else:
    if has:
        print("already installed"); out = s
    else:
        if s.count(ANCHOR) != 1:
            print(f'the anchor "{ANCHOR}" is there {s.count(ANCHOR)} times (want 1): not installing', file=sys.stderr); sys.exit(3)
        out = s.replace(ANCHOR, BLOCK + "\n" + ANCHOR, 1)
        print("block added before the anchor")
open(dst, "w", encoding="utf-8", newline="").write(out.replace("\n", "\r\n") if crlf else out)
PY

php -l "$NEW" >/dev/null || { echo "php -l failed on the new file - nothing replaced"; exit 1; }
if cmp -s "$F" "$NEW"; then echo "no change"; exit 0; fi
if [ "$MODE" = dry ]; then echo "(dry run: the new file passes php -l, nothing replaced)"; exit 0; fi
mkdir -p "$BACKUP_DIR"
cp -p "$F" "$BACKUP_DIR/bot.php.before-shop-command-$STAMP"
chown --reference="$F" "$NEW" 2>/dev/null || true
chmod --reference="$F" "$NEW"
mv "$NEW" "$F"
php -l "$F" >/dev/null && echo "done: $F ($(grep -c '\[shop-command\]' "$F") marker lines); the old file: $BACKUP_DIR/bot.php.before-shop-command-$STAMP"

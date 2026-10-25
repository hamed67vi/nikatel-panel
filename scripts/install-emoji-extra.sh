#!/usr/bin/env bash
# The emoji list of the shop bot's "edit buttons" panel (premium_emoji.php, peEmojiList) gets the emoji of the buttons that are written in the bot's code too (admin panel, statistics, tickets, rating, paging ...)
# and of the buttons the admin made himself, so every emoji of the bot can be given a premium icon.
#   bash scripts/install-emoji-extra.sh [--dry-run | --uninstall] <bot dir>        e.g. /var/www/bot
# Copies bot/menu/emoji_extra.php next to premium_emoji.php and puts TWO marked lines (both end with "// [emoji-extra]") into premium_emoji.php:
#   1) the include of emoji_extra.php at the top (only when the file is there);
#   2) in peEmojiList(), right after the line that reads the labels of $buttonValues: the extra labels go through the same $add().
# Only the LIST of emoji changes: no button text, no keyboard. Nothing old is changed or removed; the line is behind function_exists().
# The old file is kept in $BACKUP_DIR (default /root/backups); php -l runs on the new file BEFORE it replaces the old one; --uninstall takes the two lines out again (byte for byte) and removes emoji_extra.php.
set -euo pipefail
MODE=install
case "${1:-}" in --dry-run) MODE=dry; shift;; --uninstall) MODE=uninstall; shift;; esac
DIR="${1:?bot directory, e.g. /var/www/bot}"
P="$DIR/premium_emoji.php"
SRC="$(cd "$(dirname "$0")/.." && pwd)/bot/menu/emoji_extra.php"
[ -f "$P" ] || { echo "no $P"; exit 1; }
[ -f "$SRC" ] || { echo "no $SRC"; exit 1; }
STAMP=$(date +%Y%m%d-%H%M%S)
BACKUP_DIR="${BACKUP_DIR:-/root/backups}"
NP="$P.emojiextra.$$"
trap 'rm -f "$NP"' EXIT

python3 - "$P" "$NP" "$MODE" <<'PY'
import sys
src, dst, mode = sys.argv[1:4]
TAG = " // [emoji-extra]"
LOOP = "    if (is_array($buttonValues)) foreach ($buttonValues as $v) if (is_string($v) && $v !== '') $add($v, false);\n"
EDITS = [
    ("<?php\n", "if (is_file(__DIR__ . '/emoji_extra.php')) require_once __DIR__ . '/emoji_extra.php';" + TAG + "\n"),
    (LOOP, "    if (function_exists('peExtraLabels')) foreach (peExtraLabels() as $v) if (is_string($v) && $v !== '') $add($v, false);" + TAG + "\n"),
]
s = open(src, encoding="utf-8", newline="").read()
crlf = "\r\n" in s
if crlf:
    s = s.replace("\r\n", "\n")
present = [e for e in EDITS if s.count(e[1]) == 1]
missing = [e for e in EDITS if s.count(e[1]) == 0]
if len(present) + len(missing) != len(EDITS) or s.count(TAG) != len(present):
    print(f"the marked lines are not as the installer wrote them ({s.count(TAG)} marked, {len(present)} known): not touching it", file=sys.stderr); sys.exit(3)
if mode == "uninstall":
    for anchor, ins in present:
        s = s.replace(ins, "", 1)
    print(f"{len(present)} line(s) taken out" if present else "not installed")
else:
    if missing:
        if not s.startswith("<?php\n"):
            print("premium_emoji.php does not start with <?php + newline: not installing", file=sys.stderr); sys.exit(3)
        for anchor, ins in missing:
            if anchor != "<?php\n" and s.count(anchor) != 1:
                print(f"the place for  {anchor.strip()[:70]}  is not as expected (x{s.count(anchor)}; want 1): not installing", file=sys.stderr); sys.exit(3)
        for anchor, ins in missing:
            s = anchor + ins + s[len(anchor):] if anchor == "<?php\n" else s.replace(anchor, anchor + ins, 1)
        print(f"{len(missing)} line(s) put in" + (f", {len(present)} already there" if present else ""))
    else:
        print("already installed")
open(dst, "w", encoding="utf-8", newline="").write(s.replace("\n", "\r\n") if crlf else s)
PY

php -l "$NP" >/dev/null || { echo "php -l failed on the new premium_emoji.php - nothing replaced"; exit 1; }
if [ "$MODE" = install ] || [ "$MODE" = dry ]; then php -l "$SRC" >/dev/null || { echo "php -l failed on emoji_extra.php"; exit 1; }; fi
if [ "$MODE" = dry ]; then echo "(dry run: both files pass php -l, nothing replaced or copied)"; exit 0; fi
if [ "$MODE" = install ]; then
    install -m 644 "$SRC" "$DIR/emoji_extra.php"
    chown --reference="$P" "$DIR/emoji_extra.php" 2>/dev/null || true
fi
if ! cmp -s "$P" "$NP"; then
    mkdir -p "$BACKUP_DIR"
    cp -p "$P" "$BACKUP_DIR/premium_emoji.php.before-emoji-extra-$STAMP"
    chown --reference="$P" "$NP" 2>/dev/null || true
    chmod --reference="$P" "$NP"
    mv "$NP" "$P"
    echo "done: $P ($(grep -c '\[emoji-extra\]' "$P") marker line(s)); the old file: $BACKUP_DIR/premium_emoji.php.before-emoji-extra-$STAMP"
else
    echo "no change in premium_emoji.php"
fi
if [ "$MODE" = uninstall ]; then rm -f "$DIR/emoji_extra.php"; fi
php -l "$P" >/dev/null && echo "premium_emoji.php passes php -l"
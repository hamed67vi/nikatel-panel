#!/usr/bin/env bash
# The helper subscription link shows in the Mini App only (the owner, 2026-10-27, translated: "turn its display off in the bot - let it be active only in this Mini App").
#   bash scripts/install-helper-link.sh [--dry-run | --uninstall] <bot dir>        e.g. /var/www/bot
# Copies bot/menu/helper_link.php next to config.php and puts two marked lines (both end with "// [helper-link]") into config.php:
#   1) the include of helper_link.php at the top (only when the file is there);  2) inside bot() - the one place every Telegram request passes - right BEFORE the premium-emoji line: hlOutgoing() takes the helper-link block
#      and the sentences that send the customer to it out of the text / caption of the message about to be sent.
# BOT_STATES helperLink = "mini" (also when unset: Mini App only), "both" (as before), "off" (nobody; the Mini App's api.php reads the same switch). Every hook asks function_exists() first, so a bot without helper_link.php behaves
# as before. Nothing else of the old code is changed.
# The old file is kept in $BACKUP_DIR (default /root/backups); php -l runs on the new file BEFORE it replaces the old one; --uninstall takes the two lines out again (byte for byte) and removes helper_link.php.
set -euo pipefail
MODE=install
case "${1:-}" in --dry-run) MODE=dry; shift;; --uninstall) MODE=uninstall; shift;; esac
DIR="${1:?bot directory, e.g. /var/www/bot}"
B="$DIR/config.php"
SRC="$(cd "$(dirname "$0")/.." && pwd)/bot/menu/helper_link.php"
[ -f "$B" ] || { echo "no $B"; exit 1; }
[ -f "$SRC" ] || { echo "no $SRC"; exit 1; }
STAMP=$(date +%Y%m%d-%H%M%S)
BACKUP_DIR="${BACKUP_DIR:-/root/backups}"
NB="$B.helperlink.$$"
trap 'rm -f "$NB"' EXIT

python3 - "$B" "$NB" "$MODE" <<'PY'
import sys
src, dst, mode = sys.argv[1:4]
TAG = " // [helper-link]"
INC = "if (is_file(__DIR__ . '/helper_link.php')) require_once __DIR__ . '/helper_link.php';" + TAG + "\n"
OUT = "    if (function_exists('hlOutgoing') && is_array($datas)) $datas = hlOutgoing($datas);" + TAG + "\n"
PE = "    $__peBack = null; if(function_exists('peOutgoing') && is_array($datas)){ [$datas, $__peBack] = peOutgoing($datas); } // [pe-support]\n"
EDITS = [("<?php\n", INC, "after"), (PE, OUT, "before")]
s = open(src, encoding="utf-8", newline="").read()
crlf = "\r\n" in s
if crlf:
    s = s.replace("\r\n", "\n")
has = s.count(TAG)
if mode == "uninstall":
    if has == 0:
        print("not installed")
    else:
        for anchor, ins, how in EDITS:
            if s.count(ins) != 1:
                print(f"the marked lines are not as the installer wrote them (x{s.count(ins)}): not uninstalling", file=sys.stderr); sys.exit(3)
        for anchor, ins, how in EDITS:
            s = s.replace(ins, "", 1)
        print(f"{len(EDITS)} line(s) taken out")
else:
    if has:
        if has != len(EDITS) or any(s.count(ins) != 1 for _, ins, _ in EDITS):
            print(f"{has} marked line(s) found, {len(EDITS)} expected as the installer writes them: not touching it", file=sys.stderr); sys.exit(3)
        print("already installed")
    else:
        if not s.startswith("<?php\n"):
            print("config.php does not start with <?php + newline: not installing", file=sys.stderr); sys.exit(3)
        for anchor, ins, how in EDITS:
            if anchor != "<?php\n" and s.count(anchor) != 1:
                print(f"the place for a line is not as expected (x{s.count(anchor)}; want 1): {anchor.strip()[:70]!r} - not installing", file=sys.stderr); sys.exit(3)
        for anchor, ins, how in EDITS:
            if anchor == "<?php\n":
                s = anchor + ins + s[len(anchor):]
            else:
                s = s.replace(anchor, (anchor + ins) if how == "after" else (ins + anchor), 1)
        print(f"{len(EDITS)} line(s) put in")
open(dst, "w", encoding="utf-8", newline="").write(s.replace("\n", "\r\n") if crlf else s)
PY

php -l "$NB" >/dev/null || { echo "php -l failed on the new config.php - nothing replaced"; exit 1; }
if [ "$MODE" != uninstall ]; then php -l "$SRC" >/dev/null || { echo "php -l failed on helper_link.php"; exit 1; }; fi
if [ "$MODE" = dry ]; then echo "(dry run: both files pass php -l, nothing replaced or copied)"; exit 0; fi
if [ "$MODE" = install ]; then      # (the module first: the hook asks function_exists() anyway)
    install -m 644 "$SRC" "$DIR/helper_link.php"
    chown --reference="$B" "$DIR/helper_link.php" 2>/dev/null || true
fi
if ! cmp -s "$B" "$NB"; then
    mkdir -p "$BACKUP_DIR"
    cp -p "$B" "$BACKUP_DIR/config.php.before-helper-link-$STAMP"
    chown --reference="$B" "$NB" 2>/dev/null || true
    chmod --reference="$B" "$NB"
    mv "$NB" "$B"
    echo "done: $B ($(grep -c '\[helper-link\]' "$B") marker line(s)); the old file: $BACKUP_DIR/config.php.before-helper-link-$STAMP"
else
    echo "no change in config.php"
fi
if [ "$MODE" = uninstall ]; then rm -f "$DIR/helper_link.php"; fi
php -l "$B" >/dev/null && echo "config.php passes php -l"

#!/usr/bin/env bash
# A short message to the agent when the admin raises his discount (the bot's own "editAgentDiscount" flow).
#   bash scripts/install-agent-discount-notice.sh [--dry-run | --uninstall] <bot dir>        e.g. /var/www/bot
# Copies bot/menu/agent_discount_notice.php next to bot.php and puts TWO marked lines (both end with "// [agent-notice]") into bot.php:
#   1) the include of agent_discount_notice.php at the top (only when the file is there);
#   2) right after the line that shows the agent's discount screen in the handler that saves a new discount: agDiscountNotice(...) - when the new discount is higher than the old one, the agent gets one short message.
# Nothing of the old code is changed or removed; the new line is behind function_exists() and the helper swallows every failure. BOT_STATES agentDiscountMsg = "off" switches the message off.
# The old file is kept in $BACKUP_DIR (default /root/backups); php -l runs on the new file BEFORE it replaces the old one; --uninstall takes the two lines out again (byte for byte) and removes agent_discount_notice.php.
set -euo pipefail
MODE=install
case "${1:-}" in --dry-run) MODE=dry; shift;; --uninstall) MODE=uninstall; shift;; esac
DIR="${1:?bot directory, e.g. /var/www/bot}"
B="$DIR/bot.php"
SRC="$(cd "$(dirname "$0")/.." && pwd)/bot/menu/agent_discount_notice.php"
[ -f "$B" ] || { echo "no $B"; exit 1; }
[ -f "$SRC" ] || { echo "no $SRC"; exit 1; }
STAMP=$(date +%Y%m%d-%H%M%S)
BACKUP_DIR="${BACKUP_DIR:-/root/backups}"
NB="$B.agentnotice.$$"
trap 'rm -f "$NB"' EXIT

python3 - "$B" "$NB" "$MODE" <<'PY'
import sys
src, dst, mode = sys.argv[1:4]
TAG = " // [agent-notice]"
EDITS = [
    ("<?php\n", "if (is_file(__DIR__ . '/agent_discount_notice.php')) require_once __DIR__ . '/agent_discount_notice.php';" + TAG + "\n", "after"),
    ("        sendMessage(str_replace(\"AGENT-NAME\", $userName, $mainValues['agent_discount_settings']), getAgentDiscounts($match[2]));\n",
     "        if(function_exists('agDiscountNotice')) agDiscountNotice($match[1], $match[2], $match[3], $info['discount_percent'] ?? '', $discountInfo);" + TAG + "\n", "after"),
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
        for anchor, ins, how in EDITS:
            if s.count(ins) != 1:
                print(f"the marked lines are not as the installer wrote them (x{s.count(ins)}): not uninstalling", file=sys.stderr); sys.exit(3)
        for anchor, ins, how in EDITS:
            s = s.replace(ins, "", 1)
        print(f"{len(EDITS)} line(s) taken out")
else:
    if has:
        if has != len(EDITS):
            print(f"{has} marked line(s) found, {len(EDITS)} expected: not touching it", file=sys.stderr); sys.exit(3)
        print("already installed")
    else:
        if not s.startswith("<?php\n"):
            print("bot.php does not start with <?php + newline: not installing", file=sys.stderr); sys.exit(3)
        for anchor, ins, how in EDITS:
            if anchor != "<?php\n" and s.count(anchor) != 1:
                print(f"the place for  {anchor.strip()[:70]}  is not as expected (x{s.count(anchor)}; want 1): not installing", file=sys.stderr); sys.exit(3)
        for anchor, ins, how in EDITS:
            s = anchor + ins + s[len(anchor):] if anchor == "<?php\n" else s.replace(anchor, anchor + ins, 1)
        print(f"{len(EDITS)} line(s) put in")
open(dst, "w", encoding="utf-8", newline="").write(s.replace("\n", "\r\n") if crlf else s)
PY

php -l "$NB" >/dev/null || { echo "php -l failed on the new bot.php - nothing replaced"; exit 1; }
if [ "$MODE" = install ] || [ "$MODE" = dry ]; then php -l "$SRC" >/dev/null || { echo "php -l failed on agent_discount_notice.php"; exit 1; }; fi
if [ "$MODE" = dry ]; then echo "(dry run: both files pass php -l, nothing replaced or copied)"; exit 0; fi
if [ "$MODE" = install ]; then
    install -m 644 "$SRC" "$DIR/agent_discount_notice.php"
    chown --reference="$B" "$DIR/agent_discount_notice.php" 2>/dev/null || true
fi
if ! cmp -s "$B" "$NB"; then
    mkdir -p "$BACKUP_DIR"
    cp -p "$B" "$BACKUP_DIR/bot.php.before-agent-notice-$STAMP"
    chown --reference="$B" "$NB" 2>/dev/null || true
    chmod --reference="$B" "$NB"
    mv "$NB" "$B"
    echo "done: $B ($(grep -c '\[agent-notice\]' "$B") marker line(s)); the old file: $BACKUP_DIR/bot.php.before-agent-notice-$STAMP"
else
    echo "no change in bot.php"
fi
if [ "$MODE" = uninstall ]; then rm -f "$DIR/agent_discount_notice.php"; fi
php -l "$B" >/dev/null && echo "bot.php passes php -l"
#!/usr/bin/env bash
# A customer with an ACTIVE AGENCY PLAN takes no discount code in the shop bot (the owner, 2026-10-26, translated: "whoever has an active agency plan must not accept a discount code any more").
#   bash scripts/install-agent-no-code.sh [--dry-run | --uninstall] <bot dir>        e.g. /var/www/bot
# Puts ONE marked line (it ends with "// [no-agent-code]") into config.php, inside the bot's own function applyDiscountToPay(), right after the line that finds out whether the buyer is an agent
# ($isAgent): for an agent it tells him in one sentence that he has the agency discount and returns the same "refused" answer the function gives for any other code that does not fit -
# BEFORE the code is used up (the use of a code is taken at the end of the function), so a refused agent costs the code nothing.
# The Mini App has the same rule (api.php discountApply). Nothing of the old code is changed or removed.
# The old file is kept in $BACKUP_DIR (default /root/backups); php -l runs on the new file BEFORE it replaces the old one; --uninstall takes the line out again (byte for byte).
set -euo pipefail
MODE=install
case "${1:-}" in --dry-run) MODE=dry; shift;; --uninstall) MODE=uninstall; shift;; esac
DIR="${1:?bot directory, e.g. /var/www/bot}"
C="$DIR/config.php"
[ -f "$C" ] || { echo "no $C"; exit 1; }
STAMP=$(date +%Y%m%d-%H%M%S)
BACKUP_DIR="${BACKUP_DIR:-/root/backups}"
NC="$C.nocode.$$"
trap 'rm -f "$NC"' EXIT

python3 - "$C" "$NC" "$MODE" <<'PY'
import sys
src, dst, mode = sys.argv[1:4]
TAG = " // [no-agent-code]"
ANCHOR = "    $isAgent = ($userRow && isset($userRow['is_agent'])) ? (int)$userRow['is_agent'] : 0;\n"
LINE = ("    if ($isAgent == 1) { sendMessage('کد تخفیف برای همکاران قابل استفاده نیست؛ شما از تخفیف همکاری استفاده می‌کنید.'); "
        "return ['success' => false, 'price' => $price, 'hash_id' => $hash_id, 'discount_value' => 0, 'discount_text' => '', 'payInfo' => $payInfo]; }" + TAG + "\n")
s = open(src, encoding="utf-8", newline="").read()
crlf = "\r\n" in s
if crlf:
    s = s.replace("\r\n", "\n")
has = s.count(TAG)
if mode == "uninstall":
    if has == 0:
        print("not installed")
    elif s.count(LINE) != 1:
        print(f"the marked line is not as the installer wrote it (x{s.count(LINE)}): not uninstalling", file=sys.stderr); sys.exit(3)
    else:
        s = s.replace(LINE, "", 1)
        print("1 line taken out")
else:
    if has:
        if has != 1 or s.count(LINE) != 1:
            print(f"{has} marked line(s) found that are not the installer's: not touching it", file=sys.stderr); sys.exit(3)
        print("already installed")
    else:
        if s.count(ANCHOR) != 1:
            print(f"the place in applyDiscountToPay() is not as expected (x{s.count(ANCHOR)}; want 1): not installing", file=sys.stderr); sys.exit(3)
        s = s.replace(ANCHOR, ANCHOR + LINE, 1)
        print("1 line put in")
open(dst, "w", encoding="utf-8", newline="").write(s.replace("\n", "\r\n") if crlf else s)
PY

php -l "$NC" >/dev/null || { echo "php -l failed on the new config.php - nothing replaced"; exit 1; }
if [ "$MODE" = dry ]; then echo "(dry run: the new config.php passes php -l, nothing replaced)"; exit 0; fi
if ! cmp -s "$C" "$NC"; then
    mkdir -p "$BACKUP_DIR"
    cp -p "$C" "$BACKUP_DIR/config.php.before-no-agent-code-$STAMP"
    chown --reference="$C" "$NC" 2>/dev/null || true
    chmod --reference="$C" "$NC"
    mv "$NC" "$C"
    echo "done: $C ($(grep -c '\[no-agent-code\]' "$C") marker line(s)); the old file: $BACKUP_DIR/config.php.before-no-agent-code-$STAMP"
else
    echo "no change in config.php"
fi
php -l "$C" >/dev/null && echo "config.php passes php -l"

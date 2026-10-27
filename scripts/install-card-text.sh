#!/usr/bin/env bash
# The card-to-card screen and the "currency payment" button of the shop bot (the owner, 2026-10-27, translated: "in the card-to-card part show only the star line; an old customer gets my card number below it,
# anyone else gets the support id" / "add the currency payment under card-to-card" / "delete the red outband line entirely").
#   bash scripts/install-card-text.sh [--dry-run | --uninstall] <bot dir>        e.g. /var/www/bot
# Copies bot/menu/card_text.php next to config.php and puts four marked lines (all end with "// [card-text]") into the bot:
#   bot.php          1) the include of card_text.php at the top (only when the file is there);  2) ctcStrip() right after check(): the tap on the currency button goes on as a tap on the card button
#   config.php       3) inside bot() - the one place every Telegram request passes - right BEFORE the premium-emoji line: ctcOutgoing() puts the currency button under every card-to-card button
#   buy_helpers.php  4) first line of buildCartToCartInfoMessage(): ctcMessage() gives the new card text / the tether text, or null = the old function goes on
# and takes the owner's red line ("before paying tether for outband ...") out of settings/values.php (a text of his: it is deleted, --uninstall does NOT bring it back; the backup keeps it).
# Every hook asks function_exists() first, so a bot without card_text.php (or with BOT_STATES ctcText = off / ctcCurrency = off) behaves as before. Nothing else of the old code is changed.
# The old files are kept in $BACKUP_DIR (default /root/backups); php -l runs on every new file BEFORE any file is replaced; --uninstall takes the four lines out again (byte for byte) and removes card_text.php.
set -euo pipefail
MODE=install
case "${1:-}" in --dry-run) MODE=dry; shift;; --uninstall) MODE=uninstall; shift;; esac
DIR="${1:?bot directory, e.g. /var/www/bot}"
SRC="$(cd "$(dirname "$0")/.." && pwd)/bot/menu/card_text.php"
for f in bot.php config.php buy_helpers.php settings/values.php; do [ -f "$DIR/$f" ] || { echo "no $DIR/$f"; exit 1; }; done
[ -f "$SRC" ] || { echo "no $SRC"; exit 1; }
STAMP=$(date +%Y%m%d-%H%M%S)
BACKUP_DIR="${BACKUP_DIR:-/root/backups}"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

python3 - "$DIR" "$WORK" "$MODE" <<'PY'
import os, sys
d, work, mode = sys.argv[1:4]
TAG = " // [card-text]"
INC = "if (is_file(__DIR__ . '/card_text.php')) require_once __DIR__ . '/card_text.php';" + TAG + "\n"
STRIP = "if (function_exists('ctcStrip')) ctcStrip();" + TAG + "\n"
OUT = "    if (function_exists('ctcOutgoing') && is_array($datas)) $datas = ctcOutgoing($datas);" + TAG + "\n"
HELP = "    if (function_exists('ctcMessage') && ($__ctc = ctcMessage($paymentKeys, $mainValues, $priceToman, $userId)) !== null) return $__ctc;" + TAG + "\n"
PE = "    $__peBack = null; if(function_exists('peOutgoing') && is_array($datas)){ [$datas, $__peBack] = peOutgoing($datas); } // [pe-support]\n"
SIG = "function buildCartToCartInfoMessage(array $paymentKeys, array $mainValues, int $priceToman, int $userId = 0): string {\n"
CHECK = "\ncheck();\n"
# (file, [(anchor, inserted line, "after" | "before")]) - the order is the order of the lines in the file
FILES = {
    "bot.php": [("<?php\n", INC, "after"), (CHECK, STRIP, "after")],
    "config.php": [(PE, OUT, "before")],
    "buy_helpers.php": [(SIG, HELP, "after")],
}
RED = "واریز تتر برای اوتباند"      # the owner's red line: the word that finds it

def load(path):
    s = open(path, encoding="utf-8", newline="").read()
    crlf = "\r\n" in s
    return (s.replace("\r\n", "\n") if crlf else s), crlf

def save(path, s, crlf):
    open(path, "w", encoding="utf-8", newline="").write(s.replace("\n", "\r\n") if crlf else s)

changed = []
for name, edits in FILES.items():
    s, crlf = load(f"{d}/{name}")
    has = s.count(TAG)
    if mode == "uninstall":
        if has == 0:
            print(f"{name}: not installed"); continue
        for anchor, ins, how in edits:
            if s.count(ins) != 1:
                print(f"{name}: the marked line is not as the installer wrote it (x{s.count(ins)}): not uninstalling", file=sys.stderr); sys.exit(3)
        for anchor, ins, how in edits:
            s = s.replace(ins, "", 1)
        print(f"{name}: {len(edits)} line(s) taken out")
    else:
        if has:
            if has != len(edits) or any(s.count(ins) != 1 for _, ins, _ in edits):
                print(f"{name}: {has} marked line(s) found, {len(edits)} expected as the installer writes them: not touching it", file=sys.stderr); sys.exit(3)
            print(f"{name}: already installed"); continue
        if not s.startswith("<?php\n"):
            print(f"{name} does not start with <?php + newline: not installing", file=sys.stderr); sys.exit(3)
        for anchor, ins, how in edits:
            if anchor != "<?php\n" and s.count(anchor) != 1:
                print(f"{name}: the place for a line is not as expected (x{s.count(anchor)}; want 1): {anchor.strip()[:70]!r} - not installing", file=sys.stderr); sys.exit(3)
        for anchor, ins, how in edits:
            if anchor == "<?php\n":
                s = anchor + ins + s[len(anchor):]
            else:
                s = s.replace(anchor, (anchor + ins) if how == "after" else (ins + anchor), 1)
        print(f"{name}: {len(edits)} line(s) put in")
    save(f"{work}/{name}", s, crlf)
    changed.append(name)

# the owner's red line is deleted from his texts (install only; a line of values.php that holds the word and starts with the red circle)
if mode != "uninstall":
    s, crlf = load(f"{d}/settings/values.php")
    lines = s.split("\n")
    keep = [ln for ln in lines if not (RED in ln and ln.lstrip().startswith("\U0001F534"))]
    gone = len(lines) - len(keep)
    if gone > 3:
        print(f"settings/values.php: {gone} lines hold the red outband text, 3 expected at most: not touching it", file=sys.stderr); sys.exit(3)
    if gone:
        os.makedirs(f"{work}/settings", exist_ok=True)
        save(f"{work}/settings/values.php", "\n".join(keep), crlf)
        changed.append("settings/values.php")
    print(f"settings/values.php: {gone} red outband line(s) deleted" if gone else "settings/values.php: the red outband line is not there (already deleted)")
open(f"{work}/changed.txt", "w").write("\n".join(changed))
PY

CHANGED="$(cat "$WORK/changed.txt")"
for f in $CHANGED; do php -l "$WORK/$f" >/dev/null || { echo "php -l failed on the new $f - nothing replaced"; exit 1; }; done
if [ "$MODE" != uninstall ]; then php -l "$SRC" >/dev/null || { echo "php -l failed on card_text.php"; exit 1; }; fi
if [ "$MODE" = dry ]; then echo "(dry run: every new file passes php -l, nothing replaced or copied)"; exit 0; fi

if [ "$MODE" = install ]; then      # (the module first: every hook asks function_exists() anyway)
    install -m 644 "$SRC" "$DIR/card_text.php"
    chown --reference="$DIR/config.php" "$DIR/card_text.php" 2>/dev/null || true
fi
for f in $CHANGED; do
    if ! cmp -s "$DIR/$f" "$WORK/$f"; then
        mkdir -p "$BACKUP_DIR"
        cp -p "$DIR/$f" "$BACKUP_DIR/$(basename "$f").before-card-text-$STAMP"
        chown --reference="$DIR/$f" "$WORK/$f" 2>/dev/null || true
        chmod --reference="$DIR/$f" "$WORK/$f"
        mv "$WORK/$f" "$DIR/$f"
        echo "done: $f; the old file: $BACKUP_DIR/$(basename "$f").before-card-text-$STAMP"
    fi
done
if [ "$MODE" = uninstall ]; then rm -f "$DIR/card_text.php"; fi
for f in bot.php config.php buy_helpers.php settings/values.php; do php -l "$DIR/$f" >/dev/null || { echo "php -l FAILED on $f"; exit 1; }; done
echo "all four files pass php -l; marked lines: $(cat "$DIR"/bot.php "$DIR"/config.php "$DIR"/buy_helpers.php | grep -c '\[card-text\]')"

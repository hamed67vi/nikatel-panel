#!/usr/bin/env bash
# The "show configs" button of the shop bot (the owner, 2026-10-27, translated: "when the subscription is sent to the user in the bot, send only the subscription link - but add a 'show configs' button to it").
#   bash scripts/install-configs-button.sh [--dry-run | --uninstall] <bot dir>        e.g. /var/www/bot
# Copies bot/menu/configs_button.php next to config.php and puts five marked lines (all end with "// [configs-button]") into the bot:
#   bot.php          1) the include of configs_button.php at the top (only when the file is there);  2) right after check(): cfgBtnHandle() answers a tap on the button (and exits)
#   config.php       3) in getOrderDetailKeys() (the service page of "my subscriptions"): cfgBtnFlat() adds the button to the buttons of the page
#   buy_helpers.php  4) in buildLinksAndSendQr() (the "new subscription" message after a purchase): cfgBtnQr() makes its QR code the subscription link, never a config;  5) cfgBtnKeys() adds the button to its keyboard
# Nothing is drawn unless BOT_STATES configLinkState = "off" (the configs are in the text otherwise); BOT_STATES cfgButton = "off" switches the button off. Every hook asks function_exists() first, so a bot without
# configs_button.php behaves as before. Nothing else of the old code is changed.
# The old files are kept in $BACKUP_DIR (default /root/backups); php -l runs on every new file BEFORE any file is replaced; --uninstall takes the five lines out again (byte for byte) and removes configs_button.php.
set -euo pipefail
MODE=install
case "${1:-}" in --dry-run) MODE=dry; shift;; --uninstall) MODE=uninstall; shift;; esac
DIR="${1:?bot directory, e.g. /var/www/bot}"
SRC="$(cd "$(dirname "$0")/.." && pwd)/bot/menu/configs_button.php"
for f in bot.php config.php buy_helpers.php; do [ -f "$DIR/$f" ] || { echo "no $DIR/$f"; exit 1; }; done
[ -f "$SRC" ] || { echo "no $SRC"; exit 1; }
STAMP=$(date +%Y%m%d-%H%M%S)
BACKUP_DIR="${BACKUP_DIR:-/root/backups}"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

python3 - "$DIR" "$WORK" "$MODE" <<'PY'
import os, sys
d, work, mode = sys.argv[1:4]
TAG = " // [configs-button]"
INC = "if (is_file(__DIR__ . '/configs_button.php')) require_once __DIR__ . '/configs_button.php';" + TAG + "\n"
HANDLE = "if (function_exists('cfgBtnHandle') && cfgBtnHandle()) exit;" + TAG + "\n"
FLAT = "        if (function_exists('cfgBtnFlat')) $flatButtons = cfgBtnFlat($flatButtons, $id, $botState);" + TAG + "\n"
QR = "        if (function_exists('cfgBtnQr')) $qrLink = cfgBtnQr($qrLink, $mainSubLink, $botState);" + TAG + "\n"
KEYS = "        if (function_exists('cfgBtnKeys')) $keys = cfgBtnKeys($keys, $token, $botState);" + TAG + "\n"
CHECK = "\ncheck();\n"
QRSUB = "            $flatButtons[] = ['text' => $buttonValues['qr_sub'], 'callback_data' => \"showQrSub{$id}\"];\n"
QRLINE = "        $qrLink = ($linkCount <= 3 && !empty($link)) ? $link : $mainSubLink;\n"
SENDTO = "        if (!$sendToUser) {\n"
# (file, [(anchor, inserted line, "after" | "before")]) - the order is the order of the lines in the file
FILES = {
    "bot.php": [("<?php\n", INC, "after"), (CHECK, HANDLE, "after")],
    "config.php": [(QRSUB, FLAT, "after")],
    "buy_helpers.php": [(QRLINE, QR, "after"), (SENDTO, KEYS, "before")],
}

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
open(f"{work}/changed.txt", "w").write("\n".join(changed))
PY

CHANGED="$(cat "$WORK/changed.txt")"
for f in $CHANGED; do php -l "$WORK/$f" >/dev/null || { echo "php -l failed on the new $f - nothing replaced"; exit 1; }; done
if [ "$MODE" != uninstall ]; then php -l "$SRC" >/dev/null || { echo "php -l failed on configs_button.php"; exit 1; }; fi
if [ "$MODE" = dry ]; then echo "(dry run: every new file passes php -l, nothing replaced or copied)"; exit 0; fi

if [ "$MODE" = install ]; then      # (the module first: every hook asks function_exists() anyway)
    install -m 644 "$SRC" "$DIR/configs_button.php"
    chown --reference="$DIR/config.php" "$DIR/configs_button.php" 2>/dev/null || true
fi
for f in $CHANGED; do
    if ! cmp -s "$DIR/$f" "$WORK/$f"; then
        mkdir -p "$BACKUP_DIR"
        cp -p "$DIR/$f" "$BACKUP_DIR/$(basename "$f").before-configs-button-$STAMP"
        chown --reference="$DIR/$f" "$WORK/$f" 2>/dev/null || true
        chmod --reference="$DIR/$f" "$WORK/$f"
        mv "$WORK/$f" "$DIR/$f"
        echo "done: $f; the old file: $BACKUP_DIR/$(basename "$f").before-configs-button-$STAMP"
    fi
done
if [ "$MODE" = uninstall ]; then rm -f "$DIR/configs_button.php"; fi
for f in bot.php config.php buy_helpers.php; do php -l "$DIR/$f" >/dev/null || { echo "php -l FAILED on $f"; exit 1; }; done
echo "all three files pass php -l; marked lines: $(cat "$DIR"/bot.php "$DIR"/config.php "$DIR"/buy_helpers.php | grep -c '\[configs-button\]')"
